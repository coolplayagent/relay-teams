# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import cast

from pydantic import JsonValue
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_teams.env.proxy_env import ProxyEnvInput
from agent_teams.external_agents import (
    ExternalAgentConfig,
    ExternalAgentSummary,
    ExternalAgentTestResult,
    StdioTransportConfig,
)
from agent_teams.env.github_config_models import GitHubConfig
from agent_teams.env.github_connectivity import GitHubConnectivityProbeRequest
from agent_teams.env.github_connectivity import GitHubConnectivityProbeResult
from agent_teams.env.web_config_models import WebConfig, WebProvider
from agent_teams.env.web_connectivity import WebConnectivityProbeResult
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
from agent_teams.interfaces.server.ui_language_models import (
    UiLanguage,
    UiLanguageSettings,
)
from agent_teams.interfaces.server.routers import system
from agent_teams.providers.model_connectivity import (
    ModelConnectivityProbeRequest,
    ModelConnectivityProbeResult,
    ModelDiscoveryResult,
)
from agent_teams.providers.model_config import ProviderModelInfo, ProviderType


class _FakeSystemService:
    def __init__(self) -> None:
        self.saved_notification_config: dict[str, object] | None = None
        self.saved_orchestration_config: dict[str, object] | None = None
        self.saved_model_profile: tuple[str, dict[str, object], str | None] | None = (
            None
        )
        self.saved_proxy_config: dict[str, object] | None = None
        self.saved_web_config: dict[str, object] | None = None
        self.saved_github_config: dict[str, object] | None = None
        self.saved_ui_language_settings: dict[str, object] | None = None
        self.proxy_save_error: RuntimeError | None = None
        self.external_agents: dict[str, ExternalAgentConfig] = {
            "codex_local": ExternalAgentConfig(
                agent_id="codex_local",
                name="Codex Local",
                description="Runs Codex via stdio",
                transport=StdioTransportConfig(command="codex", args=("--serve",)),
            )
        }

    def get_config_status(self) -> dict[str, object]:
        return {"model": {"loaded": True}}

    def get_ui_language_settings(self) -> UiLanguageSettings:
        return UiLanguageSettings(language=UiLanguage.ZH_CN)

    def save_ui_language_settings(
        self,
        settings: UiLanguageSettings,
    ) -> UiLanguageSettings:
        self.saved_ui_language_settings = settings.model_dump(mode="json")
        return settings

    def get_model_config(self) -> dict[str, object]:
        return {}

    def get_model_profiles(self) -> dict[str, object]:
        return {
            "default": {
                "provider": "openai_compatible",
                "model": "gpt-4o-mini",
                "base_url": "https://example.test/v1",
                "api_key": "secret",
                "has_api_key": True,
                "headers": [],
                "is_default": True,
                "context_window": 128000,
            }
        }

    def save_model_profile(
        self,
        name: str,
        profile: dict[str, object],
        *,
        source_name: str | None = None,
    ) -> None:
        self.saved_model_profile = (name, profile, source_name)

    def delete_model_profile(self, _name: str) -> None:
        return None

    def save_model_config(self, _config: dict[str, object]) -> None:
        return None

    def reload_model_config(self) -> None:
        return None

    def reload_proxy_config(self) -> None:
        return None

    def get_saved_proxy_config(self) -> dict[str, object]:
        return {
            "http_proxy": "http://proxy.example:8080",
            "https_proxy": None,
            "all_proxy": None,
            "no_proxy": "localhost,127.0.0.1",
            "proxy_username": "alice",
            "proxy_password": "secret",
            "ssl_verify": None,
        }

    def save_proxy_config(self, config: ProxyEnvInput) -> None:
        if self.proxy_save_error is not None:
            raise self.proxy_save_error
        self.saved_proxy_config = config.model_dump(mode="json")

    def get_web_config(self) -> WebConfig:
        return WebConfig(provider=WebProvider.EXA, api_key=None)

    def list_agents(self) -> tuple[ExternalAgentSummary, ...]:
        return tuple(
            ExternalAgentSummary(
                agent_id=agent.agent_id,
                name=agent.name,
                description=agent.description,
                transport=agent.transport.transport,
            )
            for agent in self.external_agents.values()
        )

    def get_agent(self, agent_id: str) -> ExternalAgentConfig:
        return self.external_agents[agent_id]

    def save_agent(
        self,
        agent_id: str,
        config: ExternalAgentConfig,
    ) -> ExternalAgentConfig:
        self.external_agents[agent_id] = config
        return config

    def delete_agent(self, agent_id: str) -> None:
        self.external_agents.pop(agent_id)

    def resolve_runtime_agent(self, agent_id: str) -> ExternalAgentConfig:
        return self.external_agents[agent_id]

    def save_web_config(self, config: WebConfig) -> None:
        self.saved_web_config = config.model_dump(mode="json")

    def get_github_config(self) -> GitHubConfig:
        return GitHubConfig(token=None)

    def save_github_config(self, config: GitHubConfig) -> None:
        self.saved_github_config = config.model_dump(mode="json")

    def reload_mcp_config(self) -> None:
        return None

    def reload_skills_config(self) -> None:
        return None

    def get_notification_config(self) -> dict[str, object]:
        return {
            "tool_approval_requested": {
                "enabled": True,
                "channels": ["browser", "toast"],
                "feishu_format": "text",
            },
            "run_completed": {
                "enabled": False,
                "channels": ["toast"],
                "feishu_format": "text",
            },
            "run_failed": {
                "enabled": True,
                "channels": ["browser", "toast"],
                "feishu_format": "text",
            },
            "run_stopped": {
                "enabled": False,
                "channels": ["toast"],
                "feishu_format": "text",
            },
        }

    def save_notification_config(self, config: dict[str, object]) -> None:
        self.saved_notification_config = config

    def get_orchestration_config(self) -> dict[str, object]:
        return {
            "default_orchestration_preset_id": "default",
            "presets": [
                {
                    "preset_id": "default",
                    "name": "Default",
                    "description": "General delegation flow.",
                    "role_ids": ["writer", "reviewer"],
                    "orchestration_prompt": "Delegate by capability and keep the final answer concise.",
                }
            ],
        }

    def save_orchestration_config(self, config: dict[str, object]) -> None:
        self.saved_orchestration_config = config

    def get_provider_models(
        self,
        *,
        provider: ProviderType | None = None,
    ) -> tuple[ProviderModelInfo, ...]:
        models = (
            ProviderModelInfo(
                profile="default",
                provider=ProviderType.OPENAI_COMPATIBLE,
                model="gpt-4o-mini",
                base_url="https://example.com/v1",
            ),
            ProviderModelInfo(
                profile="glm",
                provider=ProviderType.BIGMODEL,
                model="glm-4.5",
                base_url="https://open.bigmodel.cn/api/paas/v4",
            ),
            ProviderModelInfo(
                profile="echo",
                provider=ProviderType.ECHO,
                model="echo",
                base_url="http://localhost",
            ),
        )
        if provider is None:
            return models
        return tuple(model for model in models if model.provider == provider)

    def probe_connectivity(
        self,
        request: object,
    ) -> ModelConnectivityProbeResult | GitHubConnectivityProbeResult:
        if isinstance(request, GitHubConnectivityProbeRequest):
            return GitHubConnectivityProbeResult.model_validate(
                {
                    "ok": True,
                    "username": "octocat",
                    "host": "github.com",
                    "gh_path": "/tmp/gh",
                    "gh_version": "2.88.1",
                    "status_code": 200,
                    "exit_code": 0,
                    "latency_ms": 51,
                    "checked_at": "2026-03-12T00:00:00Z",
                    "diagnostics": {
                        "binary_available": True,
                        "auth_valid": True,
                        "used_proxy": False,
                        "bundled_binary": True,
                    },
                    "retryable": False,
                }
            )
        assert isinstance(request, ModelConnectivityProbeRequest)
        return ModelConnectivityProbeResult.model_validate(
            {
                "ok": True,
                "provider": ProviderType.OPENAI_COMPATIBLE.value,
                "model": "gpt-4o-mini",
                "latency_ms": 123,
                "checked_at": "2026-03-10T00:00:00Z",
                "diagnostics": {
                    "endpoint_reachable": True,
                    "auth_valid": True,
                    "rate_limited": False,
                },
                "token_usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 1,
                    "total_tokens": 9,
                },
                "retryable": False,
            }
        )

    def discover_models(
        self,
        _request: object,
    ) -> ModelDiscoveryResult:
        return ModelDiscoveryResult.model_validate(
            {
                "ok": True,
                "provider": ProviderType.OPENAI_COMPATIBLE.value,
                "base_url": "https://example.test/v1",
                "latency_ms": 37,
                "checked_at": "2026-03-10T00:00:00Z",
                "diagnostics": {
                    "endpoint_reachable": True,
                    "auth_valid": True,
                    "rate_limited": False,
                },
                "models": ["fake-chat-model", "reasoning-model"],
                "retryable": False,
            }
        )

    def probe_web_connectivity(
        self,
        _request: object,
    ) -> WebConnectivityProbeResult:
        return WebConnectivityProbeResult.model_validate(
            {
                "ok": True,
                "url": "https://example.com",
                "final_url": "https://example.com",
                "status_code": 200,
                "latency_ms": 88,
                "checked_at": "2026-03-12T00:00:00Z",
                "used_method": "HEAD",
                "diagnostics": {
                    "endpoint_reachable": True,
                    "used_proxy": True,
                    "redirected": False,
                },
                "retryable": False,
            }
        )


