from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.hooks import HooksConfig
from relay_teams.interfaces.server.deps import get_hook_service
from relay_teams.interfaces.server.routers import system


class _FakeHookService:
    def __init__(self) -> None:
        self.saved: dict[str, object] | None = None

    def get_user_config(self) -> HooksConfig:
        return HooksConfig.model_validate(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "shell",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python policy.py",
                                }
                            ],
                        }
                    ]
                }
            }
        )

    def get_runtime_view(self) -> dict[str, object]:
        return {
            "sources": [
                {
                    "scope": "project",
                    "path": "/workspace/.relay-teams/hooks.json",
                }
            ],
            "loaded_hooks": [
                {
                    "name": "python policy.py",
                    "handler_type": "command",
                    "event_name": "PreToolUse",
                    "matcher": "shell",
                    "if_rule": None,
                    "role_ids": [],
                    "session_modes": [],
                    "run_kinds": [],
                    "timeout_seconds": 5.0,
                    "run_async": False,
                    "on_error": "ignore",
                    "source": {
                        "scope": "project",
                        "path": "/workspace/.relay-teams/hooks.json",
                    },
                }
            ],
        }

    def save_user_config(self, payload: object) -> HooksConfig:
        config = HooksConfig.model_validate(payload)
        self.saved = config.model_dump(mode="json")
        return config

    def validate_config(self, payload: object) -> HooksConfig:
        return HooksConfig.model_validate(payload)


def _create_client(service: _FakeHookService) -> TestClient:
    app = FastAPI()
    app.include_router(system.router, prefix="/api")
    app.dependency_overrides[get_hook_service] = lambda: service
    return TestClient(app)


def test_get_hooks_config() -> None:
    client = _create_client(_FakeHookService())

    response = client.get("/api/system/configs/hooks")

    assert response.status_code == 200
    payload = response.json()
    assert payload["hooks"]["PreToolUse"][0]["matcher"] == "shell"


def test_get_hooks_runtime_view() -> None:
    client = _create_client(_FakeHookService())

    response = client.get("/api/system/configs/hooks/runtime")

    assert response.status_code == 200
    payload = response.json()
    assert payload["sources"][0]["scope"] == "project"
    assert payload["loaded_hooks"][0]["name"] == "python policy.py"
    assert payload["loaded_hooks"][0]["event_name"] == "PreToolUse"


def test_save_hooks_config() -> None:
    service = _FakeHookService()
    client = _create_client(service)

    response = client.put(
        "/api/system/configs/hooks",
        json={
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "http",
                                "url": "https://hooks.example.test/check",
                            }
                        ],
                    }
                ]
            }
        },
    )

    assert response.status_code == 200
    assert service.saved is not None
    hooks = service.saved.get("hooks")
    assert isinstance(hooks, dict)
    assert "UserPromptSubmit" in hooks


def test_validate_hooks_config() -> None:
    client = _create_client(_FakeHookService())

    response = client.post(
        "/api/system/configs/hooks:validate",
        json={
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "echo ok",
                            }
                        ],
                    }
                ]
            }
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
