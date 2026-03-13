# -*- coding: utf-8 -*-
from __future__ import annotations

from typer.testing import CliRunner

from agent_teams.interfaces.cli import app as cli_app

runner = CliRunner()


def test_root_message_runs_single_prompt(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    streamed: dict[str, object] = {}

    def fake_autostart(base_url: str, autostart: bool) -> None:
        streamed["base_url"] = base_url
        streamed["autostart"] = autostart

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, timeout_seconds)
        calls.append((method, path, payload))
        if path == "/api/sessions":
            return {"session_id": "session-1"}
        if path == "/api/runs":
            return {"run_id": "run-1"}
        raise AssertionError(f"unexpected path: {path}")

    def fake_stream(base_url: str, run_id: str, debug: bool) -> None:
        streamed["stream_base_url"] = base_url
        streamed["run_id"] = run_id
        streamed["debug"] = debug

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)
    monkeypatch.setattr(cli_app, "_stream_events", fake_stream)

    result = runner.invoke(cli_app.app, ["-m", "hello"])

    assert result.exit_code == 0
    assert calls == [
        ("POST", "/api/sessions", {"workspace_id": "default"}),
        (
            "POST",
            "/api/runs",
            {
                "session_id": "session-1",
                "intent": "hello",
                "execution_mode": "ai",
            },
        ),
    ]
    assert streamed == {
        "base_url": cli_app.DEFAULT_BASE_URL,
        "autostart": True,
        "stream_base_url": cli_app.DEFAULT_BASE_URL,
        "run_id": "run-1",
        "debug": False,
    }


def test_run_module_removed() -> None:
    result = runner.invoke(cli_app.app, ["run", "prompt", "-m", "hello"])
    assert result.exit_code != 0
    assert "No such command 'run'" in result.output


def test_root_help_lists_env_module() -> None:
    result = runner.invoke(cli_app.app, ["--help"])
    assert result.exit_code == 0
    assert "env" in result.output
    assert "mcp" in result.output
    assert "skills" in result.output
    assert "triggers" in result.output
    assert "prompts" in result.output