def _create_test_client(fake_service: object) -> TestClient:
    app = FastAPI()
    app.include_router(system.router, prefix="/api")
    app.dependency_overrides[get_config_status_service] = lambda: fake_service
    app.dependency_overrides[get_model_config_service] = lambda: fake_service
    app.dependency_overrides[get_notification_settings_service] = lambda: fake_service
    app.dependency_overrides[get_orchestration_settings_service] = lambda: fake_service
    app.dependency_overrides[get_mcp_config_reload_service] = lambda: fake_service
    app.dependency_overrides[get_skills_config_reload_service] = lambda: fake_service
    app.dependency_overrides[get_proxy_config_service] = lambda: fake_service
    app.dependency_overrides[get_ui_language_settings_service] = lambda: fake_service
    app.dependency_overrides[get_web_config_service] = lambda: fake_service
    app.dependency_overrides[get_github_config_service] = lambda: fake_service
    app.dependency_overrides[get_external_agent_config_service] = lambda: fake_service
    return TestClient(app)


def test_health_check_returns_runtime_identity_and_skill_sanity() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"] == "0.1.0"
    assert payload["python_executable"]
    assert payload["package_root"]
    assert payload["config_dir"]
    assert payload["builtin_roles_dir"]
    assert payload["builtin_skills_dir"]
    role_registry_sanity = payload["role_registry_sanity"]
    assert role_registry_sanity["builtin_role_count"] >= 1
    assert role_registry_sanity["has_builtin_coordinator"] is True
    assert role_registry_sanity["has_builtin_main_agent"] is True
    skill_registry_sanity = payload["skill_registry_sanity"]
    assert skill_registry_sanity["builtin_skill_count"] >= 1
    assert "builtin:deepresearch" in skill_registry_sanity["builtin_skill_refs"]
    assert skill_registry_sanity["has_builtin_deepresearch"] is True
    tool_registry_sanity = payload["tool_registry_sanity"]
    assert tool_registry_sanity["available_tool_count"] >= 1
    assert "write" in tool_registry_sanity["available_tool_names"]


