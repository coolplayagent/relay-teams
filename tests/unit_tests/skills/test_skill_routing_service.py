# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.retrieval import RetrievalService, SqliteFts5RetrievalStore
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.sessions.runs.run_models import RuntimePromptConversationContext
from relay_teams.skills import (
    SkillIndexService,
    SkillRegistry,
    SkillRoutingContext,
    SkillRoutingFallbackReason,
    SkillRoutingMode,
    SkillRuntimeService,
    SkillsDirectory,
    build_skill_routing_query_text,
)
from relay_teams.skills.skill_models import SkillInstructionEntry


def test_skill_index_documents_include_instruction_script_and_resource_summaries(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills" / "time"
    resources_dir = skill_dir / "resources"
    scripts_dir = skill_dir / "scripts"
    resources_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    (resources_dir / "usage.txt").write_text("Use UTC.\n", encoding="utf-8")
    (scripts_dir / "lint.py").write_text("print('lint')\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: time\n"
        "description: Normalize timestamps to UTC and convert timezones.\n"
        "resources:\n"
        "  usage.txt:\n"
        "    description: Timezone usage notes.\n"
        "    path: resources/usage.txt\n"
        "---\n"
        "Use UTC for all timestamps.\n"
        "- lint: Validate timezone conversions (scripts/lint.py)\n",
        encoding="utf-8",
    )
    registry = SkillRegistry(directory=SkillsDirectory(base_dir=tmp_path / "skills"))
    index_service = SkillIndexService(
        retrieval_service=_build_retrieval_service(tmp_path)
    )

    documents = index_service.build_documents(skill_registry=registry)

    assert len(documents) == 1
    document = documents[0]
    assert document.document_id == "time"
    assert "Use UTC for all timestamps." in document.body
    assert "Scripts\n- lint: Validate timezone conversions" in document.body
    assert "Resources\n- usage.txt: Timezone usage notes." in document.body
    assert "time" in document.keywords
    assert "usage" in document.keywords
    assert "builtin" not in document.keywords


def test_build_skill_routing_query_text_uses_stable_context_fields() -> None:
    query_text = build_skill_routing_query_text(
        SkillRoutingContext(
            objective="Normalize all timestamps to UTC.",
            role_name="Spec Coder",
            role_description="Implements requested changes.",
            shared_state_snapshot=(("ticket", "BUG-123"), ("priority", "high")),
            conversation_context=RuntimePromptConversationContext(
                source_provider="feishu",
                source_kind="im",
                feishu_chat_type="group",
                im_force_direct_send=True,
            ),
            orchestration_prompt="Prioritize timezone correctness before formatting.",
        )
    )

    assert "Objective: Normalize all timestamps to UTC." in query_text
    assert "Role: Spec Coder - Implements requested changes." in query_text
    assert "- ticket: BUG-123" in query_text
    assert "- priority: high" in query_text
    assert "- source_provider: feishu" in query_text
    assert "- feishu_chat_type: group" in query_text
    assert (
        "Orchestration Prompt: Prioritize timezone correctness before formatting."
        in query_text
    )
    assert "Runtime Environment Information" not in query_text
    assert "AGENTS.md" not in query_text


def test_skill_runtime_service_passthrough_when_authorized_count_is_small(
    tmp_path: Path,
) -> None:
    registry = _build_registry(
        tmp_path,
        {
            "time": "Normalize timestamps to UTC.",
            "planner": "Break work into executable steps.",
        },
    )
    service = SkillRuntimeService(
        skill_registry=registry,
        retrieval_service=_build_retrieval_service(tmp_path),
    )
    _ = service.rebuild_index()
    role = _build_role(("time", "planner"))

    result = service.prepare_prompt(
        role=role,
        objective="Fix the timestamp bug.",
        shared_state_snapshot=(),
        conversation_context=None,
        consumer="tests.skills.passthrough",
    )

    assert result.routing.diagnostics.mode == SkillRoutingMode.PASSTHROUGH
    assert result.routing.visible_skills == ("time", "planner")
    assert result.user_prompt == "Fix the timestamp bug."
    assert result.system_prompt_skill_instructions == (
        SkillInstructionEntry(
            name="time",
            description="Normalize timestamps to UTC.",
        ),
        SkillInstructionEntry(
            name="planner",
            description="Break work into executable steps.",
        ),
    )


def test_skill_runtime_service_routes_hits_and_caps_visible_skills(
    tmp_path: Path,
) -> None:
    registry = _build_registry(
        tmp_path,
        {
            "time": "Normalize timestamps to UTC and convert timezones.",
            "planner": "Break engineering work into steps.",
            "sql": "Write SQL queries and review migrations.",
            "docs": "Draft release notes and developer documentation.",
            "api": "Design and review HTTP APIs.",
            "tests": "Write regression and unit tests.",
            "frontend": "Implement interface updates.",
            "ops": "Handle runtime operations and deployments.",
            "calendar": "Coordinate schedules and milestones.",
        },
    )
    service = SkillRuntimeService(
        skill_registry=registry,
        retrieval_service=_build_retrieval_service(tmp_path),
    )
    _ = service.rebuild_index()
    role = _build_role(
        (
            "time",
            "planner",
            "sql",
            "docs",
            "api",
            "tests",
            "frontend",
            "ops",
            "calendar",
        )
    )

    result = service.prepare_prompt(
        role=role,
        objective="Convert all PST timestamps in the report to UTC.",
        shared_state_snapshot=(("ticket", "TZ-12"),),
        conversation_context=None,
        orchestration_prompt="Prioritize timezone normalization.",
        consumer="tests.skills.search",
    )

    assert result.routing.diagnostics.mode == SkillRoutingMode.SEARCH
    assert result.routing.visible_skills[0] == "time"
    assert len(result.routing.visible_skills) == 8
    assert result.system_prompt_skill_instructions == ()
    assert set(result.routing.visible_skills).issubset(
        set(result.routing.authorized_skills)
    )
    assert (
        "- time: Normalize timestamps to UTC and convert timezones."
        in result.user_prompt
    )


def test_skill_runtime_service_falls_back_when_search_has_no_hits(
    tmp_path: Path,
) -> None:
    registry = _build_registry(
        tmp_path,
        {
            "planner": "Break work into executable steps.",
            "sql": "Write SQL queries and review migrations.",
            "docs": "Draft release notes and developer documentation.",
            "api": "Design and review HTTP APIs.",
            "tests": "Write regression and unit tests.",
            "frontend": "Implement interface updates.",
            "ops": "Handle runtime operations and deployments.",
            "calendar": "Coordinate schedules and milestones.",
            "triage": "Triage incident tickets and classify severity.",
        },
    )
    service = SkillRuntimeService(
        skill_registry=registry,
        retrieval_service=_build_retrieval_service(tmp_path),
    )
    _ = service.rebuild_index()
    role = _build_role(
        (
            "planner",
            "sql",
            "docs",
            "api",
            "tests",
            "frontend",
            "ops",
            "calendar",
            "triage",
        )
    )

    result = service.prepare_prompt(
        role=role,
        objective="neutrino kaleidoscope harmonic lattice",
        shared_state_snapshot=(),
        conversation_context=None,
        consumer="tests.skills.fallback",
    )

    assert result.routing.diagnostics.mode == SkillRoutingMode.FALLBACK
    assert (
        result.routing.diagnostics.fallback_reason == SkillRoutingFallbackReason.NO_HITS
    )
    assert result.routing.visible_skills == result.routing.authorized_skills


def test_skill_runtime_service_prefers_app_variant_for_duplicate_display_name(
    tmp_path: Path,
) -> None:
    app_skill_dir = tmp_path / ".agent-teams" / "skills" / "time"
    builtin_skill_dir = tmp_path / "builtin" / "skills" / "time"
    _write_skill(
        app_skill_dir,
        name="time",
        description="App timezone helper.",
    )
    _write_skill(
        builtin_skill_dir,
        name="time",
        description="Builtin timezone helper.",
    )
    registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / ".agent-teams" / "skills",
        builtin_skills_dir=tmp_path / "builtin" / "skills",
    )
    service = SkillRuntimeService(
        skill_registry=registry,
        retrieval_service=_build_retrieval_service(tmp_path),
    )
    _ = service.rebuild_index()
    role = _build_role(("app:time", "builtin:time"))

    result = service.prepare_prompt(
        role=role,
        objective="Fix the timestamp bug.",
        shared_state_snapshot=(),
        conversation_context=None,
        consumer="tests.skills.duplicate_preference",
    )

    assert result.routing.authorized_skills == ("time",)
    assert result.routing.visible_skills == ("time",)
    assert result.system_prompt_skill_instructions == (
        SkillInstructionEntry(
            name="time",
            description="App timezone helper.",
        ),
    )


def _build_registry(
    tmp_path: Path,
    skill_map: dict[str, str],
) -> SkillRegistry:
    skills_dir = tmp_path / "skills"
    for name, description in skill_map.items():
        _write_skill(
            skills_dir / name,
            name=name,
            description=description,
        )
    return SkillRegistry(directory=SkillsDirectory(base_dir=skills_dir))


def _build_retrieval_service(tmp_path: Path) -> RetrievalService:
    return RetrievalService(store=SqliteFts5RetrievalStore(tmp_path / "retrieval.db"))


def _build_role(skills: tuple[str, ...]) -> RoleDefinition:
    return RoleDefinition(
        role_id="spec_coder",
        name="Spec Coder",
        description="Implements requested changes.",
        version="1.0.0",
        tools=(),
        skills=skills,
        model_profile="default",
        system_prompt="Implement tasks.",
    )


def _write_skill(
    skill_dir: Path,
    *,
    name: str,
    description: str,
) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{description}\n",
        encoding="utf-8",
    )
