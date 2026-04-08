# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import mkdtemp
import threading
from typing import cast

from pydantic import JsonValue
import pytest

from relay_teams.builtin import get_builtin_skills_dir
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.skills.discovery import SkillsDirectory
from relay_teams.skills.skill_models import SkillScope
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.skills.skill_registry import SkillRegistry

from relay_teams.tools.runtime import ToolContext


def test_get_toolset_tools_builds_skill_tools_without_annotation_errors() -> None:
    registry = SkillRegistry(
        directory=SkillsDirectory(base_dir=Path(".agent_teams/skills"))
    )

    tools = registry.get_toolset_tools(("time",))

    names = {tool.name for tool in tools}
    assert names == {"load_skill"}


def test_get_instruction_entries_returns_structured_data(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "time"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: time\n"
        "description: timezone helper\n"
        "---\n"
        "Use UTC for all timestamps.\n",
        encoding="utf-8",
    )
    registry = SkillRegistry(directory=SkillsDirectory(base_dir=tmp_path / "skills"))

    entries = registry.get_instruction_entries(("time",))

    assert len(entries) == 1
    assert entries[0].name == "time"
    assert entries[0].description == "timezone helper"


def test_resolve_known_ignores_unknown_skills_when_strict_is_false(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills" / "time"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: time\n"
        "description: timezone helper\n"
        "---\n"
        "Use UTC for all timestamps.\n",
        encoding="utf-8",
    )
    registry = SkillRegistry(directory=SkillsDirectory(base_dir=tmp_path / "skills"))

    resolved = registry.resolve_known(
        ("time", "missing_skill"),
        strict=False,
        consumer="tests.unit_tests.skills.test_skill_registry",
    )

    assert resolved == ("app:time",)


def test_registry_from_skill_dirs_keeps_builtin_and_app_variants_for_same_name(
    tmp_path: Path,
) -> None:
    builtin_skill_dir = tmp_path / "builtin" / "skills" / "time"
    app_skill_dir = tmp_path / ".agent-teams" / "skills" / "time"
    builtin_skill_dir.mkdir(parents=True)
    app_skill_dir.mkdir(parents=True)

    (builtin_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: time\n"
        "description: builtin timezone helper\n"
        "---\n"
        "Use the builtin timezone.\n",
        encoding="utf-8",
    )
    (app_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: time\n"
        "description: app timezone helper\n"
        "---\n"
        "Use UTC for all app timestamps.\n",
        encoding="utf-8",
    )

    registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / ".agent-teams" / "skills",
        builtin_skills_dir=tmp_path / "builtin" / "skills",
    )

    app_skill = registry.get_skill_definition("app:time")
    builtin_skill = registry.get_skill_definition("builtin:time")
    resolved = registry.resolve_known(("time",), strict=False)
    entries = registry.get_instruction_entries(("app:time", "builtin:time"))

    assert app_skill is not None
    assert app_skill.scope == SkillScope.APP
    assert app_skill.metadata.description == "app timezone helper"
    assert builtin_skill is not None
    assert builtin_skill.scope == SkillScope.BUILTIN
    assert resolved == ("app:time",)
    assert entries[0].name == "time (app)"
    assert entries[1].name == "time (builtin)"


def test_registry_from_skill_dirs_loads_user_skill_when_project_skill_missing(
    tmp_path: Path,
) -> None:
    builtin_skill_dir = tmp_path / "builtin" / "skills" / "time"
    builtin_skill_dir.mkdir(parents=True)
    (builtin_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: time\n"
        "description: builtin timezone helper\n"
        "---\n"
        "Use the builtin timezone.\n",
        encoding="utf-8",
    )

    registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / ".agent-teams" / "skills",
        builtin_skills_dir=tmp_path / "builtin" / "skills",
    )

    skill = registry.get_skill_definition("time")

    assert skill is not None
    assert skill.scope == SkillScope.BUILTIN
    assert registry.list_names() == ("builtin:time",)


