# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from typer.testing import CliRunner

from relay_teams.interfaces.cli import app as cli_app

runner = CliRunner()


def test_clawhub_config_commands_call_expected_endpoints(monkeypatch) -> None:
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
        return {"status": "ok", "token": None}

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    get_result = runner.invoke(
        cli_app.app,
        ["clawhub", "config", "get", "--format", "json"],
    )
    save_result = runner.invoke(
        cli_app.app,
        ["clawhub", "config", "save", "--token", "ch_secret"],
    )

    assert get_result.exit_code == 0
    assert json.loads(get_result.stdout) == {"status": "ok", "token": None}
    assert save_result.exit_code == 0
    assert calls == [
        ("GET", "/api/system/configs/clawhub", None),
        ("PUT", "/api/system/configs/clawhub", {"token": "ch_secret"}),
    ]


def test_clawhub_skill_commands_call_expected_endpoints(monkeypatch) -> None:
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
        if method == "GET" and path == "/api/system/configs/clawhub/skills":
            return [{"skill_id": "skill-creator-2"}]
        return {"status": "ok"}

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    list_result = runner.invoke(
        cli_app.app,
        ["clawhub", "skills", "list", "--format", "json"],
    )
    save_result = runner.invoke(
        cli_app.app,
        [
            "clawhub",
            "skills",
            "save",
            "skill-creator-2",
            "--config-json",
            json.dumps({"runtime_name": "skill-creator"}),
        ],
    )
    delete_result = runner.invoke(
        cli_app.app,
        ["clawhub", "skills", "delete", "skill-creator-2"],
    )

    assert list_result.exit_code == 0
    assert json.loads(list_result.stdout) == [{"skill_id": "skill-creator-2"}]
    assert save_result.exit_code == 0
    assert delete_result.exit_code == 0
    assert calls == [
        ("GET", "/api/system/configs/clawhub/skills", None),
        (
            "PUT",
            "/api/system/configs/clawhub/skills/skill-creator-2",
            {"runtime_name": "skill-creator"},
        ),
        ("DELETE", "/api/system/configs/clawhub/skills/skill-creator-2", None),
    ]


def test_clawhub_config_save_requires_token_or_clear(monkeypatch) -> None:
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
        ["clawhub", "config", "save"],
    )

    assert save_result.exit_code == 2
    assert calls == []
