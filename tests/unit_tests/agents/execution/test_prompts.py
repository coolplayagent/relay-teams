# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_teams.agents.execution.prompt_instructions import PromptInstructionResolver
from agent_teams.agents.execution import system_prompts
from agent_teams.agents.execution.system_prompts import (
    PromptSkillInstruction,
    RuntimePromptBuildInput,
    SystemPromptSectionsInput,
    build_runtime_system_prompt,
    build_runtime_system_prompt_result,
    compose_system_prompt,
)
from agent_teams.agents.execution.user_prompts import (
    UserPromptBuildInput,
    UserPromptSkillCandidate,
    build_user_prompt,
)
from agent_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from agent_teams.mcp.mcp_models import McpConfigScope, McpServerSpec, McpToolInfo
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from agent_teams.roles.temporary_role_models import TemporaryRoleSpec
from agent_teams.roles.temporary_role_repository import TemporaryRoleRepository
from agent_teams.sessions.runs.run_models import RunTopologySnapshot
from agent_teams.sessions.session_models import SessionMode


@pytest.fixture(autouse=True)
def _suppress_host_github_prompt_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        system_prompts,
        "_get_github_cli_environment_status",
        lambda: (False, None),
    )


def _role(role_id: str) -> RoleDefinition:
    tools = ()
    if role_id.casefold() in {"coordinator_agent", "coordinator"}:
        tools = (
            "create_tasks",
            "create_temporary_role",
            "update_task",
            "list_available_roles",
            "list_delegated_tasks",
            "dispatch_task",
        )
    return RoleDefinition(
        role_id=role_id,
        name="role",
        description="Role description.",
        version="1",
        tools=tools,
        mcp_servers=(),
        skills=(),
        model_profile="default",
        system_prompt="You are a focused agent.",
    )


def _task() -> TaskEnvelope:
    return TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="trace-1",
        objective="Deliver weekly summary",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )


class _FakeMcpRegistry(McpRegistry):
    def __init__(self) -> None:
        super().__init__(
            (
                McpServerSpec(
                    name="docs",
                    config={"mcpServers": {"docs": {"command": "npx"}}},
                    server_config={"command": "npx"},
                    source=McpConfigScope.APP,
                ),
            )
        )

    async def list_tools(self, name: str) -> tuple[McpToolInfo, ...]:
        assert name == "docs"
        return (McpToolInfo(name="docs_search", description="Search docs"),)


def _coordinator_registry() -> RoleRegistry:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="coordinator_agent",
            name="Coordinator",
            description="Coordinates delegated work.",
            version="1",
            tools=(
                "create_tasks",
                "create_temporary_role",
                "update_task",
                "list_available_roles",
                "list_delegated_tasks",
                "dispatch_task",
            ),
            mcp_servers=(),
            skills=(),
            model_profile="default",
            system_prompt="You are a focused agent.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="writer_agent",
            name="Writer",
            description="Drafts release notes.",
            version="1",
            tools=("read", "write"),
            mcp_servers=("docs",),
            skills=("time",),
            model_profile="default",
            system_prompt="You are a writer.",
        )
    )
    return registry


def test_runtime_system_prompt_for_coordinator_has_contract_and_context() -> None:
    prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("coordinator_agent"),
                task=_task(),
                topology=RunTopologySnapshot(
                    session_mode=SessionMode.ORCHESTRATION,
                    main_agent_role_id="MainAgent",
                    normal_root_role_id="MainAgent",
                    coordinator_role_id="coordinator_agent",
                    orchestration_preset_id="default",
                    orchestration_prompt="Delegate by capability and finalize yourself.",
                    allowed_role_ids=("writer_agent",),
                ),
                shared_state_snapshot=(("status", "ready"),),
            ),
            role_registry=_coordinator_registry(),
            mcp_registry=_FakeMcpRegistry(),
        )
    )

    assert prompt.startswith("You are a focused agent.")
    assert "## Runtime Rules" in prompt
    assert "## Orchestration Rules" in prompt
    assert "## Orchestration Prompt" in prompt
    assert "## Available Roles" in prompt
    assert "### writer_agent" in prompt
    assert (
        "Delegate only when another role is a better fit than continuing yourself."
        in prompt
    )
    assert (
        "Create tasks as durable contracts with concrete outcomes and constraints."
        in prompt
    )
    assert (
        "Inspect the current worker pool with `list_available_roles` when selecting or reusing a dispatch target."
        in prompt
    )
    assert (
        "If no existing role is a good fit, create a run-scoped role with `create_temporary_role` before dispatch."
        in prompt
    )
    assert (
        "Prefer `template_role_id` when creating a temporary role so it inherits the closest existing capabilities."
        in prompt
    )
    assert "Choose the executing role in `dispatch_task`." in prompt
    assert (
        "Use the dispatch prompt to pass stage-specific instructions and upstream context."
        in prompt
    )
    assert "dispatch targets, not your own capabilities." in prompt
    assert "- Source: static" in prompt
    assert "- Description: Drafts release notes." in prompt
    assert "- Tools: read, write" in prompt
    assert "- MCP Tools: docs_search" in prompt
    assert "- Skills: time" in prompt
    assert "Deliver weekly summary" not in prompt


