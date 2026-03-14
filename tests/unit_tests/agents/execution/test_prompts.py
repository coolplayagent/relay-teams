# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

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
from agent_teams.mcp.models import McpConfigScope, McpServerSpec, McpToolInfo
from agent_teams.mcp.registry import McpRegistry
from agent_teams.roles.models import RoleDefinition
from agent_teams.roles.registry import RoleRegistry
from agent_teams.agents.tasks.models import TaskEnvelope, VerificationPlan


def _role(role_id: str) -> RoleDefinition:
    tools = ()
    if role_id.casefold() in {"coordinator_agent", "coordinator"}:
        tools = (
            "create_tasks",
            "update_task",
            "list_run_tasks",
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
            tools=("create_tasks", "update_task", "list_run_tasks", "dispatch_task"),
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
                shared_state_snapshot=(("status", "ready"),),
            ),
            role_registry=_coordinator_registry(),
            mcp_registry=_FakeMcpRegistry(),
        )
    )

    assert prompt.startswith("You are a focused agent.")
    assert "## Role Usage" in prompt
    assert "## Available Roles" in prompt
    assert "### Writer" in prompt
    assert "- Description: Drafts release notes." in prompt
    assert "- Tools: read, write" in prompt
    assert "- MCP Tools: docs/search" in prompt
    assert "- Skills: time" in prompt
    assert "Deliver weekly summary" not in prompt


def test_runtime_system_prompt_for_worker_skips_runtime_contract() -> None:
    prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("writer_agent"),
                task=_task(),
                shared_state_snapshot=(),
            )
        )
    )

    assert prompt == "You are a focused agent."


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
                shared_state_snapshot=(),
            ),
            role_registry=_coordinator_registry(),
            mcp_registry=_FakeMcpRegistry(),
        )
    )

    assert "### Writer" in prompt
    assert "## Role Usage" in prompt
    assert "Use `create_tasks` to create tasks and bind to available roles." in prompt
    assert "Use `dispatch_task` to dispatch tasks." in prompt
    assert "Use `update_task` to update tasks." in prompt
