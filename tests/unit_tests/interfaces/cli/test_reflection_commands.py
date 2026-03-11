# -*- coding: utf-8 -*-
from __future__ import annotations

from typer.testing import CliRunner

from agent_teams.interfaces.cli import app as cli_app

runner = CliRunner()


def test_reflection_jobs_list_builds_expected_request(monkeypatch) -> None:
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
        return [
            {
                "job_id": "rjob-1",
                "job_type": "daily_reflection",
                "status": "queued",
                "role_id": "writer_agent",
                "instance_id": "inst-1",
                "trigger_date": "2026-03-11",
            }
        ]

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(
        cli_app.app, ["reflection", "jobs", "list", "--format", "json"]
    )

    assert result.exit_code == 0
    assert captured == {
        "base_url": cli_app.DEFAULT_BASE_URL,
        "autostart": True,
        "method": "GET",
        "path": "/api/reflection/jobs?limit=50",
        "payload": None,
    }
    assert '"job_id": "rjob-1"' in result.output


def test_root_help_lists_reflection_module() -> None:
    result = runner.invoke(cli_app.app, ["--help"])
    assert result.exit_code == 0
    assert "reflection" in result.output