def test_registry_from_config_dirs_merges_builtin_and_app_skills(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    builtin_skills_dir = tmp_path / "builtin" / "skills"
    monkeypatch.setattr(
        "relay_teams.skills.discovery.get_builtin_skills_dir_path",
        lambda: builtin_skills_dir.resolve(),
    )

    _write_skill(
        builtin_skills_dir / "shared",
        name="shared",
        description="builtin shared skill",
        instructions="Builtin instructions.",
    )
    _write_skill(
        builtin_skills_dir / "builtin_only",
        name="builtin_only",
        description="builtin only skill",
        instructions="Builtin only instructions.",
    )
    _write_skill(
        app_config_dir / "skills" / "shared",
        name="shared",
        description="app shared skill",
        instructions="App instructions.",
    )
    _write_skill(
        app_config_dir / "skills" / "app_only",
        name="app_only",
        description="app only skill",
        instructions="App only instructions.",
    )

    registry = SkillRegistry.from_config_dirs(app_config_dir=app_config_dir)

    skills = registry.list_skill_definitions()
    shared_app_skill = registry.get_skill_definition("app:shared")
    shared_builtin_skill = registry.get_skill_definition("builtin:shared")
    builtin_only_skill = registry.get_skill_definition("builtin:builtin_only")

    assert tuple(skill.ref for skill in skills) == (
        "app:app_only",
        "builtin:builtin_only",
        "app:shared",
        "builtin:shared",
    )
    assert shared_app_skill is not None
    assert shared_app_skill.scope == SkillScope.APP
    assert shared_builtin_skill is not None
    assert shared_builtin_skill.scope == SkillScope.BUILTIN
    assert builtin_only_skill is not None
    assert builtin_only_skill.scope == SkillScope.BUILTIN


def test_registry_from_config_dirs_creates_app_skills_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    monkeypatch.setattr(
        "relay_teams.skills.discovery.get_builtin_skills_dir_path",
        lambda: (tmp_path / "builtin" / "skills").resolve(),
    )

    registry = SkillRegistry.from_config_dirs(app_config_dir=app_config_dir)

    assert (app_config_dir / "skills").is_dir()
    assert registry.list_skill_definitions() == ()


def test_skills_directory_discover_replaces_skill_cache_atomically(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "alpha",
        name="alpha",
        description="alpha skill",
        instructions="Use alpha.",
    )
    _write_skill(
        tmp_path / "skills" / "beta",
        name="beta",
        description="beta skill",
        instructions="Use beta.",
    )
    directory = SkillsDirectory(base_dir=tmp_path / "skills")
    directory.discover()
    original_load_skill = directory._load_skill
    load_started = threading.Event()
    allow_continue = threading.Event()

    def blocking_load_skill(*, path: Path, scope: SkillScope):
        if path.parent.name == "alpha":
            load_started.set()
            assert allow_continue.wait(timeout=5)
        return original_load_skill(path=path, scope=scope)

    directory._load_skill = blocking_load_skill
    worker = threading.Thread(target=directory.discover)
    worker.start()
    assert load_started.wait(timeout=5)

    refs_during_discover = {skill.ref for skill in directory.list_skills()}

    allow_continue.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert refs_during_discover == {"app:alpha", "app:beta"}


def test_registry_loads_builtin_skill_installer_definition(tmp_path: Path) -> None:
    registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / ".agent-teams" / "skills",
        builtin_skills_dir=get_builtin_skills_dir(),
    )

    skill = registry.get_skill_definition("builtin:skill-installer")

    assert skill is not None
    assert skill.scope == SkillScope.BUILTIN
    assert tuple(sorted(skill.metadata.scripts.keys())) == (
        "bind-skill-to-role",
        "install-skill-from-github",
        "list-skills",
    )


def test_load_skill_returns_manifest_and_selected_absolute_file_paths(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills" / "time"
    resources_dir = skill_dir / "resources"
    scripts_dir = skill_dir / "scripts"
    resources_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    manifest_path = skill_dir / "SKILL.md"
    manifest_content = (
        "---\n"
        "name: time\n"
        "description: timezone helper\n"
        "resources:\n"
        "  usage.txt:\n"
        "    description: Usage notes.\n"
        "    path: resources/usage.txt\n"
        "---\n"
        "Use UTC for all timestamps.\n"
    )
    manifest_path.write_text(manifest_content, encoding="utf-8")
    usage_path = resources_dir / "usage.txt"
    usage_path.write_text("Use UTC.\n", encoding="utf-8")
    script_path = scripts_dir / "trace_context.py"
    script_path.write_text("print('trace')\n", encoding="utf-8")
    registry = SkillRegistry(directory=SkillsDirectory(base_dir=tmp_path / "skills"))

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, _FakeCtx())),
            name="time",
        )
    )

    assert result["ok"] is True
    data = cast(dict[str, JsonValue], result["data"])
    assert data["ref"] == "app:time"
    assert data["manifest_path"] == manifest_path.resolve().as_posix()
    assert data["manifest_content"] == manifest_content
    assert data["instructions"] == "Use UTC for all timestamps."
    assert data["directory"] == skill_dir.resolve().as_posix()
    assert cast(dict[str, JsonValue], data["resources"])["usage.txt"] == {
        "name": "usage.txt",
        "description": "Usage notes.",
        "path": usage_path.resolve().as_posix(),
        "content": None,
    }
    assert cast(dict[str, JsonValue], data["scripts"])["trace_context"] == {
        "name": "trace_context",
        "description": "Execute trace_context script.",
        "path": script_path.resolve().as_posix(),
    }
    assert data["files_truncated"] is False
    assert data["files_omitted_count"] == 0
    assert sorted(cast(list[str], data["files"])) == sorted(
        [
            manifest_path.resolve().as_posix(),
            script_path.resolve().as_posix(),
            usage_path.resolve().as_posix(),
        ]
    )


