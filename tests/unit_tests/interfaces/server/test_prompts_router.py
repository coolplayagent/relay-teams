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
    get_skill_runtime_service,
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
from agent_teams.skills.skill_routing_models import (
    SkillPromptResult,
    SkillRoutingDiagnostics,
    SkillRoutingMode,
    SkillRoutingResult,
)
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
        self._known = {
            "time": "builtin:time",
            "planner": "builtin:planner",
        }

    def validate_known(self, skill_names: tuple[str, ...]) -> None:
        unknown = [
            name
            for name in skill_names
            if name not in self._known and name not in self._known.values()
        ]
        if unknown:
            raise ValueError(f"Unknown skills: {unknown}")

    def resolve_known(
        self,
        skill_names: tuple[str, ...],
        *,
        strict: bool = True,
        consumer: str | None = None,
    ) -> tuple[str, ...]:
        _ = consumer
        resolved: list[str] = []
        unknown: list[str] = []
        for name in skill_names:
            if name in self._known.values():
                resolved.append(name)
                continue
            ref = self._known.get(name)
            if ref is None:
                unknown.append(name)
                continue
            resolved.append(ref)
        if strict and unknown:
            raise ValueError(f"Unknown skills: {unknown}")
        return tuple(resolved)

    def get_instruction_entries(
        self, skill_names: tuple[str, ...]
    ) -> tuple[SkillInstructionEntry, ...]:
        resolved_names = self.resolve_known(skill_names)
        return tuple(
            SkillInstructionEntry(
                name=resolved_name.removeprefix("builtin:"),
                description=(
                    "Normalize all times to UTC."
                    if resolved_name.endswith(":time")
                    else "Break objectives into executable plans."
                ),
            )
            for resolved_name in resolved_names
        )


class _FakeSkillRuntimeService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def prepare_prompt(
        self,
        *,
        role: RoleDefinition,
        objective: str,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        conversation_context: object | None,
        orchestration_prompt: str = "",
        skill_names: tuple[str, ...] | None = None,
        consumer: str,
    ) -> SkillPromptResult:
        _ = conversation_context
        resolved_skills = role.skills if skill_names is None else skill_names
        visible_skills = tuple(
            name.removeprefix("builtin:").removeprefix("app:")
            for name in resolved_skills
        )
        self.calls.append(
            {
                "role_id": role.role_id,
                "objective": objective,
                "shared_state_snapshot": shared_state_snapshot,
                "orchestration_prompt": orchestration_prompt,
                "skill_names": resolved_skills,
                "consumer": consumer,
            }
        )
        descriptions = {
            "time": "Normalize all times to UTC.",
            "planner": "Break objectives into executable plans.",
        }
        user_prompt = objective.strip()
        system_prompt_skill_instructions: tuple[SkillInstructionEntry, ...] = ()
        mode = SkillRoutingMode.PASSTHROUGH
        if len(visible_skills) <= 8:
            system_prompt_skill_instructions = tuple(
                SkillInstructionEntry(
                    name=name,
                    description=descriptions[name],
                )
                for name in visible_skills
            )
        elif user_prompt and visible_skills:
            mode = SkillRoutingMode.SEARCH
            user_prompt = (
                user_prompt
                + "\n\n## Skill Candidates\n"
                + "\n".join(
                    f"- {name}: {descriptions[name]}" for name in visible_skills
                )
            )
        return SkillPromptResult(
            user_prompt=user_prompt,
            system_prompt_skill_instructions=system_prompt_skill_instructions,
            routing=SkillRoutingResult(
                authorized_skills=visible_skills,
                visible_skills=visible_skills,
                diagnostics=SkillRoutingDiagnostics(
                    mode=mode,
                    query_text=(
                        ""
                        if not objective.strip()
                        else f"Objective: {objective.strip()}"
                    ),
                    authorized_count=len(visible_skills),
                    visible_skills=visible_skills,
                ),
            ),
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


def _create_client(
    *,
    skill_runtime_service: _FakeSkillRuntimeService | None = None,
) -> TestClient:
    resolved_skill_runtime_service = (
        _FakeSkillRuntimeService()
        if skill_runtime_service is None
        else skill_runtime_service
    )
    app = FastAPI()
    app.include_router(prompts.router, prefix="/api")
    app.dependency_overrides[get_role_registry] = _build_role_registry
    app.dependency_overrides[get_tool_registry] = _build_tool_registry
    app.dependency_overrides[get_mcp_registry] = _FakeMcpRegistry
    app.dependency_overrides[get_skill_registry] = _FakeSkillRegistry
    app.dependency_overrides[get_skill_runtime_service] = lambda: (
        resolved_skill_runtime_service
    )
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
    assert "## Available Skills" in payload["provider_system_prompt"]
    assert payload["provider_system_prompt"] == payload["runtime_system_prompt"]
    assert payload["user_prompt"] == "Deliver summary"
    assert payload["skill_routing"]["mode"] == "passthrough"
    assert payload["skill_routing"]["visible_skills"] == ["time"]
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
    app.dependency_overrides[get_skill_runtime_service] = _FakeSkillRuntimeService
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
    app.dependency_overrides[get_skill_runtime_service] = _FakeSkillRuntimeService
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
    assert payload["user_prompt"] == ""
    assert payload["skill_routing"]["visible_skills"] == ["planner"]
    assert "## Available Skills" in payload["runtime_system_prompt"]


def test_prompts_preview_passes_orchestration_prompt_to_skill_runtime_service() -> None:
    fake_skill_runtime_service = _FakeSkillRuntimeService()
    client = _create_client(skill_runtime_service=fake_skill_runtime_service)

    response = client.post(
        "/api/prompts:preview",
        json={
            "role_id": "coordinator_agent",
            "objective": "Deliver summary",
            "orchestration_prompt": "Delegate by capability and finalize yourself.",
        },
    )

    assert response.status_code == 200
    assert len(fake_skill_runtime_service.calls) == 1
    assert (
        fake_skill_runtime_service.calls[0]["orchestration_prompt"]
        == "Delegate by capability and finalize yourself."
    )


def test_prompts_preview_system_prompt_is_stable_across_objectives() -> None:
    client = _create_client()

    first = client.post(
        "/api/prompts:preview",
        json={
            "role_id": "coordinator_agent",
            "objective": "Deliver summary",
        },
    )
    second = client.post(
        "/api/prompts:preview",
        json={
            "role_id": "coordinator_agent",
            "objective": "Investigate timezone drift",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert (
        first_payload["runtime_system_prompt"]
        == second_payload["runtime_system_prompt"]
    )
    assert (
        first_payload["provider_system_prompt"]
        == second_payload["provider_system_prompt"]
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
