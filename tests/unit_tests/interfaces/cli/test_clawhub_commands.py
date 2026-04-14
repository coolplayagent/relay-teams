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
        if method == "POST" and path == "/api/system/configs/clawhub/skills:install":
            return {
                "ok": True,
                "slug": "skill-creator-2",
                "requested_version": "v1.2.3",
                "installed_skill": {
                    "skill_id": "skill-creator-2",
                    "runtime_name": "skill-creator",
                    "description": "Create skills.",
                    "ref": "app:skill-creator",
                    "scope": "app",
                    "directory": "/tmp/.relay-teams/skills/skill-creator-2",
                    "manifest_path": "/tmp/.relay-teams/skills/skill-creator-2/SKILL.md",
                    "valid": True,
                    "error": None,
                },
                "latency_ms": 63,
                "checked_at": "2026-04-09T12:05:00Z",
                "diagnostics": {
                    "binary_available": True,
                    "token_configured": False,
                    "installation_attempted": False,
                    "installed_during_install": False,
                    "registry": "https://mirror-cn.clawhub.com",
                    "workdir": "/tmp/.relay-teams",
                    "skills_reloaded": True,
                },
                "retryable": False,
                "error_code": None,
                "error_message": None,
            }
        return {"status": "ok"}

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    list_result = runner.invoke(
        cli_app.app,
        ["clawhub", "skills", "list", "--format", "json"],
    )
    install_result = runner.invoke(
        cli_app.app,
        [
            "clawhub",
            "skills",
            "install",
            "skill-creator-2",
            "--version",
            "v1.2.3",
            "--force",
            "--format",
            "json",
        ],
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
    assert install_result.exit_code == 0
    assert json.loads(install_result.stdout)["installed_skill"]["ref"] == (
        "app:skill-creator"
    )
    assert save_result.exit_code == 0
    assert delete_result.exit_code == 0
    assert calls == [
        ("GET", "/api/system/configs/clawhub/skills", None),
        (
            "POST",
            "/api/system/configs/clawhub/skills:install",
            {"slug": "skill-creator-2", "force": True, "version": "v1.2.3"},
        ),
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
