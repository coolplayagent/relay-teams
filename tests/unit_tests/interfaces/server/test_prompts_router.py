# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_teams.interfaces.server.deps import (
    get_role_registry,
    get_skill_registry,
    get_tool_registry,
)
from agent_teams.interfaces.server.routers import prompts
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
                instructions=(
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
            version="1.0",
            tools=("dispatch_tasks",),
            mcp_servers=(),
            skills=("time",),
            model_profile="default",
            system_prompt="You are coordinator.",
        )
    )
    return registry


def _build_tool_registry() -> ToolRegistry:
    return ToolRegistry(tools={"dispatch_tasks": (lambda _agent: None)})


def _create_client() -> TestClient:
    app = FastAPI()
    app.include_router(prompts.router, prefix="/api")
    app.dependency_overrides[get_role_registry] = _build_role_registry
    app.dependency_overrides[get_tool_registry] = _build_tool_registry
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
            "tools": ["dispatch_tasks"],
            "skills": ["time"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["role_id"] == "coordinator_agent"
    assert payload["tools"] == ["dispatch_tasks"]
    assert payload["skills"] == ["time"]
    assert "## Runtime Contract" in payload["runtime_system_prompt"]
    assert "- priority: 1" in payload["runtime_system_prompt"]
    assert "## Tool Rules" in payload["provider_system_prompt"]
    assert "## Skill Instructions" in payload["provider_system_prompt"]
    assert payload["user_prompt"] == "Deliver summary"
    assert payload["tool_prompt"].startswith("## Tool Rules")
    assert payload["skill_prompt"].startswith("## Skill Instructions")


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
    assert payload["skills"] == ["planner"]
    assert "### Skill: planner" in payload["provider_system_prompt"]
    assert (
        "Break objectives into executable plans." in payload["provider_system_prompt"]
    )
    assert "### Skill: time" not in payload["provider_system_prompt"]


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
