# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_teams.computer import ExecutionSurface
from agent_teams.external_agents import ExternalAgentOption, ExternalAgentTransportType
from agent_teams.interfaces.server.deps import (
    get_external_agent_config_service,
    get_mcp_service,
    get_role_registry,
    get_role_settings_service,
    get_skill_registry,
    get_tool_registry,
)
from agent_teams.interfaces.server.routers import roles
from agent_teams.mcp.mcp_models import McpConfigScope, McpServerSummary
from agent_teams.roles import (
    NormalModeRoleOption,
    RoleConfigSource,
    RoleAgentOption,
    RoleConfigOptions,
    RoleDefinition,
    RoleDocumentRecord,
    RoleDocumentSummary,
    RoleRegistry,
    RoleSkillOption,
    RoleValidationResult,
)
from agent_teams.skills.skill_models import SkillOptionEntry, SkillScope
from agent_teams.roles import default_memory_profile


class _FakeRoleSettingsService:
    def list_role_documents(self) -> tuple[RoleDocumentSummary, ...]:
        return (
            RoleDocumentSummary(
                role_id="writer",
                name="Writer",
                description="Drafts user-facing content.",
                version="1.0.0",
                model_profile="default",
                source=RoleConfigSource.APP,
                deletable=True,
            ),
        )

    def get_role_document(self, role_id: str) -> RoleDocumentRecord:
        if role_id != "writer":
            raise ValueError("Role not found: missing")
        return RoleDocumentRecord(
            source_role_id=None,
            role_id="writer",
            name="Writer",
            description="Drafts user-facing content.",
            version="1.0.0",
            tools=("dispatch_task",),
            mcp_servers=(),
            skills=(),
            model_profile="default",
            memory_profile=default_memory_profile(),
            system_prompt="Write clearly.",
            source=RoleConfigSource.APP,
            file_name="writer.md",
            content="---\nrole_id: writer\n---\n\nWrite clearly.\n",
        )

    def save_role_document(
        self,
        role_id: str,
        draft: object,
    ) -> RoleDocumentRecord:
        _ = draft
        return self.get_role_document(role_id)

    def validate_role_document(
        self,
        draft: object,
    ) -> RoleValidationResult:
        _ = draft
        return RoleValidationResult(valid=True, role=self.get_role_document("writer"))

    def validate_all_roles(self) -> dict[str, int | bool]:
        return {"valid": True, "loaded_count": 1}

    def delete_role_document(self, role_id: str) -> None:
        if role_id == "missing":
            raise ValueError("Role not found: missing")
        if role_id != "writer":
            raise ValueError(f"Role cannot be deleted: {role_id}")


class _FakeToolRegistry:
    def list_names(self) -> tuple[str, ...]:
        return ("create_tasks", "dispatch_task")

    def list_configurable_names(self) -> tuple[str, ...]:
        return self.list_names()


class _FakeMcpService:
    def list_servers(self) -> tuple[McpServerSummary, ...]:
        return (
            McpServerSummary(
                name="docs",
                source=McpConfigScope.APP,
                transport="stdio",
            ),
        )


class _FakeSkillRegistry:
    def list_skill_options(self) -> tuple[SkillOptionEntry, ...]:
        return (
            SkillOptionEntry(
                ref="builtin:diff",
                name="diff",
                description="Inspect file changes.",
                scope=SkillScope.BUILTIN,
            ),
            SkillOptionEntry(
                ref="app:time",
                name="time",
                description="Read the current time.",
                scope=SkillScope.APP,
            ),
        )


class _FakeExternalAgentService:
    def list_agent_options(self) -> tuple[ExternalAgentOption, ...]:
        return (
            ExternalAgentOption(
                agent_id="codex",
                name="Codex",
                transport=ExternalAgentTransportType.STDIO,
            ),
        )