def test_load_skill_prefers_app_scope_for_ambiguous_plain_name(
    tmp_path: Path,
) -> None:
    app_skill_dir = tmp_path / ".agent-teams" / "skills" / "deepresearch"
    builtin_skill_dir = tmp_path / "builtin" / "skills" / "deepresearch"
    app_skill_dir.mkdir(parents=True)
    builtin_skill_dir.mkdir(parents=True)
    (app_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: deepresearch\n"
        "description: app deepresearch\n"
        "---\n"
        "Use app deepresearch.\n",
        encoding="utf-8",
    )
    (builtin_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: deepresearch\n"
        "description: builtin deepresearch\n"
        "---\n"
        "Use builtin deepresearch.\n",
        encoding="utf-8",
    )
    registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / ".agent-teams" / "skills",
        builtin_skills_dir=tmp_path / "builtin" / "skills",
    )
    ctx = _FakeCtx()
    ctx.deps.role_registry = RoleRegistry()
    ctx.deps.role_registry.register(
        RoleDefinition(
            role_id="spec_coder",
            name="Spec Coder",
            description="Implements requested changes.",
            version="1",
            tools=(),
            skills=("deepresearch",),
            system_prompt="Implement tasks.",
        )
    )

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, ctx)),
            name="deepresearch",
        )
    )

    assert result["ok"] is True
    data = cast(dict[str, JsonValue], result["data"])
    assert data["ref"] == "app:deepresearch"
    assert data["description"] == "app deepresearch"
    assert data["instructions"] == "Use app deepresearch."


def test_load_skill_uses_authorized_builtin_scope_for_ambiguous_plain_name(
    tmp_path: Path,
) -> None:
    app_skill_dir = tmp_path / ".agent-teams" / "skills" / "deepresearch"
    builtin_skill_dir = tmp_path / "builtin" / "skills" / "deepresearch"
    app_skill_dir.mkdir(parents=True)
    builtin_skill_dir.mkdir(parents=True)
    (app_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: deepresearch\n"
        "description: app deepresearch\n"
        "---\n"
        "Use app deepresearch.\n",
        encoding="utf-8",
    )
    (builtin_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: deepresearch\n"
        "description: builtin deepresearch\n"
        "---\n"
        "Use builtin deepresearch.\n",
        encoding="utf-8",
    )
    registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / ".agent-teams" / "skills",
        builtin_skills_dir=tmp_path / "builtin" / "skills",
    )
    ctx = _FakeCtx()
    ctx.deps.role_registry = RoleRegistry()
    ctx.deps.role_registry.register(
        RoleDefinition(
            role_id="spec_coder",
            name="Spec Coder",
            description="Implements requested changes.",
            version="1",
            tools=(),
            skills=("builtin:deepresearch",),
            system_prompt="Implement tasks.",
        )
    )

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, ctx)),
            name="deepresearch",
        )
    )

    assert result["ok"] is True
    data = cast(dict[str, JsonValue], result["data"])
    assert data["ref"] == "builtin:deepresearch"
    assert data["description"] == "builtin deepresearch"
    assert data["instructions"] == "Use builtin deepresearch."


def test_load_skill_rejects_role_unauthorized_skill(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "planner"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: planner\ndescription: planning helper\n---\nPlan the work.\n",
        encoding="utf-8",
    )
    registry = SkillRegistry(directory=SkillsDirectory(base_dir=tmp_path / "skills"))

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, _FakeCtx())),
            name="planner",
        )
    )

    assert result["ok"] is False
    error = cast(dict[str, JsonValue], result["error"])
    assert (
        error["message"] == "Role spec_coder is not authorized to load skill: planner"
    )


