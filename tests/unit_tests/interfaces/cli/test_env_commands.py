# -*- coding: utf-8 -*-
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from relay_teams.env.env_cli import EnvListEntry
from relay_teams.interfaces.cli import env_commands
from relay_teams.interfaces.cli import app as cli_app

runner = CliRunner()


def test_env_list_outputs_json_with_prefix_and_secret_options(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_collect_env_entries(
        *, prefix: str | None, show_secrets: bool
    ) -> list[EnvListEntry]:
        captured["prefix"] = prefix
        captured["show_secrets"] = show_secrets
        return [
            EnvListEntry(
                key="FOO_TOKEN",
                value="secret",
                source="app",
                masked=False,
            )
        ]

    monkeypatch.setattr(env_commands, "collect_env_entries", fake_collect_env_entries)

    result = runner.invoke(
        cli_app.app,
        [
            "env",
            "list",
            "--format",
            "json",
            "--show-secrets",
            "--prefix",
            "FOO",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == [
        {
            "key": "FOO_TOKEN",
            "value": "secret",
            "source": "app",
            "masked": False,
        }
    ]
    assert captured == {"prefix": "FOO", "show_secrets": True}


def test_env_proxy_reload_uses_interface_http_client_path(monkeypatch) -> None:
    autostart_calls: list[tuple[str, bool, bool, bool]] = []
    request_calls: list[tuple[str, str, str, dict[str, object] | None]] = []

    def fake_autostart(
        base_url: str, autostart: bool, daemon: bool = False, force: bool = False
    ) -> None:
        autostart_calls.append((base_url, autostart, daemon, force))

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object] | list[object]:
        request_calls.append((base_url, method, path, payload))
        return {"reloaded": True}

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(
        cli_app.app,
        [
            "env",
            "proxy-reload",
            "--base-url",
            "http://127.0.0.1:8123",
            "--no-autostart",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"reloaded": True}
    assert autostart_calls == [("http://127.0.0.1:8123", False, False, False)]
    assert request_calls == [
        (
            "http://127.0.0.1:8123",
            "POST",
            "/api/system/configs/proxy:reload",
            None,
        )
    ]


def test_env_probe_web_sends_probe_payload_and_outputs_json(monkeypatch) -> None:
    request_calls: list[tuple[str, str, str, dict[str, object] | None]] = []

    def fake_autostart(
        base_url: str, autostart: bool, daemon: bool = False, force: bool = False
    ) -> None:
        _ = (base_url, autostart, daemon, force)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object] | list[object]:
        request_calls.append((base_url, method, path, payload))
        return {
            "ok": True,
            "url": "https://example.com",
            "final_url": "https://example.com/",
            "used_method": "HEAD",
            "status_code": 200,
            "latency_ms": 12,
            "diagnostics": {"used_proxy": True, "redirected": False},
        }

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(
        cli_app.app,
        [
            "env",
            "probe-web",
            "https://example.com",
            "--timeout-ms",
            "2500",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["ok"] is True
    assert request_calls == [
        (
            "http://127.0.0.1:8000",
            "POST",
            "/api/system/configs/web:probe",
            {"url": "https://example.com", "timeout_ms": 2500},
        )
    ]


def test_env_probe_web_renders_table(monkeypatch) -> None:
    def fake_autostart(
        base_url: str, autostart: bool, daemon: bool = False, force: bool = False
    ) -> None:
        _ = (base_url, autostart, daemon, force)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, method, path, payload)
        return {
            "ok": False,
            "url": "https://example.com",
            "final_url": "https://example.com",
            "used_method": "GET",
            "status_code": 502,
            "latency_ms": 42,
            "error_code": "bad_gateway",
            "error_message": "proxy returned 502",
            "diagnostics": {"used_proxy": True, "redirected": True},
        }

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(
        cli_app.app,
        ["env", "probe-web", "https://example.com"],
    )

    assert result.exit_code == 0
    assert "Used Proxy" in result.output
    assert "true" in result.output
    assert "proxy returned 502" in result.output


def test_require_object_response_rejects_list_payload() -> None:
    with pytest.raises(RuntimeError, match="Expected JSON object"):
        env_commands._require_object_response([])
