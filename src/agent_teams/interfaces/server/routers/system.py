# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, JsonValue

from agent_teams.env.environment_variable_models import (
    EnvironmentVariableCatalog,
    EnvironmentVariableRecord,
    EnvironmentVariableSaveRequest,
    EnvironmentVariableScope,
)
from agent_teams.env.environment_variable_service import EnvironmentVariableService
from agent_teams.env.github_config_models import GitHubConfig
from agent_teams.env.github_config_service import GitHubConfigService
from agent_teams.env.github_connectivity import (
    GitHubConnectivityProbeRequest,
    GitHubConnectivityProbeResult,
)
from agent_teams.external_agents import (
    ExternalAgentConfig,
    ExternalAgentConfigService,
    ExternalAgentSummary,
    ExternalAgentTestResult,
)
from agent_teams.external_agents.acp_client import probe_acp_agent
from agent_teams.env.proxy_config_service import ProxyConfigService
from agent_teams.env.proxy_env import ProxyEnvInput
from agent_teams.env.web_config_models import WebConfig
from agent_teams.env.web_config_service import WebConfigService
from agent_teams.env.web_connectivity import (
    WebConnectivityProbeRequest,
    WebConnectivityProbeResult,
)
from agent_teams.interfaces.server.deps import (
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
from agent_teams.interfaces.server.ui_language_models import UiLanguageSettings
from agent_teams.interfaces.server.ui_language_service import UiLanguageSettingsService
from agent_teams.agents.orchestration.settings_service import (
    OrchestrationSettingsService,
)
from agent_teams.interfaces.server.config_status_service import ConfigStatusService
from agent_teams.interfaces.server.runtime_identity import (
    ServerHealthPayload,
    build_server_health_payload,
)
from agent_teams.mcp.config_reload_service import McpConfigReloadService
from agent_teams.notifications.notification_settings_service import (
    NotificationSettingsService,
)
from agent_teams.providers.model_config import (
    DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
    ModelRequestHeader,
    ProviderType,
)
from agent_teams.providers.model_config_service import ModelConfigService
from agent_teams.providers.model_connectivity import (
    ModelDiscoveryRequest,
    ModelDiscoveryResult,
    ModelConnectivityProbeRequest,
    ModelConnectivityProbeResult,
)
from agent_teams.skills.config_reload_service import SkillsConfigReloadService
from agent_teams.validation import RequiredIdentifierStr

router = APIRouter(prefix="/system", tags=["System"])


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
    except Exception as exc:
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


class ModelProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: str | None = None
    provider: ProviderType = ProviderType.OPENAI_COMPATIBLE
    is_default: bool | None = None
    model: str
    base_url: str
    api_key: str | None = None
    headers: tuple[ModelRequestHeader, ...] | None = None
    ssl_verify: bool | None = None
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int | None = None
    context_window: int | None = None
    connect_timeout_seconds: float = DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS


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
            "base_url": req.base_url,
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
        service.save_model_profile(name, profile, source_name=req.source_name)
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class ModelConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: dict[str, JsonValue]


@router.put("/configs/model")
def save_model_config(
    req: ModelConfigRequest,
    service: ModelConfigService = Depends(get_model_config_service),
) -> dict[str, str]:
    try:
        service.save_model_config(req.config)
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
) -> dict[str, JsonValue]:
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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/configs/environment-variables/{scope}/{key}")
def delete_environment_variable(
    scope: EnvironmentVariableScope,
    key: str,
    service: EnvironmentVariableService = Depends(get_environment_variable_service),
) -> dict[str, str]:
    try:
        service.delete_environment_variable(scope=scope, key=key)
        return {"status": "ok"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class NotificationConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: dict[str, JsonValue]


class OrchestrationConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: dict[str, JsonValue]


@router.put("/configs/notifications")
def save_notification_config(
    req: NotificationConfigRequest,
    service: NotificationSettingsService = Depends(get_notification_settings_service),
) -> dict[str, str]:
    try:
        service.save_notification_config(req.config)
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/configs/orchestration")
def get_orchestration_config(
    service: OrchestrationSettingsService = Depends(get_orchestration_settings_service),
) -> dict[str, JsonValue]:
    return service.get_orchestration_config()


@router.put("/configs/orchestration")
def save_orchestration_config(
    req: OrchestrationConfigRequest,
    service: OrchestrationSettingsService = Depends(get_orchestration_settings_service),
) -> dict[str, str]:
    try:
        service.save_orchestration_config(req.config)
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
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/configs/proxy:reload")
def reload_proxy_config(
    service: ProxyConfigService = Depends(get_proxy_config_service),
) -> dict[str, str]:
    try:
        service.reload_proxy_config()
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/configs/skills:reload")
def reload_skills_config(
    service: SkillsConfigReloadService = Depends(get_skills_config_reload_service),
) -> dict[str, str]:
    try:
        service.reload_skills_config()
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
