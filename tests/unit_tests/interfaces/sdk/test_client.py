# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.interfaces.sdk.client import AgentTeamsClient


def test_reload_proxy_config_calls_expected_endpoint(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.reload_proxy_config()

    assert response == {"status": "ok"}
    assert captured == {
        "method": "POST",
        "path": "/api/system/configs/proxy:reload",
        "payload": None,
    }


def test_probe_web_connectivity_passes_timeout_payload(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.probe_web_connectivity(
        url="https://example.com",
        timeout_ms=2500,
    )

    assert response == {"ok": True}
    assert captured == {
        "method": "POST",
        "path": "/api/system/configs/web:probe",
        "payload": {"url": "https://example.com", "timeout_ms": 2500},
    }


def test_get_proxy_config_calls_expected_endpoint(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"http_proxy": "http://proxy.example:8080"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.get_proxy_config()

    assert response == {"http_proxy": "http://proxy.example:8080"}
    assert captured == {
        "method": "GET",
        "path": "/api/system/configs/proxy",
        "payload": None,
    }


def test_get_web_config_calls_expected_endpoint(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {
            "provider": "exa",
            "exa_api_key": None,
            "fallback_provider": "searxng",
            "searxng_instance_url": "https://search.mdosch.de/",
            "searxng_instance_seeds": [
                "https://search.mdosch.de/",
                "https://search.seddens.net/",
                "https://search.wdpserver.com/",
            ],
        }

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.get_web_config()

    assert response == {
        "provider": "exa",
        "exa_api_key": None,
        "fallback_provider": "searxng",
        "searxng_instance_url": "https://search.mdosch.de/",
        "searxng_instance_seeds": [
            "https://search.mdosch.de/",
            "https://search.seddens.net/",
            "https://search.wdpserver.com/",
        ],
    }
    assert captured == {
        "method": "GET",
        "path": "/api/system/configs/web",
        "payload": None,
    }


def test_get_github_config_calls_expected_endpoint(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"token": None}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.get_github_config()

    assert response == {"token": None}
    assert captured == {
        "method": "GET",
        "path": "/api/system/configs/github",
        "payload": None,
    }


def test_save_proxy_config_passes_proxy_payload(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.save_proxy_config(
        http_proxy="http://proxy.example:8080",
        https_proxy="http://proxy.example:8443",
        no_proxy="localhost,127.0.0.1",
        proxy_username="alice",
        proxy_password="secret",
    )

    assert response == {"status": "ok"}
    assert captured == {
        "method": "PUT",
        "path": "/api/system/configs/proxy",
        "payload": {
            "http_proxy": "http://proxy.example:8080",
            "https_proxy": "http://proxy.example:8443",
            "all_proxy": None,
            "no_proxy": "localhost,127.0.0.1",
            "proxy_username": "alice",
            "proxy_password": "secret",
            "ssl_verify": None,
        },
    }


def test_save_web_config_passes_web_payload(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.save_web_config(
        provider="exa",
        exa_api_key="secret",
        fallback_provider="searxng",
        searxng_instance_url="https://search.example.test/",
    )

    assert response == {"status": "ok"}
    assert captured == {
        "method": "PUT",
        "path": "/api/system/configs/web",
        "payload": {
            "provider": "exa",
            "exa_api_key": "secret",
            "fallback_provider": "searxng",
            "searxng_instance_url": "https://search.example.test/",
        },
    }


def test_save_web_config_defaults_to_searxng_fallback_provider(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.save_web_config(
        provider="exa",
        exa_api_key="secret",
        searxng_instance_url="https://search.example.test/",
    )

    assert response == {"status": "ok"}
    assert captured == {
        "method": "PUT",
        "path": "/api/system/configs/web",
        "payload": {
            "provider": "exa",
            "exa_api_key": "secret",
            "fallback_provider": "searxng",
            "searxng_instance_url": "https://search.example.test/",
        },
    }


def test_save_github_config_passes_payload(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.save_github_config(token="ghp_secret")

    assert response == {"status": "ok"}
    assert captured == {
        "method": "PUT",
        "path": "/api/system/configs/github",
        "payload": {
            "token": "ghp_secret",
        },
    }


def test_probe_web_connectivity_includes_proxy_override(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.probe_web_connectivity(
        url="https://example.com",
        timeout_ms=2500,
        https_proxy="http://proxy.example:8443",
        no_proxy="localhost,127.0.0.1",
        proxy_username="alice",
        proxy_password="secret",
    )

    assert response == {"ok": True}
    assert captured == {
        "method": "POST",
        "path": "/api/system/configs/web:probe",
        "payload": {
            "url": "https://example.com",
            "timeout_ms": 2500,
            "proxy_override": {
                "http_proxy": None,
                "https_proxy": "http://proxy.example:8443",
                "all_proxy": None,
                "no_proxy": "localhost,127.0.0.1",
                "proxy_username": "alice",
                "proxy_password": "secret",
                "ssl_verify": None,
            },
        },
    }


def test_probe_github_connectivity_passes_payload(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.probe_github_connectivity(
        token="ghp_secret",
        timeout_ms=2500,
    )

    assert response == {"ok": True}
    assert captured == {
        "method": "POST",
        "path": "/api/system/configs/github:probe",
        "payload": {
            "token": "ghp_secret",
            "timeout_ms": 2500,
        },
    }


def test_create_run_includes_target_role_id(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"run_id": "run-1", "session_id": "session-1"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    handle = client.create_run(
        input="hello",
        session_id="session-1",
        target_role_id="writer",
    )

    assert handle.run_id == "run-1"
    assert handle.session_id == "session-1"
    assert captured == {
        "method": "POST",
        "path": "/api/runs",
        "payload": {
            "session_id": "session-1",
            "input": [{"kind": "text", "text": "hello"}],
            "execution_mode": "ai",
            "yolo": False,
            "target_role_id": "writer",
        },
    }


def test_external_agent_sdk_calls_expected_endpoints(monkeypatch) -> None:
    client = AgentTeamsClient()
    calls: list[tuple[str, str, object | None]] = []

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object] | list[object]:
        calls.append((method, path, payload))
        if method == "GET" and path == "/api/system/configs/agents":
            return [{"agent_id": "codex_local"}]
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    assert client.list_external_agents() == [{"agent_id": "codex_local"}]
    assert client.get_external_agent("codex_local") == {"status": "ok"}
    assert client.save_external_agent("codex_local", {"agent_id": "codex_local"}) == {
        "status": "ok"
    }
    assert client.test_external_agent("codex_local") == {"status": "ok"}
    assert client.delete_external_agent("codex_local") == {"status": "ok"}
    assert calls == [
        ("GET", "/api/system/configs/agents", None),
        ("GET", "/api/system/configs/agents/codex_local", None),
        ("PUT", "/api/system/configs/agents/codex_local", {"agent_id": "codex_local"}),
        ("POST", "/api/system/configs/agents/codex_local:test", {}),
        ("DELETE", "/api/system/configs/agents/codex_local", None),
    ]
