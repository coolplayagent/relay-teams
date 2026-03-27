# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_teams.agents.execution import system_prompts
from agent_teams.interfaces.server.deps import (
    get_mcp_registry,
    get_role_registry,
    get_skill_registry,
    get_tool_registry,
    get_workspace_manager,
    get_workspace_service,
)
from agent_teams.interfaces.server.routers import prompts
from agent_teams.mcp.mcp_models import McpConfigScope, McpServerSpec, McpToolInfo
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.skills.skill_models import SkillInstructionEntry
from agent_teams.tools.registry import ToolRegistry
from agent_teams.workspace import (
    WorkspaceManager,
    WorkspaceRepository,
    WorkspaceService,
)


@pytest.fixture(autouse=True)
def _suppress_host_github_prompt_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        system_prompts,
        "_get_github_cli_environment_status",
        lambda: (False, None),
    )


class _FakeWorkspaceService:
    def __init__(self, known_workspace_ids: set[str]) -> None:
        self._known_workspace_ids = known_workspace_ids

    def require_workspace(self, workspace_id: str) -> None:
        if workspace_id not in self._known_workspace_ids:
            raise KeyError(workspace_id)


class _FakeWorkspaceHandle:
    def __init__(self, workdir: Path) -> None:
        self._workdir = workdir
        self.locations = SimpleNamespace(worktree_root=workdir)
        self.root_path = workdir

    def resolve_workdir(self) -> Path:
        return self._workdir


class _FakeWorkspaceManager:
    def resolve(
        self,
        *,
        session_id: str,
        role_id: str,
        instance_id: str | None,
        workspace_id: str,
        conversation_id: str | None = None,
        profile: object | None = None,
    ) -> _FakeWorkspaceHandle:
        _ = (session_id, role_id, instance_id, conversation_id, profile)
        return _FakeWorkspaceHandle(
            Path("/tmp") / workspace_id / "execution-root" / "preview"
        )


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
            McpToolInfo(name="docs_read_file", description="Read a file"),
            McpToolInfo(name="docs_search_docs", description="Search docs"),
        )


def _create_client() -> TestClient:
    app = FastAPI()
    app.include_router(prompts.router, prefix="/api")
    app.dependency_overrides[get_role_registry] = _build_role_registry
    app.dependency_overrides[get_tool_registry] = _build_tool_registry
    app.dependency_overrides[get_mcp_registry] = _FakeMcpRegistry
    app.dependency_overrides[get_skill_registry] = _FakeSkillRegistry
    app.dependency_overrides[get_workspace_service] = lambda: _FakeWorkspaceService(
        {"preview-workspace"}
    )
    app.dependency_overrides[get_workspace_manager] = lambda: _FakeWorkspaceManager()
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
    assert "## Runtime Rules" in payload["runtime_system_prompt"]
    assert "## Available Roles" in payload["runtime_system_prompt"]
    assert (
        "Use the dispatch prompt to pass stage-specific instructions and upstream context."
        in payload["runtime_system_prompt"]
    )
    assert (
        "If no existing role is a good fit, create a run-scoped role with `create_temporary_role` before dispatch."
        in payload["runtime_system_prompt"]
    )
    assert (
        "The roles listed below are dispatch targets, not your own capabilities."
        in payload["runtime_system_prompt"]
    )
    assert "### writer_agent" in payload["runtime_system_prompt"]
    assert "- Source: static" in payload["runtime_system_prompt"]
    assert (
        "- Description: Drafts release notes and summaries."
        in payload["runtime_system_prompt"]
    )
    assert "- Tools: dispatch_task" in payload["runtime_system_prompt"]
    assert (
        "- MCP Tools: docs_read_file, docs_search_docs"
        in payload["runtime_system_prompt"]
    )
    assert "- Skills: planner" in payload["runtime_system_prompt"]
    assert "priority" not in payload["runtime_system_prompt"]
    assert "## Available Skills" in payload["runtime_system_prompt"]
    assert "- time: Normalize all times to UTC." in payload["runtime_system_prompt"]
    assert payload["runtime_system_prompt"].index("## Available Skills") < payload[
        "runtime_system_prompt"
    ].index("## Runtime Environment Information")
    assert "## Available Skills" in payload["provider_system_prompt"]
    assert "- time: Normalize all times to UTC." in payload["provider_system_prompt"]
    assert payload["provider_system_prompt"].index("## Available Skills") < payload[
        "provider_system_prompt"
    ].index("## Runtime Environment Information")
    assert payload["user_prompt"] == "Deliver summary"
    assert "## Available Roles" in payload["provider_system_prompt"]