def test_get_notification_config() -> None:
    client = _create_test_client(_FakeSystemService())
    response = client.get("/api/system/configs/notifications")
    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_approval_requested"]["enabled"] is True
    assert payload["run_completed"]["channels"] == ["toast"]


def test_get_ui_language_settings() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/ui-language")

    assert response.status_code == 200
    assert response.json() == {"language": "zh-CN"}


def test_save_ui_language_settings() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/ui-language",
        json={"language": "en-US"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_ui_language_settings == {"language": "en-US"}


def test_save_model_profile_includes_connect_timeout_seconds() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/default",
        json={
            "provider": "openai_compatible",
            "model": "gpt-4o-mini",
            "base_url": "https://example.test/v1",
            "api_key": "secret",
            "temperature": 0.2,
            "top_p": 1.0,
            "max_tokens": 2048,
            "context_window": 128000,
            "connect_timeout_seconds": 25.0,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_model_profile is not None
    _, saved_profile, source_name = service.saved_model_profile
    assert saved_profile["connect_timeout_seconds"] == 25.0
    assert saved_profile["context_window"] == 128000
    assert source_name is None


def test_save_notification_config() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)
    request_payload = {
        "config": {
            "tool_approval_requested": {
                "enabled": True,
                "channels": ["browser", "toast"],
                "feishu_format": "text",
            },
            "run_completed": {
                "enabled": True,
                "channels": ["toast", "feishu"],
                "feishu_format": "card",
            },
            "run_failed": {
                "enabled": True,
                "channels": ["browser", "toast"],
                "feishu_format": "text",
            },
            "run_stopped": {
                "enabled": True,
                "channels": ["toast"],
                "feishu_format": "text",
            },
        }
    }
    response = client.put("/api/system/configs/notifications", json=request_payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_notification_config is not None
    run_completed = service.saved_notification_config["run_completed"]
    assert isinstance(run_completed, dict)
    assert run_completed["enabled"] is True
    assert run_completed["feishu_format"] == "card"


def test_get_orchestration_config() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/orchestration")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_orchestration_preset_id"] == "default"
    assert payload["presets"][0]["role_ids"] == ["writer", "reviewer"]


def test_save_github_config() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/github",
        json={"token": "ghp_secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_github_config == {"token": "ghp_secret"}


def test_probe_github_connectivity() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.post(
        "/api/system/configs/github:probe",
        json={"token": "ghp_secret", "timeout_ms": 2500},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["username"] == "octocat"


def test_save_orchestration_config() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/orchestration",
        json={
            "config": {
                "default_orchestration_preset_id": "shipping",
                "presets": [
                    {
                        "preset_id": "shipping",
                        "name": "Shipping",
                        "description": "Release work.",
                        "role_ids": ["writer"],
                        "orchestration_prompt": "Use writer for outward-facing updates.",
                    }
                ],
            }
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_orchestration_config is not None
    assert service.saved_orchestration_config["default_orchestration_preset_id"] == (
        "shipping"
    )


def test_get_provider_models() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/model/providers/models")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 3
    assert payload[0]["profile"] == "default"


def test_get_model_profiles_returns_api_key() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/model/profiles")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default"]["api_key"] == "secret"
    assert payload["default"]["has_api_key"] is True
    assert payload["default"]["is_default"] is True
    assert payload["default"]["context_window"] == 128000


def test_get_provider_models_with_filter() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get(
        "/api/system/configs/model/providers/models",
        params={"provider": ProviderType.ECHO.value},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["provider"] == ProviderType.ECHO.value


def test_probe_model_connectivity() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.post(
        "/api/system/configs/model:probe",
        json={"profile_name": "default"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["latency_ms"] == 123
    assert payload["token_usage"]["total_tokens"] == 9


def test_discover_model_catalog() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.post(
        "/api/system/configs/model:discover",
        json={
            "override": {
                "base_url": "https://example.test/v1",
                "api_key": "secret",
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["latency_ms"] == 37
    assert payload["models"] == ["fake-chat-model", "reasoning-model"]


def test_reload_proxy_config() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.post("/api/system/configs/proxy:reload")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_proxy_config() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/proxy")

    assert response.status_code == 200
    assert response.json() == {
        "http_proxy": "http://proxy.example:8080",
        "https_proxy": None,
        "all_proxy": None,
        "no_proxy": "localhost,127.0.0.1",
        "proxy_username": "alice",
        "proxy_password": "secret",
        "ssl_verify": None,
    }


def test_get_web_config() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/web")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "exa",
        "api_key": None,
    }


def test_save_proxy_config() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/proxy",
        json={
            "http_proxy": "http://proxy.example:8080",
            "https_proxy": "http://proxy.example:8443",
            "all_proxy": "",
            "no_proxy": "localhost,127.0.0.1",
            "proxy_username": "alice",
            "proxy_password": "secret",
            "ssl_verify": None,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_proxy_config == {
        "http_proxy": "http://proxy.example:8080",
        "https_proxy": "http://proxy.example:8443",
        "all_proxy": "",
        "no_proxy": "localhost,127.0.0.1",
        "proxy_username": "alice",
        "proxy_password": "secret",
        "ssl_verify": None,
    }


def test_save_web_config() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/web",
        json={
            "provider": "exa",
            "api_key": "secret",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_web_config == {
        "provider": "exa",
        "api_key": "secret",
    }


def test_list_external_agents() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/agents")

    assert response.status_code == 200
    assert response.json() == [
        {
            "agent_id": "codex_local",
            "name": "Codex Local",
            "description": "Runs Codex via stdio",
            "transport": "stdio",
        }
    ]


def test_get_external_agent_omits_stdio_working_directory() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/agents/codex_local")

    assert response.status_code == 200
    assert response.json() == {
        "agent_id": "codex_local",
        "name": "Codex Local",
        "description": "Runs Codex via stdio",
        "transport": {
            "transport": "stdio",
            "command": "codex",
            "args": ["--serve"],
            "env": [],
        },
    }


def test_save_external_agent() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/agents/claude_http",
        json={
            "agent_id": "claude_http",
            "name": "Claude HTTP",
            "description": "Runs Claude over HTTP",
            "transport": {
                "transport": "streamable_http",
                "url": "http://127.0.0.1:4100/acp",
                "headers": [],
                "ssl_verify": True,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["agent_id"] == "claude_http"
    assert (
        service.external_agents["claude_http"].transport.transport == "streamable_http"
    )


def test_test_external_agent(monkeypatch) -> None:
    async def fake_probe(_config: ExternalAgentConfig) -> ExternalAgentTestResult:
        return ExternalAgentTestResult(
            ok=True,
            message="Connected",
            agent_name="Codex",
            agent_version="1.0.0",
            protocol_version=1,
        )

    monkeypatch.setattr(system, "probe_acp_agent", fake_probe)
    client = _create_test_client(_FakeSystemService())

    response = client.post("/api/system/configs/agents/codex_local:test")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "message": "Connected",
        "agent_name": "Codex",
        "agent_version": "1.0.0",
        "protocol_version": 1,
    }


def test_save_proxy_config_returns_user_error_for_missing_keyring() -> None:
    service = _FakeSystemService()
    service.proxy_save_error = RuntimeError(
        "Proxy password persistence requires a usable system keyring backend."
    )
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/proxy",
        json={
            "https_proxy": "http://proxy.example:8443",
            "proxy_username": "alice",
            "proxy_password": "secret",
        },
    )

    assert response.status_code == 400
    assert "system keyring backend" in response.json()["detail"]


def test_probe_web_connectivity() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.post(
        "/api/system/configs/web:probe",
        json={"url": "https://example.com"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["used_method"] == "HEAD"
    assert payload["diagnostics"]["used_proxy"] is True


def test_probe_web_connectivity_accepts_proxy_override() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.post(
        "/api/system/configs/web:probe",
        json={
            "url": "https://example.com",
            "proxy_override": {
                "https_proxy": "http://proxy.example:8443",
                "no_proxy": "",
                "proxy_username": "alice",
                "proxy_password": "secret",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True


def test_save_model_profile_allows_missing_api_key_for_edit() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/default",
        json={
            "provider": ProviderType.OPENAI_COMPATIBLE.value,
            "model": "kimi-k2.5",
            "base_url": "https://api.moonshot.cn/v1",
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": 4096,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_model_profile is not None
    saved_name, saved_profile, source_name = service.saved_model_profile
    assert saved_name == "default"
    assert "api_key" not in saved_profile
    assert saved_profile["top_p"] == 0.95
    assert source_name is None


def test_save_model_profile_accepts_bigmodel_provider() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/glm",
        json={
            "provider": ProviderType.BIGMODEL.value,
            "model": "glm-4.5",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_key": "secret",
            "temperature": 0.2,
            "top_p": 0.9,
            "max_tokens": 4096,
        },
    )

    assert response.status_code == 200
    assert service.saved_model_profile is not None
    _, saved_profile, _ = service.saved_model_profile
    assert saved_profile["provider"] == ProviderType.BIGMODEL.value


def test_save_model_profile_accepts_minimax_provider() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/minimax",
        json={
            "provider": ProviderType.MINIMAX.value,
            "model": "MiniMax-M1-80k",
            "base_url": "https://api.minimaxi.com/v1",
            "api_key": "secret",
            "temperature": 0.2,
            "top_p": 0.9,
            "max_tokens": 4096,
        },
    )

    assert response.status_code == 200
    assert service.saved_model_profile is not None
    _, saved_profile, _ = service.saved_model_profile
    assert saved_profile["provider"] == ProviderType.MINIMAX.value


def test_save_model_profile_accepts_source_name_for_rename() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/renamed",
        json={
            "source_name": "default",
            "provider": ProviderType.OPENAI_COMPATIBLE.value,
            "model": "kimi-k2.5",
            "base_url": "https://api.moonshot.cn/v1",
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": 4096,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_model_profile is not None
    saved_name, saved_profile, source_name = service.saved_model_profile
    assert saved_name == "renamed"
    assert saved_profile["model"] == "kimi-k2.5"
    assert source_name == "default"


def test_save_model_profile_includes_default_flag_when_present() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/kimi",
        json={
            "provider": ProviderType.OPENAI_COMPATIBLE.value,
            "model": "kimi-k2.5",
            "base_url": "https://api.moonshot.cn/v1",
            "api_key": "secret",
            "is_default": True,
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": 4096,
        },
    )

    assert response.status_code == 200
    assert service.saved_model_profile is not None
    _, saved_profile, _ = service.saved_model_profile
    assert saved_profile["is_default"] is True


def test_save_model_profile_forwards_headers() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/default",
        json={
            "provider": ProviderType.OPENAI_COMPATIBLE.value,
            "model": "claude-proxy",
            "base_url": "https://example.test/v1",
            "headers": [
                {
                    "name": "Authorization",
                    "value": "Bearer from-header",
                    "secret": True,
                }
            ],
            "temperature": 0.2,
            "top_p": 1.0,
            "max_tokens": 2048,
        },
    )

    assert response.status_code == 200
    assert service.saved_model_profile is not None
    _, saved_profile, _ = service.saved_model_profile
    saved_headers = saved_profile["headers"]
    assert isinstance(saved_headers, list)
    first_header = saved_headers[0]
    assert isinstance(first_header, dict)
    first_header_payload = first_header
    assert cast(dict[str, JsonValue], first_header_payload)["name"] == "Authorization"


class _FakeEnvironmentVariableService:
    def __init__(self) -> None:
        self.saved_payload: dict[str, str] | None = None
        self.deleted_key: tuple[str, str] | None = None
        self.permission_error: PermissionError | None = None

    def list_environment_variables(self) -> dict[str, object]:
        return {
            "system": [
                {
                    "key": "ComSpec",
                    "value": r"%SystemRoot%\\system32\\cmd.exe",
                    "scope": "system",
                    "value_kind": "expandable",
                }
            ],
            "app": [
                {
                    "key": "OPENAI_API_KEY",
                    "value": "secret",
                    "scope": "app",
                    "value_kind": "string",
                }
            ],
        }

    def save_environment_variable(
        self,
        *,
        scope: object,
        key: str,
        request: object,
    ) -> dict[str, str]:
        if self.permission_error is not None:
            raise self.permission_error
        source_key = getattr(request, "source_key")
        value = getattr(request, "value")
        self.saved_payload = {
            "scope": str(getattr(scope, "value", scope)),
            "key": key,
            "source_key": "" if source_key is None else str(source_key),
            "value": value,
        }
        return {
            "key": key,
            "value": value,
            "scope": str(getattr(scope, "value", scope)),
            "value_kind": "string",
        }

    def delete_environment_variable(self, *, scope: object, key: str) -> None:
        if self.permission_error is not None:
            raise self.permission_error
        self.deleted_key = (str(getattr(scope, "value", scope)), key)


def _create_env_test_client(fake_service: object) -> TestClient:
    app = FastAPI()
    app.include_router(system.router, prefix="/api")
    app.dependency_overrides[get_environment_variable_service] = lambda: fake_service
    return TestClient(app)


def test_get_environment_variables() -> None:
    client = _create_env_test_client(_FakeEnvironmentVariableService())

    response = client.get("/api/system/configs/environment-variables")

    assert response.status_code == 200
    payload = response.json()
    assert payload["system"][0]["key"] == "ComSpec"
    assert payload["app"][0]["scope"] == "app"


def test_save_environment_variable() -> None:
    service = _FakeEnvironmentVariableService()
    client = _create_env_test_client(service)

    response = client.put(
        "/api/system/configs/environment-variables/app/OPENAI_API_KEY",
        json={
            "source_key": "OPENAI_KEY",
            "value": "updated-secret",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "key": "OPENAI_API_KEY",
        "value": "updated-secret",
        "scope": "app",
        "value_kind": "string",
    }
    assert service.saved_payload == {
        "scope": "app",
        "key": "OPENAI_API_KEY",
        "source_key": "OPENAI_KEY",
        "value": "updated-secret",
    }


def test_delete_environment_variable_returns_forbidden_on_permission_error() -> None:
    service = _FakeEnvironmentVariableService()
    service.permission_error = PermissionError(
        "System-level environment access denied."
    )
    client = _create_env_test_client(service)

    response = client.delete(
        "/api/system/configs/environment-variables/system/Path",
    )

    assert response.status_code == 403
    assert "access denied" in response.json()["detail"].lower()
