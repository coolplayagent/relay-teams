# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_teams.interfaces.server.deps import (
    get_mcp_registry,
    get_role_registry,
    get_skill_registry,
    get_tool_registry,
)
from agent_teams.interfaces.server.routers import prompts
from agent_teams.mcp.models import McpConfigScope, McpServerSpec, McpToolInfo
from agent_teams.mcp.registry import McpRegistry
from agent_teams.roles.models import RoleDefinition
from agent_teams.roles.registry import RoleRegistry
from agent_teams.skills.models import SkillInstructionEntry
from agent_teams.tools.registry import ToolRegistry


class _FakeSkillRegistry:
    def __init__(self) -> None:
        self._known = {"time", "planner"}

    def validate_known(self, skill_names: tuple[str, ...]) -> None:
        unknown = [name for name in skill_names if name not in self._known]
        if unknown:
            raise ValueError(f"Unknown skills: {unknown}")

    def get_instruction_entries(
        self, skill_names: tuple[str, ...]
    ) -> tuple[SkillInstructionEntry, ...]:
        self.validate_known(skill_names)
        return tuple(
            SkillInstructionEntry(
                name=name,
                description=(
                    "Normalize all times to UTC."
                    if name == "time"
                    else "Break objectives into executable plans."
                ),
            )
            for name in skill_names
        )


def _build_role_registry() -> RoleRegistry:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="coordinator_agent",
            name="Coordinator",
            description="Coordinates delegated work.",
            version="1.0",
            tools=("dispatch_task",),
            mcp_servers=("docs",),
            skills=("time",),
            model_profile="default",
            system_prompt="You are coordinator.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="writer_agent",
            name="Writer",
            description="Drafts release notes and summaries.",
            version="1.0",
            tools=("dispatch_task",),
            mcp_servers=("docs",),
            skills=("planner",),
            model_profile="default",
            system_prompt="You are writer.",
        )
    )
    return registry


def _build_tool_registry() -> ToolRegistry:
    return ToolRegistry(tools={"dispatch_task": (lambda _agent: None)})


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
        return (
            McpToolInfo(name="read_file", description="Read a file"),
            McpToolInfo(name="search_docs", description="Search docs"),
        )


def _create_client() -> TestClient:
    app = FastAPI()
    app.include_router(prompts.router, prefix="/api")
    app.dependency_overrides[get_role_registry] = _build_role_registry
    app.dependency_overrides[get_tool_registry] = _build_tool_registry
    app.dependency_overrides[get_mcp_registry] = _FakeMcpRegistry
    app.dependency_overrides[get_skill_registry] = _FakeSkillRegistry
    return TestClient(app)


def test_prompts_preview_returns_runtime_provider_and_user_sections() -> None:
    client = _create_client()

    response = client.post(
        "/api/prompts:preview",
        json={
            "role_id": "coordinator_agent",
            "objective": "Deliver summary",
            "shared_state": {"priority": 1},
            "tools": ["dispatch_task"],
            "skills": ["time"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["role_id"] == "coordinator_agent"
    assert payload["tools"] == ["dispatch_task"]
    assert payload["skills"] == ["time"]
    assert payload["runtime_system_prompt"].startswith("You are coordinator.")
    assert "## Role Usage" in payload["runtime_system_prompt"]
    assert "## Available Roles" in payload["runtime_system_prompt"]
    assert "### writer_agent" in payload["runtime_system_prompt"]
    assert (
        "- Description: Drafts release notes and summaries."
        in payload["runtime_system_prompt"]
    )
    assert "- Tools: dispatch_task" in payload["runtime_system_prompt"]
    assert (
        "- MCP Tools: docs/read_file, docs/search_docs"
        in payload["runtime_system_prompt"]
    )
    assert "- Skills: planner" in payload["runtime_system_prompt"]
    assert "priority" not in payload["runtime_system_prompt"]
    assert "## Available Skills" in payload["provider_system_prompt"]
    assert "- time: Normalize all times to UTC." in payload["provider_system_prompt"]
    assert payload["user_prompt"] == "Deliver summary"
    assert "## Available Roles" in payload["provider_system_prompt"]


def test_prompts_preview_skill_override_replaces_role_default() -> None:
    client = _create_client()

    response = client.post(
        "/api/prompts:preview",
        json={
            "role_id": "coordinator_agent",
            "skills": ["planner"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["objective"] == ""
    assert payload["skills"] == ["planner"]
    assert payload["user_prompt"] == ""
    assert (
        "- planner: Break objectives into executable plans."
        in payload["provider_system_prompt"]
    )
    assert (
        "- time: Normalize all times to UTC." not in payload["provider_system_prompt"]
    )


def test_prompts_preview_returns_404_for_unknown_role() -> None:
    client = _create_client()

    response = client.post(
        "/api/prompts:preview",
        json={"role_id": "unknown_role"},
    )

    assert response.status_code == 404


def test_prompts_preview_returns_400_for_unknown_tool_override() -> None:
    client = _create_client()

    response = client.post(
        "/api/prompts:preview",
        json={
            "role_id": "coordinator_agent",
            "tools": ["unknown_tool"],
        },
    )

    assert response.status_code == 400
    assert "Unknown tools" in response.text


def test_prompts_preview_returns_400_for_unknown_skill_override() -> None:
    client = _create_client()

    response = client.post(
        "/api/prompts:preview",
        json={
            "role_id": "coordinator_agent",
            "skills": ["unknown_skill"],
        },
    )

    assert response.status_code == 400
    assert "Unknown skills" in response.text