def test_prompts_preview_uses_workspace_execution_root_when_workspace_is_provided(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "preview-workspace" / "execution-root" / "preview"
    workspace_root.mkdir(parents=True)
    workspace_repo = WorkspaceRepository(tmp_path / "prompt_preview.db")
    workspace_service = WorkspaceService(repository=workspace_repo)
    _ = workspace_service.create_workspace(
        workspace_id="preview-workspace",
        root_path=workspace_root,
    )
    workspace_manager = WorkspaceManager(
        project_root=tmp_path,
        workspace_repo=workspace_repo,
    )

    app = FastAPI()
    app.include_router(prompts.router, prefix="/api")
    app.dependency_overrides[get_role_registry] = _build_role_registry
    app.dependency_overrides[get_tool_registry] = _build_tool_registry
    app.dependency_overrides[get_mcp_registry] = _FakeMcpRegistry
    app.dependency_overrides[get_skill_registry] = _FakeSkillRegistry
    app.dependency_overrides[get_workspace_service] = lambda: workspace_service
    app.dependency_overrides[get_workspace_manager] = lambda: workspace_manager
    client = TestClient(app)

    response = client.post(
        "/api/prompts:preview",
        json={
            "role_id": "writer_agent",
            "workspace_id": "preview-workspace",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert (
        f"- Working Directory: {workspace_root.resolve()}"
        in payload["runtime_system_prompt"]
    )


def test_prompts_preview_includes_project_instruction_files(tmp_path: Path) -> None:
    workspace_root = tmp_path / "preview-workspace" / "execution-root" / "preview"
    workspace_root.mkdir(parents=True)
    (workspace_root / "AGENTS.md").write_text(
        "Workspace instructions.",
        encoding="utf-8",
    )
    workspace_repo = WorkspaceRepository(tmp_path / "prompt_preview.db")
    workspace_service = WorkspaceService(repository=workspace_repo)
    _ = workspace_service.create_workspace(
        workspace_id="preview-workspace",
        root_path=workspace_root,
    )
    workspace_manager = WorkspaceManager(
        project_root=tmp_path,
        workspace_repo=workspace_repo,
    )

    app = FastAPI()
    app.include_router(prompts.router, prefix="/api")
    app.dependency_overrides[get_role_registry] = _build_role_registry
    app.dependency_overrides[get_tool_registry] = _build_tool_registry
    app.dependency_overrides[get_mcp_registry] = _FakeMcpRegistry
    app.dependency_overrides[get_skill_registry] = _FakeSkillRegistry
    app.dependency_overrides[get_workspace_service] = lambda: workspace_service
    app.dependency_overrides[get_workspace_manager] = lambda: workspace_manager
    client = TestClient(app)

    response = client.post(
        "/api/prompts:preview",
        json={
            "role_id": "writer_agent",
            "workspace_id": "preview-workspace",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "Workspace instructions." in payload["runtime_system_prompt"]


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
    assert (
        "- planner: Break objectives into executable plans."
        in payload["runtime_system_prompt"]
    )
    assert "- time: Normalize all times to UTC." not in payload["runtime_system_prompt"]
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


def test_prompts_preview_returns_404_for_unknown_workspace() -> None:
    client = _create_client()

    response = client.post(
        "/api/prompts:preview",
        json={
            "role_id": "coordinator_agent",
            "workspace_id": "missing-workspace",
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Workspace not found"}


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
