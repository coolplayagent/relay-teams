# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from relay_teams.computer import ExecutionSurface
from relay_teams.external_agents import ExternalAgentOption, ExternalAgentTransportType
from relay_teams.interfaces.server.deps import (
    get_external_agent_config_service,
    get_mcp_service,
    get_model_config_service,
    get_role_registry,
    get_role_settings_service,
    get_skills_config_reload_service,
    get_skill_registry,
    get_tool_registry,
)
from relay_teams.media import MediaModality
from relay_teams.interfaces.server.routers import roles
from relay_teams.mcp.mcp_models import McpConfigScope, McpServerSummary
from relay_teams.providers.model_config import ModelEndpointConfig, ProviderType
from relay_teams.roles import (
    NormalModeRoleOption,
    RoleConfigSource,
    RoleAgentOption,
    RoleConfigOptions,
    RoleDefinition,
    RoleDocumentRecord,
    RoleDocumentSummary,
    RoleMode,
    RoleRegistry,
    RoleSkillOption,
    RoleToolGroupOption,
    SystemRolesUnavailableError,
    RoleValidationResult,
)
from relay_teams.skills.skill_models import SkillOptionEntry, SkillSource
from relay_teams.roles import default_memory_profile


class _FakeRoleSettingsService:
    validate_all_error: Exception | None = None

    def list_role_documents(self) -> tuple[RoleDocumentSummary, ...]:
        return (
            RoleDocumentSummary(
                role_id="writer",
                name="Writer",
                description="Drafts user-facing content.",
                version="1.0.0",
                model_profile="default",
                mode=RoleMode.PRIMARY,
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
            tools=("orch_dispatch_task",),
            mcp_servers=(),
            skills=(),
            model_profile="default",
            mode=RoleMode.SUBAGENT,
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
        if self.validate_all_error is not None:
            raise self.validate_all_error
        return {"valid": True, "loaded_count": 1}

    def delete_role_document(self, role_id: str) -> None:
        if role_id == "missing":
            raise ValueError("Role not found: missing")
        if role_id != "writer":
            raise ValueError(f"Role cannot be deleted: {role_id}")


class _FakeToolRegistry:
    def list_names(self) -> tuple[str, ...]:
        return ("orch_create_tasks", "orch_dispatch_task")

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
    def __init__(
        self,
        options: tuple[SkillOptionEntry, ...] | None = None,
    ) -> None:
        self._options = options or (
            SkillOptionEntry(
                ref="diff",
                name="diff",
                description="Inspect file changes.",
                source=SkillSource.BUILTIN,
            ),
            SkillOptionEntry(
                ref="time",
                name="time",
                description="Read the current time.",
                source=SkillSource.USER_RELAY_TEAMS,
            ),
        )

    def list_skill_options(self) -> tuple[SkillOptionEntry, ...]:
        return self._options


class _FakeSkillsReloadService:
    def __init__(self, registry: _FakeSkillRegistry | None = None) -> None:
        self._registry = registry or _FakeSkillRegistry()
        self.reload_calls = 0

    def reload_skills_config(self) -> _FakeSkillRegistry:
        self.reload_calls += 1
        return self._registry


class _FakeExternalAgentService:
    def list_agent_options(self) -> tuple[ExternalAgentOption, ...]:
        return (
            ExternalAgentOption(
                agent_id="codex",
                name="Codex",
                transport=ExternalAgentTransportType.STDIO,
            ),
        )


class _FakeModelConfigService:
    def __init__(self) -> None:
        self.runtime = type(
            "_Runtime",
            (),
            {
                "llm_profiles": {
                    "default": ModelEndpointConfig(
                        provider=ProviderType.OPENAI_COMPATIBLE,
                        model="gpt-4.1-mini",
                        base_url="https://api.openai.com/v1",
                        api_key="test-key",
                    ),
                    "writer": ModelEndpointConfig(
                        provider=ProviderType.OPENAI_COMPATIBLE,
                        model="gpt-4.1-nano",
                        base_url="https://api.openai.com/v1",
                        api_key="test-key",
                    ),
                },
                "default_model_profile": "default",
            },
        )()


def _create_test_client(
    *,
    registry: RoleRegistry | None = None,
    service: _FakeRoleSettingsService | None = None,
    skill_registry: _FakeSkillRegistry | None = None,
    skills_reload_service: _FakeSkillsReloadService | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(roles.router, prefix="/api")
    resolved_registry = registry or RoleRegistry()
    if registry is None:
        resolved_registry.register(
            RoleDefinition(
                role_id="Coordinator",
                name="Coordinator",
                description="Coordinates the run.",
                version="1.0.0",
                tools=("orch_dispatch_task",),
                model_profile="default",
                mode=RoleMode.PRIMARY,
                system_prompt="Coordinate the run.",
            )
        )
        resolved_registry.register(
            RoleDefinition(
                role_id="MainAgent",
                name="Main Agent",
                description="Executes normal-mode runs.",
                version="1.0.0",
                tools=("orch_dispatch_task",),
                model_profile="default",
                mode=RoleMode.PRIMARY,
                system_prompt="Handle the run directly.",
            )
        )
        resolved_registry.register(
            RoleDefinition(
                role_id="writer",
                name="Writer",
                description="Drafts user-facing content.",
                version="1.0.0",
                tools=("orch_dispatch_task",),
                model_profile="default",
                mode=RoleMode.SUBAGENT,
                system_prompt="Write clearly.",
            )
        )
    resolved_service = service or _FakeRoleSettingsService()
    resolved_skill_registry = skill_registry or _FakeSkillRegistry()
    resolved_skills_reload_service = skills_reload_service or _FakeSkillsReloadService(
        resolved_skill_registry
    )
    app.dependency_overrides[get_role_registry] = lambda: resolved_registry
    app.dependency_overrides[get_role_settings_service] = lambda: resolved_service
    app.dependency_overrides[get_tool_registry] = lambda: _FakeToolRegistry()
    app.dependency_overrides[get_mcp_service] = lambda: _FakeMcpService()
    app.dependency_overrides[get_model_config_service] = lambda: (
        _FakeModelConfigService()
    )
    app.dependency_overrides[get_skill_registry] = lambda: resolved_skill_registry
    app.dependency_overrides[get_skills_config_reload_service] = lambda: (
        resolved_skills_reload_service
    )
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
            "mode": "primary",
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
    assert payload["mode"] == "subagent"


def test_validate_role_config() -> None:
    client = _create_test_client()

    response = client.post(
        "/api/roles:validate-config",
        json={
            "role_id": "writer",
            "name": "Writer",
            "description": "Drafts user-facing content.",
            "version": "1.0.0",
            "tools": ["orch_dispatch_task"],
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


def test_role_config_routes_run_service_calls_in_threadpool(monkeypatch) -> None:
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def fake_run_in_threadpool(
        func: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        calls.append((func.__name__, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(roles, "call_maybe_async", fake_run_in_threadpool)
    client = _create_test_client()
    role_payload = {
        "role_id": "writer",
        "name": "Writer",
        "description": "Drafts user-facing content.",
        "version": "1.0.0",
        "tools": ["orch_dispatch_task"],
        "mcp_servers": [],
        "skills": [],
        "model_profile": "default",
        "memory_profile": default_memory_profile().model_dump(mode="json"),
        "system_prompt": "Write clearly.",
    }

    responses = [
        client.get("/api/roles/configs"),
        client.get("/api/roles/configs/writer"),
        client.put("/api/roles/configs/writer", json=role_payload),
        client.delete("/api/roles/configs/writer"),
        client.post("/api/roles:validate"),
        client.post("/api/roles:validate-config", json=role_payload),
    ]

    assert [response.status_code for response in responses] == [200] * len(responses)
    assert [call[0] for call in calls] == [
        "list_role_documents",
        "get_role_document",
        "save_role_document",
        "delete_role_document",
        "validate_all_roles",
        "validate_role_document",
    ]


def test_get_role_config_options_runs_in_threadpool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def fake_run_in_threadpool(
        func: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        calls.append((func.__name__, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(roles, "call_maybe_async", fake_run_in_threadpool)
    client = _create_test_client()

    response = client.get("/api/roles:options")

    assert response.status_code == 200
    assert [call[0] for call in calls] == ["_build_role_config_options"]


def test_get_role_config_options_returns_503_when_system_roles_are_missing() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="writer",
            name="Writer",
            description="Drafts user-facing content.",
            version="1.0.0",
            tools=("orch_dispatch_task",),
            model_profile="default",
            system_prompt="Write clearly.",
        )
    )
    client = _create_test_client(registry=registry)

    response = client.get("/api/roles:options")

    assert response.status_code == 503
    assert "Required system roles are unavailable" in response.json()["detail"]


def test_validate_roles_returns_503_when_system_roles_are_missing() -> None:
    service = _FakeRoleSettingsService()
    service.validate_all_error = SystemRolesUnavailableError(
        "Required system roles are unavailable: main_agent: missing"
    )
    client = _create_test_client(service=service)

    response = client.post("/api/roles:validate")

    assert response.status_code == 503
    assert "Required system roles are unavailable" in response.json()["detail"]


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
        coordinator_role=NormalModeRoleOption(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates the run.",
            model_profile="default",
            model_name="gpt-4.1-mini",
            input_modalities=(MediaModality.IMAGE,),
        ),
        main_agent_role=NormalModeRoleOption(
            role_id="MainAgent",
            name="Main Agent",
            description="Executes normal-mode runs.",
            model_profile="default",
            model_name="gpt-4.1-mini",
            input_modalities=(MediaModality.IMAGE,),
        ),
        normal_mode_roles=(
            NormalModeRoleOption(
                role_id="MainAgent",
                name="Main Agent",
                description="Executes normal-mode runs.",
                model_profile="default",
                model_name="gpt-4.1-mini",
                input_modalities=(MediaModality.IMAGE,),
            ),
        ),
        subagent_roles=(
            NormalModeRoleOption(
                role_id="writer",
                name="Writer",
                description="Drafts user-facing content.",
                model_profile="default",
                model_name="gpt-4.1-mini",
                input_modalities=(MediaModality.IMAGE,),
            ),
        ),
        tool_groups=(
            RoleToolGroupOption(
                id="orchestration",
                name="Orchestration",
                description="Coordinator-only orchestration tools for delegated task management.",
                tools=("orch_create_tasks", "orch_dispatch_task"),
            ),
        ),
        tools=("orch_create_tasks", "orch_dispatch_task"),
        mcp_servers=("docs",),
        skills=(
            RoleSkillOption(
                ref="diff",
                name="diff",
                description="Inspect file changes.",
                source="builtin",
            ),
            RoleSkillOption(
                ref="time",
                name="time",
                description="Read the current time.",
                source="user_relay_teams",
            ),
        ),
        agents=(
            RoleAgentOption(
                agent_id="codex",
                name="Codex",
                transport="stdio",
            ),
        ),
        role_modes=tuple(mode for mode in RoleMode),
        execution_surfaces=tuple(surface for surface in ExecutionSurface),
    ).model_dump(mode="json")


def test_get_role_config_options_reloads_missing_builtin_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        roles,
        "_collect_builtin_reserved_role_skill_names",
        lambda: frozenset({"skill-installer"}),
    )
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates the run.",
            version="1.0.0",
            tools=("orch_dispatch_task",),
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
            tools=("orch_dispatch_task",),
            skills=("skill-installer",),
            model_profile="default",
            system_prompt="Handle the run directly.",
        )
    )
    skill_registry = _FakeSkillRegistry(
        (
            SkillOptionEntry(
                ref="time",
                name="time",
                description="Read the current time.",
                source=SkillSource.USER_RELAY_TEAMS,
            ),
        )
    )
    reloaded_registry = _FakeSkillRegistry(
        (
            SkillOptionEntry(
                ref="skill-installer",
                name="skill-installer",
                description="Install skills.",
                source=SkillSource.BUILTIN,
            ),
        )
    )
    reload_service = _FakeSkillsReloadService(reloaded_registry)
    client = _create_test_client(
        registry=registry,
        skill_registry=skill_registry,
        skills_reload_service=reload_service,
    )

    response = client.get("/api/roles:options")

    assert response.status_code == 200
    payload = response.json()
    assert payload["skills"] == [
        {
            "ref": "skill-installer",
            "name": "skill-installer",
            "description": "Install skills.",
            "source": "builtin",
        }
    ]
    assert reload_service.reload_calls == 1


def test_get_role_config_options_returns_503_when_builtin_skills_still_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        roles,
        "_collect_builtin_reserved_role_skill_names",
        lambda: frozenset({"skill-installer"}),
    )
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates the run.",
            version="1.0.0",
            tools=("orch_dispatch_task",),
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
            tools=("orch_dispatch_task",),
            skills=("skill-installer",),
            model_profile="default",
            system_prompt="Handle the run directly.",
        )
    )
    skill_registry = _FakeSkillRegistry(
        (
            SkillOptionEntry(
                ref="time",
                name="time",
                description="Read the current time.",
                source=SkillSource.USER_RELAY_TEAMS,
            ),
        )
    )
    reload_service = _FakeSkillsReloadService(skill_registry)
    client = _create_test_client(
        registry=registry,
        skill_registry=skill_registry,
        skills_reload_service=reload_service,
    )

    response = client.get("/api/roles:options")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Builtin skills are unavailable: ['skill-installer']"
    }
    assert reload_service.reload_calls == 1


def test_get_role_config_options_ignores_reserved_role_skill_wildcard() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates the run.",
            version="1.0.0",
            tools=("orch_dispatch_task",),
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
            tools=("orch_dispatch_task",),
            skills=("*",),
            model_profile="default",
            system_prompt="Handle the run directly.",
        )
    )
    skill_registry = _FakeSkillRegistry(
        (
            SkillOptionEntry(
                ref="time",
                name="time",
                description="Read the current time.",
                source=SkillSource.USER_RELAY_TEAMS,
            ),
        )
    )
    reload_service = _FakeSkillsReloadService(skill_registry)
    client = _create_test_client(
        registry=registry,
        skill_registry=skill_registry,
        skills_reload_service=reload_service,
    )

    response = client.get("/api/roles:options")

    assert response.status_code == 200
    assert response.json()["skills"] == [
        {
            "ref": "time",
            "name": "time",
            "description": "Read the current time.",
            "source": "user_relay_teams",
        }
    ]
    assert reload_service.reload_calls == 0


