# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_teams.interfaces.server.deps import get_system_config_service
from agent_teams.interfaces.server.routers import system
from agent_teams.providers.model_config import ProviderModelInfo, ProviderType


class _FakeSystemService:
    def __init__(self) -> None:
        self.saved_notification_config: dict[str, object] | None = None

    def get_config_status(self) -> dict[str, object]:
        return {"model": {"loaded": True}}

    def get_model_config(self) -> dict[str, object]:
        return {}

    def get_model_profiles(self) -> dict[str, object]:
        return {}

    def save_model_profile(self, _name: str, _profile: dict[str, object]) -> None:
        return None

    def delete_model_profile(self, _name: str) -> None:
        return None

    def save_model_config(self, _config: dict[str, object]) -> None:
        return None

    def reload_model_config(self) -> None:
        return None

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


def _create_test_client(fake_service: object) -> TestClient:
    app = FastAPI()
    app.include_router(system.router, prefix="/api")
    app.dependency_overrides[get_system_config_service] = lambda: fake_service
    return TestClient(app)


def test_get_notification_config() -> None:
    client = _create_test_client(_FakeSystemService())
    response = client.get("/api/system/configs/notifications")
    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_approval_requested"]["enabled"] is True
    assert payload["run_completed"]["channels"] == ["toast"]


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
