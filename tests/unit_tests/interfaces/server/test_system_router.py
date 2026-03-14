# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_teams.env.proxy_env import ProxyEnvInput
from agent_teams.env.web_connectivity import WebConnectivityProbeResult
from agent_teams.interfaces.server.deps import (
    get_config_status_service,
    get_environment_variable_service,
    get_mcp_config_reload_service,
    get_model_config_service,
    get_notification_settings_service,
    get_proxy_config_service,
    get_skills_config_reload_service,
)
from agent_teams.interfaces.server.routers import system
from agent_teams.providers.model_connectivity import (
    ModelConnectivityProbeResult,
    ModelDiscoveryResult,
)
from agent_teams.providers.model_config import ProviderModelInfo, ProviderType


class _FakeSystemService:
    def __init__(self) -> None:
        self.saved_notification_config: dict[str, object] | None = None
        self.saved_model_profile: tuple[str, dict[str, object], str | None] | None = (
            None
        )
        self.saved_proxy_config: dict[str, object] | None = None
        self.proxy_save_error: RuntimeError | None = None

    def get_config_status(self) -> dict[str, object]:
        return {"model": {"loaded": True}}

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
                "is_default": True,
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

    def reload_mcp_config(self) -> None:
        return None

    def reload_skills_config(self) -> None:
        return None

    def get_notification_config(self) -> dict[str, object]:
        return {
            "tool_approval_requested": {
                "enabled": True,
                "channels": ["browser", "toast"],
            },
            "run_completed": {"enabled": False, "channels": ["toast"]},
            "run_failed": {"enabled": True, "channels": ["browser", "toast"]},
            "run_stopped": {"enabled": False, "channels": ["toast"]},
        }

    def save_notification_config(self, config: dict[str, object]) -> None:
        self.saved_notification_config = config

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
        _request: object,
    ) -> ModelConnectivityProbeResult:
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
    app.dependency_overrides[get_mcp_config_reload_service] = lambda: fake_service
    app.dependency_overrides[get_skills_config_reload_service] = lambda: fake_service
    app.dependency_overrides[get_proxy_config_service] = lambda: fake_service
    return TestClient(app)


def test_get_notification_config() -> None:
    client = _create_test_client(_FakeSystemService())
    response = client.get("/api/system/configs/notifications")
    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_approval_requested"]["enabled"] is True
    assert payload["run_completed"]["channels"] == ["toast"]


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
            "connect_timeout_seconds": 25.0,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_model_profile is not None
    _, saved_profile, source_name = service.saved_model_profile
    assert saved_profile["connect_timeout_seconds"] == 25.0
    assert source_name is None


def test_save_notification_config() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)
    request_payload = {
        "config": {
            "tool_approval_requested": {
                "enabled": True,
                "channels": ["browser", "toast"],
            },
            "run_completed": {"enabled": True, "channels": ["toast"]},
            "run_failed": {"enabled": True, "channels": ["browser", "toast"]},
            "run_stopped": {"enabled": True, "channels": ["toast"]},
        }
    }
    response = client.put("/api/system/configs/notifications", json=request_payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_notification_config is not None
    run_completed = service.saved_notification_config["run_completed"]
    assert isinstance(run_completed, dict)
    assert run_completed["enabled"] is True


def test_get_provider_models() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/model/providers/models")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert payload[0]["profile"] == "default"


def test_get_model_profiles_returns_api_key() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/model/profiles")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default"]["api_key"] == "secret"
    assert payload["default"]["has_api_key"] is True
    assert payload["default"]["is_default"] is True


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