def test_runtime_system_prompt_ignores_unknown_mcp_servers_in_available_roles() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="coordinator_agent",
            name="Coordinator",
            description="Coordinates delegated work.",
            version="1",
            tools=("create_tasks", "update_task", "dispatch_task"),
            mcp_servers=(),
            skills=(),
            model_profile="default",
            system_prompt="You are a focused agent.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="writer_agent",
            name="Writer",
            description="Drafts release notes.",
            version="1",
            tools=("read", "write"),
            mcp_servers=("docs", "missing_server"),
            skills=("time",),
            model_profile="default",
            system_prompt="You are a writer.",
        )
    )

    prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("coordinator_agent"),
                task=_task(),
                topology=RunTopologySnapshot(
                    session_mode=SessionMode.ORCHESTRATION,
                    main_agent_role_id="MainAgent",
                    normal_root_role_id="MainAgent",
                    coordinator_role_id="coordinator_agent",
                    orchestration_preset_id="default",
                    orchestration_prompt="Delegate by capability and finalize yourself.",
                    allowed_role_ids=("writer_agent",),
                ),
                shared_state_snapshot=(),
            ),
            role_registry=registry,
            mcp_registry=_FakeMcpRegistry(),
        )
    )

    assert "- MCP Tools: docs_search" in prompt


def test_runtime_system_prompt_includes_run_temporary_roles_in_available_roles(
    tmp_path: Path,
) -> None:
    registry = _coordinator_registry()
    resolver = RuntimeRoleResolver(
        role_registry=registry,
        temporary_role_repository=TemporaryRoleRepository(tmp_path / "roles.db"),
    )
    resolver.create_temporary_role(
        run_id="trace-1",
        session_id="session-1",
        role=TemporaryRoleSpec(
            role_id="tmp_writer",
            name="Tmp Writer",
            description="Handles a run-specific writing format.",
            system_prompt="You are a temporary writer.",
            tools=("read", "write_tmp"),
        ),
    )

    prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("coordinator_agent"),
                task=_task(),
                topology=RunTopologySnapshot(
                    session_mode=SessionMode.ORCHESTRATION,
                    main_agent_role_id="MainAgent",
                    normal_root_role_id="MainAgent",
                    coordinator_role_id="coordinator_agent",
                    orchestration_preset_id="default",
                    orchestration_prompt="Delegate by capability and finalize yourself.",
                    allowed_role_ids=("writer_agent",),
                ),
                shared_state_snapshot=(),
            ),
            role_registry=registry,
            runtime_role_resolver=resolver,
            mcp_registry=_FakeMcpRegistry(),
        )
    )

    assert "### writer_agent" in prompt
    assert "### tmp_writer" in prompt
    assert "- Source: temporary" in prompt
    assert "- Description: Handles a run-specific writing format." in prompt
    assert "- Tools: read, write_tmp" in prompt


def test_runtime_system_prompt_for_worker_skips_runtime_contract() -> None:
    working_directory = Path("/tmp/workspace-root")
    prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("writer_agent"),
                task=_task(),
                shared_state_snapshot=(),
                working_directory=working_directory,
            )
        )
    )

    assert prompt.startswith("You are a focused agent.")
    assert "## Runtime Environment Information" in prompt
    assert "- Operating System:" in prompt
    assert f"- Working Directory: {working_directory.resolve()}" in prompt


def test_runtime_environment_prompt_mentions_github_when_token_and_system_gh_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system_gh_path = Path("/usr/bin/gh")
    monkeypatch.setattr(
        system_prompts,
        "_get_github_cli_environment_status",
        lambda: (True, system_gh_path),
    )

    prompt = system_prompts.build_environment_info_prompt(
        working_directory=Path("/tmp/project")
    )

    assert (
        f"- GitHub CLI: token configured; using system gh at {system_gh_path}" in prompt
    )


def test_runtime_environment_prompt_mentions_on_demand_gh_when_only_token_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        system_prompts,
        "_get_github_cli_environment_status",
        lambda: (True, None),
    )

    prompt = system_prompts.build_environment_info_prompt(
        working_directory=Path("/tmp/project")
    )

    assert "- GitHub CLI: token configured; gh will be resolved on demand" in prompt


