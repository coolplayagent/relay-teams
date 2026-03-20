# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path

from agent_teams.agents.execution.system_prompts import (
    PromptSkillInstruction,
    RuntimePromptBuildInput,
    SystemPromptBuildInput,
    build_runtime_system_prompt,
    build_system_prompt,
)
from agent_teams.agents.execution.user_prompts import (
    UserPromptBuildInput,
    build_user_prompt,
)
from agent_teams.mcp.mcp_models import McpConfigScope, McpServerSpec, McpToolInfo
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.sessions.runs.run_models import RunTopologySnapshot
from agent_teams.sessions.session_models import SessionMode
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.agents.tasks.models import TaskEnvelope, VerificationPlan


def _role(role_id: str) -> RoleDefinition:
    tools = ()
    if role_id.casefold() in {"coordinator_agent", "coordinator"}:
        tools = (
            "create_tasks",
            "update_task",
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
        return (McpToolInfo(name="search", description="Search docs"),)


def _coordinator_registry() -> RoleRegistry:
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
    assert "Choose the executing role in `dispatch_task`." in prompt
    assert (
        "Use the dispatch prompt to pass stage-specific instructions and upstream context."
        in prompt
    )
    assert "dispatch targets, not your own capabilities." in prompt
    assert "- Description: Drafts release notes." in prompt
    assert "- Tools: read, write" in prompt
    assert "- MCP Tools: docs/search" in prompt
    assert "- Skills: time" in prompt
    assert "Deliver weekly summary" not in prompt


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


def test_system_prompt_renders_tools_and_skills() -> None:
    prompt = build_system_prompt(
        SystemPromptBuildInput(
            system_prompt="## Role\nYou are a planner.",
            allowed_tools=("dispatch_task",),
            skill_instructions=(
                PromptSkillInstruction(
                    name="time",
                    description="Normalize all times to UTC.",
                ),
            ),
        )
    )

    assert "## Tool Rules" not in prompt
    assert "## Available Skills" in prompt
    assert "- time: Normalize all times to UTC." in prompt


def test_user_prompt_builder_returns_raw_objective() -> None:
    prompt = build_user_prompt(
        UserPromptBuildInput(objective="Draft the release notes.")
    )

    assert prompt == "Draft the release notes."


def test_runtime_system_prompt_for_coordinator_mentions_task_orchestration() -> None:
    prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("coordinator_agent"),
                task=_task(),
                topology=RunTopologySnapshot(
                    session_mode=SessionMode.ORCHESTRATION,
                    main_agent_role_id="MainAgent",
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


def test_runtime_system_prompt_for_main_agent_uses_base_role_prompt_only() -> None:
    prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("MainAgent"),
                topology=RunTopologySnapshot(
                    session_mode=SessionMode.NORMAL,
                    main_agent_role_id="MainAgent",
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
