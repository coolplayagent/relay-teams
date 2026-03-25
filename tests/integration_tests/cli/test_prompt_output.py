# -*- coding: utf-8 -*-
from __future__ import annotations

import httpx
from pathlib import Path
from typer.testing import CliRunner

from agent_teams.interfaces.cli import app as cli_app
from integration_tests.support.environment import IntegrationEnvironment

runner = CliRunner()


def test_root_message_prints_fake_llm_output(
    integration_env: IntegrationEnvironment,
    monkeypatch,
) -> None:
    before_calls = _get_fake_llm_call_count(integration_env)
    assert Path.home().resolve() == integration_env.config_dir.parent.resolve()
    monkeypatch.setattr(cli_app, "DEFAULT_BASE_URL", integration_env.api_base_url)

    result = runner.invoke(cli_app.app, ["-m", "hello integration prompt"])

    assert result.exit_code == 0
    assert "[fake-llm] hello integration prompt" in result.output

    after_calls = _get_fake_llm_call_count(integration_env)
    assert after_calls > before_calls


def test_root_message_uses_yolo_by_default(monkeypatch) -> None:
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
        if path == "/api/sessions":
            return {"session_id": "session-1"}
        if path == "/api/runs":
            return {"run_id": "run-1"}
        raise AssertionError(f"unexpected path: {path}")

    def fake_stream(base_url: str, run_id: str, debug: bool) -> None:
        _ = (base_url, run_id, debug)

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)
    monkeypatch.setattr(cli_app, "_stream_events", fake_stream)

    result = runner.invoke(cli_app.app, ["-m", "hello"])

    assert result.exit_code == 0
    assert calls[-1] == (
        "POST",
        "/api/runs",
        {
            "session_id": "session-1",
            "intent": "hello",
            "execution_mode": "ai",
            "yolo": True,
        },
    )


def _get_fake_llm_call_count(integration_env: IntegrationEnvironment) -> int:
    response = httpx.get(
        f"{integration_env.fake_llm_admin_url}/metrics",
        timeout=5.0,
        trust_env=False,
    )
    response.raise_for_status()
    payload = response.json()
    return int(payload["chat_completions_calls"])