def test_load_skill_omits_large_dependency_trees_from_file_listing(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills" / "deck"
    docs_dir = skill_dir / "docs"
    node_modules_dir = skill_dir / "node_modules" / "pkg"
    docs_dir.mkdir(parents=True)
    node_modules_dir.mkdir(parents=True)
    manifest_path = skill_dir / "SKILL.md"
    manifest_path.write_text(
        "---\n"
        "name: deck\n"
        "description: build slide decks\n"
        "---\n"
        "Use the planner and designer workflow.\n",
        encoding="utf-8",
    )
    bundled_dependency = node_modules_dir / "index.js"
    bundled_dependency.write_text("export const bundled = true;\n", encoding="utf-8")
    for index in range(240):
        (docs_dir / f"section_{index:03}.md").write_text(
            f"Section {index}\n",
            encoding="utf-8",
        )

    registry = SkillRegistry(directory=SkillsDirectory(base_dir=tmp_path / "skills"))

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, _FakeCtx())),
            name="deck",
        )
    )

    assert result["ok"] is True
    data = cast(dict[str, JsonValue], result["data"])
    assert data["ref"] == "app:deck"
    files = cast(list[str], data["files"])
    assert manifest_path.resolve().as_posix() in files
    assert bundled_dependency.resolve().as_posix() not in files
    assert all("node_modules" not in path for path in files)
    assert data["files_truncated"] is True
    assert cast(int, data["files_omitted_count"]) > 0
    assert len(files) < 241


def _write_skill(
    skill_dir: Path, *, name: str, description: str, instructions: str
) -> None:
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{instructions}\n",
        encoding="utf-8",
    )


def test_validate_known_rejects_ambiguous_plain_name(tmp_path: Path) -> None:
    builtin_skill_dir = tmp_path / "builtin" / "skills" / "time"
    app_skill_dir = tmp_path / ".agent-teams" / "skills" / "time"
    builtin_skill_dir.mkdir(parents=True)
    app_skill_dir.mkdir(parents=True)
    (builtin_skill_dir / "SKILL.md").write_text(
        "---\nname: time\ndescription: builtin timezone helper\n---\nUse builtin.\n",
        encoding="utf-8",
    )
    (app_skill_dir / "SKILL.md").write_text(
        "---\nname: time\ndescription: app timezone helper\n---\nUse app.\n",
        encoding="utf-8",
    )
    registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / ".agent-teams" / "skills",
        builtin_skills_dir=tmp_path / "builtin" / "skills",
    )

    with pytest.raises(ValueError, match="Ambiguous skills require canonical refs"):
        registry.validate_known(("time",))


class _FakeRunEventHub:
    def publish(self, event: object) -> None:
        _ = event


class _FakeRunControlManager:
    def raise_if_cancelled(
        self,
        *,
        run_id: str,
        instance_id: str | None = None,
    ) -> None:
        _ = (run_id, instance_id)


class _FakeApprovalManager:
    def open_approval(self, **kwargs: object) -> None:
        _ = kwargs

    def wait_for_approval(self, **kwargs: object) -> tuple[str, str]:
        _ = kwargs
        return ("approve", "")

    def close_approval(self, **kwargs: object) -> None:
        _ = kwargs


class _FakePolicy:
    timeout_seconds = 0.01

    def requires_approval(self, tool_name: str) -> bool:
        _ = tool_name
        return False


class _FakeRunRuntimeRepo:
    def ensure(
        self,
        *,
        run_id: str,
        session_id: str,
        root_task_id: str,
    ) -> None:
        _ = (run_id, session_id, root_task_id)

    def update(self, run_id: str, **kwargs: object) -> None:
        _ = (run_id, kwargs)


class _FakeDeps:
    def __init__(self) -> None:
        self.run_id = "run-1"
        self.trace_id = "trace-1"
        self.task_id = "task-1"
        self.session_id = "session-1"
        self.instance_id = "inst-1"
        self.role_id = "spec_coder"
        self.role_registry = RoleRegistry()
        self.role_registry.register(
            RoleDefinition(
                role_id="spec_coder",
                name="Spec Coder",
                description="Implements requested changes.",
                version="1",
                tools=(),
                skills=("time", "deck"),
                system_prompt="Implement tasks.",
            )
        )
        self.runtime_role_resolver = None
        self.run_event_hub = _FakeRunEventHub()
        self.run_control_manager = _FakeRunControlManager()
        self.tool_approval_manager = _FakeApprovalManager()
        self.tool_approval_policy = _FakePolicy()
        self.run_runtime_repo = _FakeRunRuntimeRepo()
        self.notification_service = None
        self.shared_store = SharedStateRepository(Path(mkdtemp()) / "state.db")


class _FakeCtx:
    def __init__(self) -> None:
        self.deps = _FakeDeps()
        self.tool_call_id = "toolcall-1"
        self.retry = 0