def test_runtime_system_prompt_layers_keep_base_instructions_before_workspace_context() -> (
    None
):
    result = asyncio.run(
        build_runtime_system_prompt_result(
            RuntimePromptBuildInput(
                role=_role("coordinator_agent"),
                task=_task(),
                topology=RunTopologySnapshot(
                    session_mode=SessionMode.ORCHESTRATION,
                    main_agent_role_id="MainAgent",
                    normal_root_role_id="MainAgent",
                    coordinator_role_id="coordinator_agent",
                    orchestration_preset_id="default",
                    orchestration_prompt="Delegate by capability and finalize yourself.",
                    allowed_role_ids=("writer_agent",),
                ),
                shared_state_snapshot=(),
                working_directory=Path("/tmp/workspace-root"),
            ),
            role_registry=_coordinator_registry(),
            mcp_registry=_FakeMcpRegistry(),
        )
    )

    assert result.base_instructions.startswith("You are a focused agent.")
    assert "## Orchestration Rules" in result.base_instructions
    assert "## Available Roles" in result.capability_summary
    assert "## Runtime Environment Information" in result.workspace_context
    assert "## Orchestration Prompt" in result.workspace_context
    assert result.prompt.index("## Orchestration Rules") < result.prompt.index(
        "## Available Roles"
    )
    assert result.prompt.index("## Available Roles") < result.prompt.index(
        "## Runtime Environment Information"
    )


def test_compose_system_prompt_keeps_system_prompt_free_of_skill_catalog() -> None:
    prompt = compose_system_prompt(
        SystemPromptSectionsInput(
            base_instructions="## Role\nYou are a planner.",
            skill_instructions=(
                PromptSkillInstruction(
                    name="time",
                    description="Normalize all times to UTC.",
                ),
            ),
        )
    )

    assert "## Tool Rules" not in prompt
    assert "## Available Skills" not in prompt
    assert "- time: Normalize all times to UTC." not in prompt


def test_compose_system_prompt_keeps_capability_summary_before_workspace_context() -> (
    None
):
    prompt = compose_system_prompt(
        SystemPromptSectionsInput(
            base_instructions="## Role\nYou are a planner.",
            capability_summary="## Available Roles\n### writer_agent",
            workspace_context=(
                "## Runtime Environment Information\n- Working Directory: /tmp/project"
            ),
            skill_instructions=(
                PromptSkillInstruction(
                    name="time",
                    description="Normalize all times to UTC.",
                ),
            ),
        )
    )

    assert prompt.index("## Available Roles") < prompt.index(
        "## Runtime Environment Information"
    )
    assert "## Available Skills" not in prompt


def test_user_prompt_builder_returns_raw_objective() -> None:
    prompt = build_user_prompt(
        UserPromptBuildInput(objective="Draft the release notes.")
    )

    assert prompt == "Draft the release notes."


def test_user_prompt_builder_appends_skill_candidates() -> None:
    prompt = build_user_prompt(
        UserPromptBuildInput(
            objective="Draft the release notes.",
            skill_candidates=(
                UserPromptSkillCandidate(
                    name="planner",
                    description="Break objectives into executable plans.",
                ),
            ),
        )
    )

    assert prompt.startswith("Draft the release notes.")
    assert "## Skill Candidates" in prompt
    assert "- planner: Break objectives into executable plans." in prompt


def test_runtime_system_prompt_for_coordinator_mentions_task_orchestration() -> None:
    prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("coordinator_agent"),
                task=_task(),
                topology=RunTopologySnapshot(
                    session_mode=SessionMode.ORCHESTRATION,
                    main_agent_role_id="MainAgent",
                    normal_root_role_id="MainAgent",
                    coordinator_role_id="coordinator_agent",
                    orchestration_preset_id="default",
                    orchestration_prompt="Delegate by capability and finalize yourself.",
                    allowed_role_ids=("writer_agent",),
                ),
                shared_state_snapshot=(),
            ),
            role_registry=_coordinator_registry(),
            mcp_registry=_FakeMcpRegistry(),
        )
    )

    assert "### writer_agent" in prompt
    assert "## Orchestration Rules" in prompt
    assert "Orchestration Prompt" in prompt
    assert "Choose roles by their Description, Tools, MCP Tools, and Skills." in prompt
    assert "list_available_roles" in prompt


