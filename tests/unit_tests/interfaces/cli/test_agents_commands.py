# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from typer.testing import CliRunner

from relay_teams.interfaces.cli import app as cli_app

runner = CliRunner()


def test_agent_runtimes_list_supports_json_output(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_autostart(
        base_url: str, autostart: bool, daemon: bool = False, force: bool = False
    ) -> None:
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

    result = runner.invoke(cli_app.app, ["agent-runtimes", "list", "--format", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [
        {
            "agent_id": "codex_local",
            "name": "Codex Local",
            "description": "Runs Codex via stdio",
            "transport": "stdio",
        }
    ]
    assert calls == [("GET", "/api/system/configs/agent-runtimes", None)]


def test_agent_runtimes_save_and_delete_call_expected_endpoints(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_autostart(
        base_url: str, autostart: bool, daemon: bool = False, force: bool = False
    ) -> None:
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
            "agent-runtimes",
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
    delete_result = runner.invoke(
        cli_app.app,
        ["agent-runtimes", "delete", "codex_local"],
    )

    assert save_result.exit_code == 0
    assert delete_result.exit_code == 0
    assert calls == [
        (
            "PUT",
            "/api/system/configs/agent-runtimes/codex_local",
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
        ("DELETE", "/api/system/configs/agent-runtimes/codex_local", None),
    ]


def test_agent_runtimes_commands_encode_agent_id_path(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    agent_id = "team/codex runtime?#1"

    def fake_autostart(
        base_url: str, autostart: bool, daemon: bool = False, force: bool = False
    ) -> None:
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
        if path.endswith(":test"):
            return {"ok": True, "message": "Connected"}
        if method == "GET":
            return {
                "agent_id": agent_id,
                "name": "Codex Local",
                "description": "Runs Codex via stdio",
                "protocol": "cli",
                "transport": {
                    "transport": "stdio",
                    "command": "codex",
                    "args": [],
                },
            }
        return {"status": "ok"}

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    get_result = runner.invoke(
        cli_app.app,
        ["agent-runtimes", "get", agent_id, "--format", "json"],
    )
    save_result = runner.invoke(
        cli_app.app,
        [
            "agent-runtimes",
            "save",
            agent_id,
            "--config-json",
            json.dumps(
                {
                    "agent_id": agent_id,
                    "name": "Codex Local",
                    "description": "Runs Codex via stdio",
                    "protocol": "cli",
                    "transport": {
                        "transport": "stdio",
                        "command": "codex",
                        "args": [],
                    },
                }
            ),
        ],
    )
    delete_result = runner.invoke(cli_app.app, ["agent-runtimes", "delete", agent_id])
    test_result = runner.invoke(
        cli_app.app,
        ["agent-runtimes", "test", agent_id, "--format", "json"],
    )

    assert get_result.exit_code == 0
    assert save_result.exit_code == 0
    assert delete_result.exit_code == 0
    assert test_result.exit_code == 0
    encoded_path = "/api/system/configs/agent-runtimes/team%2Fcodex%20runtime%3F%231"
    assert calls == [
        ("GET", encoded_path, None),
        (
            "PUT",
            encoded_path,
            {
                "agent_id": agent_id,
                "name": "Codex Local",
                "description": "Runs Codex via stdio",
                "protocol": "cli",
                "transport": {
                    "transport": "stdio",
                    "command": "codex",
                    "args": [],
                },
            },
        ),
        ("DELETE", encoded_path, None),
        ("POST", f"{encoded_path}:test", None),
    ]


def test_agent_runtimes_test_supports_table_output(monkeypatch) -> None:
    def fake_autostart(
        base_url: str, autostart: bool, daemon: bool = False, force: bool = False
    ) -> None:
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

    result = runner.invoke(cli_app.app, ["agent-runtimes", "test", "codex_local"])

    assert result.exit_code == 0
    assert "Agent Runtime: codex_local" in result.stdout
    assert "OK: True" in result.stdout
    assert "Message: Connected" in result.stdout
