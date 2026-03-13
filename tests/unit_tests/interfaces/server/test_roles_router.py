# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_teams.interfaces.server.deps import (
    get_mcp_service,
    get_role_registry,
    get_role_settings_service,
    get_skill_registry,
    get_tool_registry,
)
from agent_teams.interfaces.server.routers import roles
from agent_teams.mcp.models import McpConfigScope, McpServerSummary
from agent_teams.roles import (
    RoleConfigSource,
    RoleConfigOptions,
    RoleDefinition,
    RoleDocumentRecord,
    RoleDocumentSummary,
    RoleRegistry,
    RoleValidationResult,
)
from agent_teams.workspace import default_workspace_profile


class _FakeRoleSettingsService:
    def list_role_documents(self) -> tuple[RoleDocumentSummary, ...]:
        return (
            RoleDocumentSummary(
                role_id="writer",
                name="Writer",
                version="1.0.0",
                model_profile="default",
                source=RoleConfigSource.APP,
            ),
        )

    def get_role_document(self, role_id: str) -> RoleDocumentRecord:
        if role_id != "writer":
            raise ValueError("Role not found: missing")
        return RoleDocumentRecord(
            source_role_id=None,
            role_id="writer",
            name="Writer",
            version="1.0.0",
            tools=("list_available_roles",),
            mcp_servers=(),
            skills=(),
            model_profile="default",
            workspace_profile=default_workspace_profile(),
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


class _FakeToolRegistry:
    def list_names(self) -> tuple[str, ...]:
        return ("dispatch_task", "list_available_roles")


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
    def list_names(self) -> tuple[str, ...]:
        return ("diff", "time")


def _create_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(roles.router, prefix="/api")
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            version="1.0.0",
            tools=("dispatch_task",),
            model_profile="default",
            system_prompt="Coordinate the run.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="writer",
            name="Writer",
            version="1.0.0",
            tools=("list_available_roles",),
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
            "version": "1.0.0",
            "model_profile": "default",
            "source": "app",
        }
    ]


def test_get_role_config() -> None:
    client = _create_test_client()

    response = client.get("/api/roles/configs/writer")

    assert response.status_code == 200
    payload = response.json()
    assert payload["role_id"] == "writer"
    assert payload["file_name"] == "writer.md"


def test_validate_role_config() -> None:
    client = _create_test_client()

    response = client.post(
        "/api/roles:validate-config",
        json={
            "role_id": "writer",
            "name": "Writer",
            "version": "1.0.0",
            "tools": ["list_available_roles"],
            "mcp_servers": [],
            "skills": [],
            "model_profile": "default",
            "workspace_profile": default_workspace_profile().model_dump(mode="json"),
            "system_prompt": "Write clearly.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["role"]["role_id"] == "writer"


def test_get_role_config_options() -> None:
    client = _create_test_client()

    response = client.get("/api/roles:options")

    assert response.status_code == 200
    assert response.json() == RoleConfigOptions(
        coordinator_role_id="Coordinator",
        tools=("dispatch_task", "list_available_roles"),
        mcp_servers=("docs",),
        skills=("diff", "time"),
        workspace_bindings=("session", "role", "instance", "task"),
    ).model_dump(mode="json")
