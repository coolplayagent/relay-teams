# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from typer.testing import CliRunner

from agent_teams.interfaces.cli import app as cli_app

runner = CliRunner()


def test_agents_list_supports_json_output(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_autostart(base_url: str, autostart: bool) -> None:
        _ = (base_url, autostart)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, timeout_seconds)
        calls.append((method, path, payload))
        return [
            {
                "agent_id": "codex_local",
                "name": "Codex Local",
                "description": "Runs Codex via stdio",
                "transport": "stdio",
            }
        ]

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(cli_app.app, ["agents", "list", "--format", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [
        {
            "agent_id": "codex_local",
            "name": "Codex Local",
            "description": "Runs Codex via stdio",
            "transport": "stdio",
        }
    ]
    assert calls == [("GET", "/api/system/configs/agents", None)]


def test_agents_save_and_delete_call_expected_endpoints(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_autostart(base_url: str, autostart: bool) -> None:
        _ = (base_url, autostart)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, timeout_seconds)
        calls.append((method, path, payload))
        return {"status": "ok"}

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    save_result = runner.invoke(
        cli_app.app,
        [
            "agents",
            "save",
            "codex_local",
            "--config-json",
            json.dumps(
                {
                    "agent_id": "codex_local",
                    "name": "Codex Local",
                    "description": "Runs Codex via stdio",
                    "transport": {
                        "transport": "stdio",
                        "command": "codex",
                        "args": [],
                    },
                }
            ),
        ],
    )
    delete_result = runner.invoke(cli_app.app, ["agents", "delete", "codex_local"])

    assert save_result.exit_code == 0
    assert delete_result.exit_code == 0
    assert calls == [
        (
            "PUT",
            "/api/system/configs/agents/codex_local",
            {
                "agent_id": "codex_local",
                "name": "Codex Local",
                "description": "Runs Codex via stdio",
                "transport": {
                    "transport": "stdio",
                    "command": "codex",
                    "args": [],
                },
            },
        ),
        ("DELETE", "/api/system/configs/agents/codex_local", None),
    ]


def test_agents_test_supports_table_output(monkeypatch) -> None:
    def fake_autostart(base_url: str, autostart: bool) -> None:
        _ = (base_url, autostart)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, timeout_seconds, method, path, payload)
        return {
            "ok": True,
            "message": "Connected",
            "agent_name": "Codex",
            "agent_version": "1.0.0",
            "protocol_version": 1,
        }

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(cli_app.app, ["agents", "test", "codex_local"])

    assert result.exit_code == 0
    assert "Agent: codex_local" in result.stdout
    assert "OK: True" in result.stdout
    assert "Message: Connected" in result.stdout
