# -*- coding: utf-8 -*-
from __future__ import annotations

from typer.testing import CliRunner

from agent_teams.interfaces.cli import app as cli_app

runner = CliRunner()


def test_roles_prompt_builds_preview_payload(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_autostart(base_url: str, autostart: bool) -> None:
        captured["base_url"] = base_url
        captured["autostart"] = autostart

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, timeout_seconds)
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {
            "role_id": "coordinator_agent",
            "objective": "Draft release note",
            "tools": ["dispatch_task"],
            "skills": ["time"],
            "runtime_system_prompt": "runtime",
            "provider_system_prompt": "provider",
            "user_prompt": "user",
        }

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(
        cli_app.app,
        [
            "roles",
            "prompt",
            "--role-id",
            "coordinator_agent",
            "--objective",
            "Draft release note",
            "--tool",
            "dispatch_task",
            "--skill",
            "time",
            "--shared-state-json",
            '{"lang":"zh-CN"}',
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "base_url": cli_app.DEFAULT_BASE_URL,
        "autostart": True,
        "method": "POST",
        "path": "/api/prompts:preview",
        "payload": {
            "role_id": "coordinator_agent",
            "objective": "Draft release note",
            "shared_state": {"lang": "zh-CN"},
            "tools": ["dispatch_task"],
            "skills": ["time"],
        },
    }
    assert '"provider_system_prompt": "provider"' in result.output


def test_roles_prompt_without_role_id_shows_available_roles(monkeypatch) -> None:
    captured: list[str] = []

    def fake_autostart(base_url: str, autostart: bool) -> None:
        _ = (base_url, autostart)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, method, payload, timeout_seconds)
        captured.append(path)
        return [
            {"role_id": "coordinator_agent"},
            {"role_id": "writer_agent"},
        ]

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(cli_app.app, ["roles", "prompt"])

    assert result.exit_code == 2
    assert captured == ["/api/roles"]
    assert "Missing required option: --role-id" in result.output
    assert "coordinator_agent" in result.output
    assert "Usage: agent-teams roles prompt --role-id <role_id>" in result.output


def test_roles_prompt_default_output_prints_full_prompt(monkeypatch) -> None:
    def fake_autostart(base_url: str, autostart: bool) -> None:
        _ = (base_url, autostart)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, method, path, payload, timeout_seconds)
        return {
            "role_id": "coordinator_agent",
            "objective": "Draft release note",
            "tools": ["dispatch_task"],
            "skills": ["time"],
            "runtime_system_prompt": "runtime line",
            "provider_system_prompt": "provider line",
            "user_prompt": "user line",
        }

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(
        cli_app.app,
        [
            "roles",
            "prompt",
            "--role-id",
            "coordinator_agent",
        ],
    )

    assert result.exit_code == 0
    assert "provider line" in result.output
    assert result.output.count("provider line") == 1
    assert "runtime line" not in result.output
    assert "user line" in result.output
    assert "role_id:" not in result.output
    assert "+-" not in result.output