def test_get_role_config_options_ignores_missing_non_system_role_skills() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates the run.",
            version="1.0.0",
            tools=("orch_dispatch_task",),
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
            tools=("orch_dispatch_task",),
            model_profile="default",
            system_prompt="Handle the run directly.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="Writer",
            name="Writer",
            description="Drafts user-facing content.",
            version="1.0.0",
            tools=("orch_dispatch_task",),
            skills=("project-planner",),
            model_profile="default",
            system_prompt="Write clearly.",
        )
    )
    skill_registry = _FakeSkillRegistry(
        (
            SkillOptionEntry(
                ref="time",
                name="time",
                description="Read the current time.",
                source=SkillSource.USER_RELAY_TEAMS,
            ),
        )
    )
    reload_service = _FakeSkillsReloadService(skill_registry)
    client = _create_test_client(
        registry=registry,
        skill_registry=skill_registry,
        skills_reload_service=reload_service,
    )

    response = client.get("/api/roles:options")

    assert response.status_code == 200
    assert response.json()["skills"] == [
        {
            "ref": "time",
            "name": "time",
            "description": "Read the current time.",
            "source": "user_relay_teams",
        }
    ]
    assert reload_service.reload_calls == 0


def test_get_role_config_options_ignores_missing_custom_reserved_role_skills() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates the run.",
            version="1.0.0",
            tools=("orch_dispatch_task",),
            skills=("project-planner",),
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
            tools=("orch_dispatch_task",),
            model_profile="default",
            system_prompt="Handle the run directly.",
        )
    )
    skill_registry = _FakeSkillRegistry(
        (
            SkillOptionEntry(
                ref="time",
                name="time",
                description="Read the current time.",
                source=SkillSource.USER_RELAY_TEAMS,
            ),
        )
    )
    reload_service = _FakeSkillsReloadService(skill_registry)
    client = _create_test_client(
        registry=registry,
        skill_registry=skill_registry,
        skills_reload_service=reload_service,
    )

    response = client.get("/api/roles:options")

    assert response.status_code == 200
    assert response.json()["skills"] == [
        {
            "ref": "time",
            "name": "time",
            "description": "Read the current time.",
            "source": "user_relay_teams",
        }
    ]
    assert reload_service.reload_calls == 0