def _create_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(roles.router, prefix="/api")
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates the run.",
            version="1.0.0",
            tools=("dispatch_task",),
            model_profile="default",
            system_prompt="Coordinate the run.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="Executes normal-mode runs.",
            version="1.0.0",
            tools=("dispatch_task",),
            model_profile="default",
            system_prompt="Handle the run directly.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="writer",
            name="Writer",
            description="Drafts user-facing content.",
            version="1.0.0",
            tools=("dispatch_task",),
            model_profile="default",
            system_prompt="Write clearly.",
        )
    )
    app.dependency_overrides[get_role_registry] = lambda: registry
    app.dependency_overrides[get_role_settings_service] = lambda: (
        _FakeRoleSettingsService()
    )
    app.dependency_overrides[get_tool_registry] = lambda: _FakeToolRegistry()
    app.dependency_overrides[get_mcp_service] = lambda: _FakeMcpService()
    app.dependency_overrides[get_skill_registry] = lambda: _FakeSkillRegistry()
    app.dependency_overrides[get_external_agent_config_service] = lambda: (
        _FakeExternalAgentService()
    )
    return TestClient(app)


def test_list_role_configs() -> None:
    client = _create_test_client()

    response = client.get("/api/roles/configs")

    assert response.status_code == 200
    payload = response.json()
    assert payload == [
        {
            "role_id": "writer",
            "name": "Writer",
            "description": "Drafts user-facing content.",
            "version": "1.0.0",
            "model_profile": "default",
            "execution_surface": "api",
            "source": "app",
            "deletable": True,
        }
    ]


def test_get_role_config() -> None:
    client = _create_test_client()

    response = client.get("/api/roles/configs/writer")

    assert response.status_code == 200
    payload = response.json()
    assert payload["role_id"] == "writer"
    assert payload["file_name"] == "writer.md"
    assert payload["execution_surface"] == "api"


def test_validate_role_config() -> None:
    client = _create_test_client()

    response = client.post(
        "/api/roles:validate-config",
        json={
            "role_id": "writer",
            "name": "Writer",
            "description": "Drafts user-facing content.",
            "version": "1.0.0",
            "tools": ["dispatch_task"],
            "mcp_servers": [],
            "skills": [],
            "model_profile": "default",
            "memory_profile": default_memory_profile().model_dump(mode="json"),
            "system_prompt": "Write clearly.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["role"]["role_id"] == "writer"


def test_delete_role_config() -> None:
    client = _create_test_client()

    response = client.delete("/api/roles/configs/writer")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_delete_role_config_returns_not_found() -> None:
    client = _create_test_client()

    response = client.delete("/api/roles/configs/missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "Role not found: missing"}


def test_delete_role_config_rejects_builtin_role() -> None:
    client = _create_test_client()

    response = client.delete("/api/roles/configs/MainAgent")

    assert response.status_code == 400
    assert response.json() == {"detail": "Role cannot be deleted: MainAgent"}


def test_get_role_config_options() -> None:
    client = _create_test_client()

    response = client.get("/api/roles:options")

    assert response.status_code == 200
    assert response.json() == RoleConfigOptions(
        coordinator_role_id="Coordinator",
        main_agent_role_id="MainAgent",
        normal_mode_roles=(
            NormalModeRoleOption(
                role_id="MainAgent",
                name="Main Agent",
                description="Executes normal-mode runs.",
            ),
            NormalModeRoleOption(
                role_id="writer",
                name="Writer",
                description="Drafts user-facing content.",
            ),
        ),
        tools=("create_tasks", "dispatch_task"),
        mcp_servers=("docs",),
        skills=(
            RoleSkillOption(
                ref="builtin:diff",
                name="diff",
                description="Inspect file changes.",
                scope=SkillScope.BUILTIN,
            ),
            RoleSkillOption(
                ref="app:time",
                name="time",
                description="Read the current time.",
                scope=SkillScope.APP,
            ),
        ),
        agents=(
            RoleAgentOption(
                agent_id="codex",
                name="Codex",
                transport="stdio",
            ),
        ),
        execution_surfaces=tuple(surface for surface in ExecutionSurface),
    ).model_dump(mode="json")
