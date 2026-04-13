# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, JsonValue

from relay_teams.env.clawhub_config_models import ClawHubConfig
from relay_teams.env.clawhub_config_service import ClawHubConfigService
from relay_teams.env.clawhub_connectivity import (
    ClawHubConnectivityProbeRequest,
    ClawHubConnectivityProbeResult,
)
from relay_teams.env.environment_variable_models import (
    EnvironmentVariableCatalog,
    EnvironmentVariableRecord,
    EnvironmentVariableSaveRequest,
    EnvironmentVariableScope,
)
from relay_teams.env.environment_variable_service import EnvironmentVariableService
from relay_teams.env.github_config_models import GitHubConfig
from relay_teams.env.github_config_service import GitHubConfigService
from relay_teams.env.github_connectivity import (
    GitHubConnectivityProbeRequest,
    GitHubConnectivityProbeResult,
)
from relay_teams.external_agents import (
    ExternalAgentConfig,
    ExternalAgentConfigService,
    ExternalAgentSummary,
    ExternalAgentTestResult,
)
from relay_teams.external_agents.acp_client import probe_acp_agent
from relay_teams.env.proxy_config_service import ProxyConfigService
from relay_teams.env.proxy_env import ProxyEnvInput
from relay_teams.env.web_config_models import WebConfig
from relay_teams.env.web_config_service import WebConfigService
from relay_teams.env.web_connectivity import (
    WebConnectivityProbeRequest,
    WebConnectivityProbeResult,
)
from relay_teams.interfaces.server.deps import (
    get_clawhub_config_service,
    get_clawhub_install_service,
    get_clawhub_search_service,
    get_clawhub_skill_service,
    get_config_status_service,
    get_environment_variable_service,
    get_external_agent_config_service,
    get_github_config_service,
    get_mcp_config_reload_service,
    get_model_config_service,
    get_notification_settings_service,
    get_orchestration_settings_service,
    get_proxy_config_service,
    get_skills_config_reload_service,
    get_ui_language_settings_service,
    get_web_config_service,
)
from relay_teams.interfaces.server.ui_language_models import UiLanguageSettings
from relay_teams.interfaces.server.ui_language_service import UiLanguageSettingsService
from relay_teams.agents.orchestration.settings_models import OrchestrationSettings
from relay_teams.agents.orchestration.settings_service import (
    OrchestrationSettingsService,
)
from relay_teams.interfaces.server.config_status_service import ConfigStatusService
from relay_teams.interfaces.server.runtime_identity import (
    ServerHealthPayload,
    build_server_health_payload,
)
from relay_teams.mcp.config_reload_service import McpConfigReloadService
from relay_teams.notifications.models import NotificationConfig
from relay_teams.notifications.notification_settings_service import (
    NotificationSettingsService,
)
from relay_teams.providers.model_config import (
    DEFAULT_MAAS_BASE_URL,
    ModelConfigPayload,
    ModelProfileConfigPayload,
    ProviderType,
)
from relay_teams.providers.model_config_service import ModelConfigService
from relay_teams.providers.model_connectivity import (
    ModelDiscoveryRequest,
    ModelDiscoveryResult,
    ModelConnectivityProbeRequest,
    ModelConnectivityProbeResult,
)
from relay_teams.skills.config_reload_service import SkillsConfigReloadService
from relay_teams.skills.clawhub_install_service import ClawHubSkillInstallService
from relay_teams.skills.clawhub_models import (
    ClawHubSkillSearchRequest,
    ClawHubSkillSearchResult,
    ClawHubSkillInstallRequest,
    ClawHubSkillInstallResult,
    ClawHubSkillDetail,
    ClawHubSkillSummary,
    ClawHubSkillWriteRequest,
)
from relay_teams.skills.clawhub_search_service import ClawHubSkillSearchService
from relay_teams.skills.clawhub_skill_service import ClawHubSkillService
from relay_teams.validation import RequiredIdentifierStr

router = APIRouter(prefix="/system", tags=["System"])


class NotificationConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: NotificationConfig


class ModelConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: ModelConfigPayload


class OrchestrationConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: OrchestrationSettings


def _raise_system_http_error(
    exc: Exception,
    *,
    key_error_status: int | None = None,
    key_error_detail: str | None = None,
    permission_error_status: int | None = None,
    value_error_status: int | None = None,
    runtime_error_status: int | None = None,
    os_error_status: int | None = None,
) -> NoReturn:
    if permission_error_status is not None and isinstance(exc, PermissionError):
        raise HTTPException(
            status_code=permission_error_status, detail=str(exc)
        ) from exc
    if key_error_status is not None and isinstance(exc, KeyError):
        detail = key_error_detail if key_error_detail is not None else str(exc)
        raise HTTPException(status_code=key_error_status, detail=detail) from exc
    if value_error_status is not None and isinstance(exc, ValueError):
        raise HTTPException(status_code=value_error_status, detail=str(exc)) from exc
    if runtime_error_status is not None and isinstance(exc, RuntimeError):
        raise HTTPException(status_code=runtime_error_status, detail=str(exc)) from exc
    if os_error_status is not None and isinstance(exc, OSError):
        raise HTTPException(status_code=os_error_status, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/health")
def health_check(request: Request) -> ServerHealthPayload:
    container = getattr(request.app.state, "container", None)
    if container is None:
        return build_server_health_payload()
    return build_server_health_payload(
        config_dir=container.config_dir,
        role_registry=container.role_registry,
        skill_registry=container.skill_registry,
        tool_registry=container.tool_registry,
    )


@router.get("/configs")
def get_config_status(
    service: ConfigStatusService = Depends(get_config_status_service),
) -> dict[str, JsonValue]:
    return service.get_config_status()


@router.get("/configs/ui-language")
def get_ui_language_settings(
    service: UiLanguageSettingsService = Depends(get_ui_language_settings_service),
) -> UiLanguageSettings:
    return service.get_ui_language_settings()


@router.put("/configs/ui-language")
def save_ui_language_settings(
    req: UiLanguageSettings,
    service: UiLanguageSettingsService = Depends(get_ui_language_settings_service),
) -> dict[str, str]:
    try:
        service.save_ui_language_settings(req)
        return {"status": "ok"}
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/configs/model")
def get_model_config(
    service: ModelConfigService = Depends(get_model_config_service),
) -> dict[str, JsonValue]:
    return service.get_model_config()


@router.get("/configs/model/profiles")
def get_model_profiles(
    service: ModelConfigService = Depends(get_model_config_service),
) -> dict[str, dict[str, JsonValue]]:
    return service.get_model_profiles()


class ModelProfileRequest(ModelProfileConfigPayload):
    model_config = ConfigDict(extra="forbid")

    source_name: str | None = None


@router.put("/configs/model/profiles/{name}")
def save_model_profile(
    name: str,
    req: ModelProfileRequest,
    service: ModelConfigService = Depends(get_model_config_service),
) -> dict[str, str]:
    try:
        profile: dict[str, JsonValue] = {
            "model": req.model,
            "provider": req.provider.value,
            "base_url": (
                DEFAULT_MAAS_BASE_URL
                if req.provider == ProviderType.MAAS
                else req.base_url
            ),
            "temperature": req.temperature,
            "top_p": req.top_p,
            "context_window": req.context_window,
            "connect_timeout_seconds": req.connect_timeout_seconds,
        }
        if "max_tokens" in req.model_fields_set:
            profile["max_tokens"] = req.max_tokens
        if req.is_default is not None:
            profile["is_default"] = req.is_default
        if req.ssl_verify is not None:
            profile["ssl_verify"] = req.ssl_verify
        if req.api_key is not None and req.api_key.strip():
            profile["api_key"] = req.api_key
        if req.headers is not None:
            profile["headers"] = [
                header.model_dump(mode="json") for header in req.headers
            ]
        if req.maas_auth is not None:
            profile["maas_auth"] = req.maas_auth.model_dump(mode="json")
        service.save_model_profile(name, profile, source_name=req.source_name)
        return {"status": "ok"}
    except Exception as exc:
        _raise_system_http_error(
            exc,
            key_error_status=404,
            value_error_status=400,
        )


@router.get("/configs/model/providers/models")
def get_provider_models(
    provider: ProviderType | None = Query(default=None),
    service: ModelConfigService = Depends(get_model_config_service),
) -> list[dict[str, JsonValue]]:
    return [
        model.model_dump(mode="json")
        for model in service.get_provider_models(provider=provider)
    ]


@router.delete("/configs/model/profiles/{name}")
def delete_model_profile(
    name: str,
    service: ModelConfigService = Depends(get_model_config_service),
) -> dict[str, str]:
    try:
        service.delete_model_profile(name)
        return {"status": "ok"}
    except Exception as exc:
        _raise_system_http_error(
            exc,
            key_error_status=404,
            value_error_status=400,
        )


@router.put("/configs/model")
def save_model_config(
    req: ModelConfigPayload | ModelConfigRequest,
    service: ModelConfigService = Depends(get_model_config_service),
) -> dict[str, str]:
    try:
        config = req.config if isinstance(req, ModelConfigRequest) else req
        service.save_model_config(config)
        return {"status": "ok"}
    except Exception as exc:
        _raise_system_http_error(exc, value_error_status=400)


@router.post("/configs/model:probe")
def probe_model_connectivity(
    req: ModelConnectivityProbeRequest,
    service: ModelConfigService = Depends(get_model_config_service),
) -> ModelConnectivityProbeResult:
    try:
        return service.probe_connectivity(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/configs/model:discover")
def discover_model_catalog(
    req: ModelDiscoveryRequest,
    service: ModelConfigService = Depends(get_model_config_service),
) -> ModelDiscoveryResult:
    try:
        return service.discover_models(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/configs/notifications")
def get_notification_config(
    service: NotificationSettingsService = Depends(get_notification_settings_service),
) -> NotificationConfig:
    return service.get_notification_config()


@router.get("/configs/environment-variables")
def get_environment_variables(
    service: EnvironmentVariableService = Depends(get_environment_variable_service),
) -> EnvironmentVariableCatalog:
    try:
        return service.list_environment_variables()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/configs/environment-variables/{scope}/{key}")
def save_environment_variable(
    scope: EnvironmentVariableScope,
    key: str,
    req: EnvironmentVariableSaveRequest,
    service: EnvironmentVariableService = Depends(get_environment_variable_service),
) -> EnvironmentVariableRecord:
    try:
        return service.save_environment_variable(scope=scope, key=key, request=req)
    except Exception as exc:
        _raise_system_http_error(
            exc,
            permission_error_status=403,
            value_error_status=400,
            runtime_error_status=400,
        )


@router.delete("/configs/environment-variables/{scope}/{key}")
def delete_environment_variable(
    scope: EnvironmentVariableScope,
    key: str,
    service: EnvironmentVariableService = Depends(get_environment_variable_service),
) -> dict[str, str]:
    try:
        service.delete_environment_variable(scope=scope, key=key)
        return {"status": "ok"}
    except Exception as exc:
        _raise_system_http_error(
            exc,
            permission_error_status=403,
            value_error_status=400,
            runtime_error_status=400,
        )


@router.get("/configs/proxy")
def get_proxy_config(
    service: ProxyConfigService = Depends(get_proxy_config_service),
) -> ProxyEnvInput:
    return service.get_saved_proxy_config()


@router.put("/configs/proxy")
def save_proxy_config(
    req: ProxyEnvInput,
    service: ProxyConfigService = Depends(get_proxy_config_service),
) -> dict[str, str]:
    try:
        service.save_proxy_config(req)
        return {"status": "ok"}
    except Exception as exc:
        _raise_system_http_error(
            exc,
            value_error_status=400,
            runtime_error_status=400,
        )


@router.get("/configs/web")
def get_web_config(
    service: WebConfigService = Depends(get_web_config_service),
) -> WebConfig:
    return service.get_web_config()


@router.put("/configs/web")
def save_web_config(
    req: WebConfig,
    service: WebConfigService = Depends(get_web_config_service),
) -> dict[str, str]:
    try:
        service.save_web_config(req)
        return {"status": "ok"}
    except Exception as exc:
        _raise_system_http_error(
            exc,
            value_error_status=400,
            runtime_error_status=400,
        )


@router.get("/configs/agents", response_model=list[ExternalAgentSummary])
def list_external_agents(
    service: ExternalAgentConfigService = Depends(get_external_agent_config_service),
) -> tuple[ExternalAgentSummary, ...]:
    return service.list_agents()


@router.get("/configs/agents/{agent_id}", response_model=ExternalAgentConfig)
def get_external_agent(
    agent_id: RequiredIdentifierStr,
    service: ExternalAgentConfigService = Depends(get_external_agent_config_service),
) -> ExternalAgentConfig:
    try:
        return service.get_agent(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/configs/agents/{agent_id}", response_model=ExternalAgentConfig)
def save_external_agent(
    agent_id: RequiredIdentifierStr,
    req: ExternalAgentConfig,
    service: ExternalAgentConfigService = Depends(get_external_agent_config_service),
) -> ExternalAgentConfig:
    try:
        return service.save_agent(agent_id, req)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/configs/agents/{agent_id}")
def delete_external_agent(
    agent_id: RequiredIdentifierStr,
    service: ExternalAgentConfigService = Depends(get_external_agent_config_service),
) -> dict[str, str]:
    try:
        service.delete_agent(agent_id)
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/configs/agents/{agent_id}:test", response_model=ExternalAgentTestResult)
async def test_external_agent(
    agent_id: RequiredIdentifierStr,
    service: ExternalAgentConfigService = Depends(get_external_agent_config_service),
) -> ExternalAgentTestResult:
    try:
        config = service.resolve_runtime_agent(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    result = await probe_acp_agent(config)
    if result.ok:
        return result
    raise HTTPException(status_code=400, detail=result.message)


@router.get("/configs/github")
def get_github_config(
    service: GitHubConfigService = Depends(get_github_config_service),
) -> GitHubConfig:
    return service.get_github_config()


@router.put("/configs/github")
def save_github_config(
    req: GitHubConfig,
    service: GitHubConfigService = Depends(get_github_config_service),
) -> dict[str, str]:
    try:
        service.save_github_config(req)
        return {"status": "ok"}
    except Exception as exc:
        _raise_system_http_error(
            exc,
            value_error_status=400,
            runtime_error_status=400,
        )


@router.get("/configs/clawhub")
def get_clawhub_config(
    service: ClawHubConfigService = Depends(get_clawhub_config_service),
) -> ClawHubConfig:
    return service.get_clawhub_config()


@router.put("/configs/clawhub")
def save_clawhub_config(
    req: ClawHubConfig,
    service: ClawHubConfigService = Depends(get_clawhub_config_service),
) -> dict[str, str]:
    try:
        service.save_clawhub_config(req)
        return {"status": "ok"}
    except Exception as exc:
        _raise_system_http_error(
            exc,
            value_error_status=400,
            runtime_error_status=400,
        )


@router.post("/configs/clawhub:probe")
def probe_clawhub_connectivity(
    req: ClawHubConnectivityProbeRequest,
    service: ClawHubConfigService = Depends(get_clawhub_config_service),
) -> ClawHubConnectivityProbeResult:
    try:
        return service.probe_connectivity(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/configs/clawhub/skills",
    response_model=list[ClawHubSkillSummary],
)
def list_clawhub_skills(
    service: ClawHubSkillService = Depends(get_clawhub_skill_service),
) -> tuple[ClawHubSkillSummary, ...]:
    return service.list_skills()


@router.post(
    "/configs/clawhub/skills:search",
    response_model=ClawHubSkillSearchResult,
)
def search_clawhub_skills(
    req: ClawHubSkillSearchRequest,
    service: ClawHubSkillSearchService = Depends(get_clawhub_search_service),
) -> ClawHubSkillSearchResult:
    try:
        return service.search(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/configs/clawhub/skills:install",
    response_model=ClawHubSkillInstallResult,
)
def install_clawhub_skill(
    req: ClawHubSkillInstallRequest,
    service: ClawHubSkillInstallService = Depends(get_clawhub_install_service),
) -> ClawHubSkillInstallResult:
    try:
        return service.install(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/configs/clawhub/skills/{skill_id}",
    response_model=ClawHubSkillDetail,
)
def get_clawhub_skill(
    skill_id: RequiredIdentifierStr,
    service: ClawHubSkillService = Depends(get_clawhub_skill_service),
) -> ClawHubSkillDetail:
    try:
        return service.get_skill(skill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put(
    "/configs/clawhub/skills/{skill_id}",
    response_model=ClawHubSkillDetail,
)
def save_clawhub_skill(
    skill_id: RequiredIdentifierStr,
    req: ClawHubSkillWriteRequest,
    service: ClawHubSkillService = Depends(get_clawhub_skill_service),
) -> ClawHubSkillDetail:
    try:
        return service.save_skill(skill_id, req)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/configs/clawhub/skills/{skill_id}")
def delete_clawhub_skill(
    skill_id: RequiredIdentifierStr,
    service: ClawHubSkillService = Depends(get_clawhub_skill_service),
) -> dict[str, str]:
    try:
        service.delete_skill(skill_id)
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/configs/notifications")
def save_notification_config(
    req: NotificationConfig | NotificationConfigRequest,
    service: NotificationSettingsService = Depends(get_notification_settings_service),
) -> dict[str, str]:
    try:
        config = req.config if isinstance(req, NotificationConfigRequest) else req
        service.save_notification_config(config)
        return {"status": "ok"}
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/configs/orchestration")
def get_orchestration_config(
    service: OrchestrationSettingsService = Depends(get_orchestration_settings_service),
) -> OrchestrationSettings:
    return service.get_orchestration_config()


@router.put("/configs/orchestration")
def save_orchestration_config(
    req: OrchestrationSettings | OrchestrationConfigRequest,
    service: OrchestrationSettingsService = Depends(get_orchestration_settings_service),
) -> dict[str, str]:
    try:
        config = req.config if isinstance(req, OrchestrationConfigRequest) else req
        service.save_orchestration_config(config)
        return {"status": "ok"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/configs/model:reload")
def reload_model_config(
    service: ModelConfigService = Depends(get_model_config_service),
) -> dict[str, str]:
    try:
        service.reload_model_config()
        return {"status": "ok"}
    except Exception as exc:
        _raise_system_http_error(
            exc,
            value_error_status=400,
            runtime_error_status=400,
        )


@router.post("/configs/proxy:reload")
def reload_proxy_config(
    service: ProxyConfigService = Depends(get_proxy_config_service),
) -> dict[str, str]:
    try:
        service.reload_proxy_config()
        return {"status": "ok"}
    except Exception as exc:
        _raise_system_http_error(
            exc,
            value_error_status=400,
            runtime_error_status=400,
        )


@router.post("/configs/web:probe")
def probe_web_connectivity(
    req: WebConnectivityProbeRequest,
    service: ProxyConfigService = Depends(get_proxy_config_service),
) -> WebConnectivityProbeResult:
    try:
        return service.probe_web_connectivity(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/configs/github:probe")
def probe_github_connectivity(
    req: GitHubConnectivityProbeRequest,
    service: GitHubConfigService = Depends(get_github_config_service),
) -> GitHubConnectivityProbeResult:
    try:
        return service.probe_connectivity(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/configs/mcp:reload")
def reload_mcp_config(
    service: McpConfigReloadService = Depends(get_mcp_config_reload_service),
) -> dict[str, str]:
    try:
        service.reload_mcp_config()
        return {"status": "ok"}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/configs/skills:reload")
def reload_skills_config(
    service: SkillsConfigReloadService = Depends(get_skills_config_reload_service),
) -> dict[str, str]:
    try:
        service.reload_skills_config()
        return {"status": "ok"}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