def test_runtime_system_prompt_for_main_agent_uses_base_role_prompt_only() -> None:
    prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("MainAgent"),
                topology=RunTopologySnapshot(
                    session_mode=SessionMode.NORMAL,
                    main_agent_role_id="MainAgent",
                    normal_root_role_id="MainAgent",
                    coordinator_role_id="Coordinator",
                    orchestration_preset_id=None,
                    orchestration_prompt="",
                    allowed_role_ids=(),
                ),
                shared_state_snapshot=(),
            )
        )
    )

    assert "## Runtime Rules" in prompt
    assert "## Normal Mode" not in prompt
    assert "You are a focused agent." in prompt
    assert "## Available Roles" not in prompt


def test_runtime_system_prompt_loads_all_project_agents_files_before_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_teams.agents.execution import prompt_instructions

    project_root = tmp_path / "project"
    nested_dir = project_root / "src" / "feature"
    config_dir = tmp_path / "config"
    nested_dir.mkdir(parents=True)
    config_dir.mkdir()
    monkeypatch.setattr(
        prompt_instructions,
        "GLOBAL_CLAUDE_FILE",
        tmp_path / "missing" / "CLAUDE.md",
    )
    monkeypatch.setattr(
        prompt_instructions,
        "GLOBAL_GEMINI_FILE",
        tmp_path / "missing" / "GEMINI.md",
    )
    (project_root / "AGENTS.md").write_text(
        "Root project instructions.", encoding="utf-8"
    )
    (project_root / "CLAUDE.md").write_text(
        "Claude root instructions.", encoding="utf-8"
    )
    (project_root / "src" / "AGENTS.md").write_text(
        "Nested project instructions.", encoding="utf-8"
    )

    result = asyncio.run(
        build_runtime_system_prompt_result(
            RuntimePromptBuildInput(
                role=_role("writer_agent"),
                shared_state_snapshot=(),
                working_directory=nested_dir,
                worktree_root=project_root,
            ),
            instruction_resolver=PromptInstructionResolver(app_config_dir=config_dir),
        )
    )

    assert "Root project instructions." in result.prompt
    assert "Nested project instructions." in result.prompt
    assert "Claude root instructions." not in result.prompt
    assert result.local_instruction_paths == (
        (project_root / "src" / "AGENTS.md").resolve(),
        (project_root / "AGENTS.md").resolve(),
    )


def test_runtime_system_prompt_falls_back_to_global_claude_before_gemini(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_teams.agents.execution import prompt_instructions

    config_dir = tmp_path / "config"
    home_dir = tmp_path / "home"
    claude_file = home_dir / ".claude" / "CLAUDE.md"
    gemini_file = home_dir / ".gemini" / "GEMINI.md"
    config_dir.mkdir()
    claude_file.parent.mkdir(parents=True)
    gemini_file.parent.mkdir(parents=True)
    claude_file.write_text("Global Claude instructions.", encoding="utf-8")
    gemini_file.write_text("Global Gemini instructions.", encoding="utf-8")
    monkeypatch.setattr(prompt_instructions, "GLOBAL_CLAUDE_FILE", claude_file)
    monkeypatch.setattr(prompt_instructions, "GLOBAL_GEMINI_FILE", gemini_file)

    result = asyncio.run(
        build_runtime_system_prompt_result(
            RuntimePromptBuildInput(
                role=_role("writer_agent"),
                shared_state_snapshot=(),
                working_directory=tmp_path,
                worktree_root=tmp_path,
            ),
            instruction_resolver=PromptInstructionResolver(app_config_dir=config_dir),
        )
    )

    assert "Global Claude instructions." in result.prompt
    assert "Global Gemini instructions." not in result.prompt
    assert result.local_instruction_paths == (claude_file.resolve(),)


def test_runtime_system_prompt_loads_configured_instruction_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    working_dir = project_root / "src"
    config_dir = tmp_path / "config"
    configured_file = project_root / "notes" / "prompt.md"
    working_dir.mkdir(parents=True)
    config_dir.mkdir()
    configured_file.parent.mkdir(parents=True)
    configured_file.write_text("Configured local instructions.", encoding="utf-8")
    resolver = PromptInstructionResolver(
        app_config_dir=config_dir,
        instructions=("notes/*.md", "https://example.test/prompt.md"),
    )

    async def fake_fetch(_url: str) -> str:
        return "Configured remote instructions."

    monkeypatch.setattr(resolver, "_fetch_url", fake_fetch)

    result = asyncio.run(
        build_runtime_system_prompt_result(
            RuntimePromptBuildInput(
                role=_role("writer_agent"),
                shared_state_snapshot=(),
                working_directory=working_dir,
                worktree_root=project_root,
            ),
            instruction_resolver=resolver,
        )
    )

    assert "Configured local instructions." in result.prompt
    assert "Configured remote instructions." in result.prompt
    assert configured_file.resolve() in result.local_instruction_paths
