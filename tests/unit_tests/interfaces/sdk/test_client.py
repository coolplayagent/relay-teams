# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.interfaces.sdk.client import AgentTeamsClient


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
            },
        },
    }