def test_get_role_config_options_uses_available_project_skill_without_reload() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates the run.",
            version="1.0.0",
            tools=("orch_dispatch_task",),
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
            tools=("orch_dispatch_task",),
            skills=("time",),
            model_profile="default",
            system_prompt="Handle the run directly.",
        )
    )
    skill_registry = _FakeSkillRegistry(
        (
            SkillOptionEntry(
                ref="time",
                name="time",
                description="Read the current time from the project override.",
                source=SkillSource.PROJECT_AGENTS,
            ),
        )
    )
    reload_service = _FakeSkillsReloadService(skill_registry)
    client = _create_test_client(
        registry=registry,
        skill_registry=skill_registry,
        skills_reload_service=reload_service,
    )

    response = client.get("/api/roles:options")

    assert response.status_code == 200
    assert response.json()["skills"] == [
        {
            "ref": "time",
            "name": "time",
            "description": "Read the current time from the project override.",
            "source": "project_agents",
        }
    ]
    assert reload_service.reload_calls == 0


def test_get_role_config_options_recomputes_required_builtin_skills_after_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        roles,
        "_collect_builtin_reserved_role_skill_names",
        lambda: frozenset({"skill-installer"}),
    )
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates the run.",
            version="1.0.0",
            tools=("orch_dispatch_task",),
            skills=("skill-installer", "project-planner"),
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
            tools=("orch_dispatch_task",),
            model_profile="default",
            system_prompt="Handle the run directly.",
        )
    )
    skill_registry = _FakeSkillRegistry(
        (
            SkillOptionEntry(
                ref="project-planner",
                name="project-planner",
                description="Temporarily exposed as builtin before reload.",
                source=SkillSource.BUILTIN,
            ),
        )
    )
    reload_service = _FakeSkillsReloadService(
        _FakeSkillRegistry(
            (
                SkillOptionEntry(
                    ref="skill-installer",
                    name="skill-installer",
                    description="Reload restored the required builtin skill.",
                    source=SkillSource.BUILTIN,
                ),
            )
        )
    )
    client = _create_test_client(
        registry=registry,
        skill_registry=skill_registry,
        skills_reload_service=reload_service,
    )

    response = client.get("/api/roles:options")

    assert response.status_code == 200
    assert response.json()["skills"] == [
        {
            "ref": "skill-installer",
            "name": "skill-installer",
            "description": "Reload restored the required builtin skill.",
            "source": "builtin",
        }
    ]
    assert reload_service.reload_calls == 1
