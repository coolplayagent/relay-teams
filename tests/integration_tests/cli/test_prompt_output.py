# -*- coding: utf-8 -*-
from __future__ import annotations

import httpx
from typer.testing import CliRunner

from agent_teams.interfaces.cli import app as cli_app
from integration_tests.support.environment import IntegrationEnvironment

runner = CliRunner()


def test_root_message_prints_fake_llm_output(
    integration_env: IntegrationEnvironment,
    monkeypatch,
) -> None:
    before_calls = _get_fake_llm_call_count(integration_env)
    monkeypatch.setattr(cli_app, "DEFAULT_BASE_URL", integration_env.api_base_url)

    result = runner.invoke(cli_app.app, ["-m", "hello integration prompt"])

    assert result.exit_code == 0
    assert "[fake-llm] hello integration prompt" in result.output

    after_calls = _get_fake_llm_call_count(integration_env)
    assert after_calls > before_calls


def _get_fake_llm_call_count(integration_env: IntegrationEnvironment) -> int:
    response = httpx.get(
        f"{integration_env.fake_llm_admin_url}/metrics",
        timeout=5.0,
    )
    response.raise_for_status()
    payload = response.json()
    return int(payload["chat_completions_calls"])
