# -*- coding: utf-8 -*-
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent_teams.interfaces.cli import app as cli_app
from agent_teams.env import env_cli

runner = CliRunner()


def test_env_list_masks_sensitive_values_by_default(
    monkeypatch, tmp_path: Path
) -> None:
    user_env_file = tmp_path / "user.env"
    project_env_file = tmp_path / "project.env"
    user_env_file.write_text(
        "OPENAI_API_KEY=user-secret\nUSER_NAME=alice\n", encoding="utf-8"
    )
    project_env_file.write_text("USER_NAME=project-alice\n", encoding="utf-8")

    monkeypatch.setattr(env_cli, "get_user_env_file_path", lambda: user_env_file)
    monkeypatch.setattr(env_cli, "get_project_env_file_path", lambda: project_env_file)
    monkeypatch.setenv("OPENAI_API_KEY", "process-secret")

    result = runner.invoke(
        cli_app.app,
        ["env", "list", "--format", "json", "--prefix", "OPENAI_API_KEY"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == [
        {
            "key": "OPENAI_API_KEY",
            "value": env_cli.MASKED_VALUE,
            "source": "process",
            "masked": True,
        }
    ]


def test_env_list_can_show_sensitive_values(monkeypatch, tmp_path: Path) -> None:
    user_env_file = tmp_path / "user.env"
    project_env_file = tmp_path / "project.env"
    user_env_file.write_text("", encoding="utf-8")
    project_env_file.write_text("", encoding="utf-8")

    monkeypatch.setattr(env_cli, "get_user_env_file_path", lambda: user_env_file)
    monkeypatch.setattr(env_cli, "get_project_env_file_path", lambda: project_env_file)
    monkeypatch.setenv("OPENAI_API_KEY", "process-secret")

    result = runner.invoke(
        cli_app.app,
        [
            "env",
            "list",
            "--format",
            "json",
            "--prefix",
            "OPENAI_API_KEY",
            "--show-secrets",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == [
        {
            "key": "OPENAI_API_KEY",
            "value": "process-secret",
            "source": "process",
            "masked": False,
        }
    ]


def test_env_list_table_output_is_pretty(monkeypatch, tmp_path: Path) -> None:
    user_env_file = tmp_path / "user.env"
    project_env_file = tmp_path / "project.env"
    user_env_file.write_text("", encoding="utf-8")
    project_env_file.write_text("", encoding="utf-8")

    monkeypatch.setattr(env_cli, "get_user_env_file_path", lambda: user_env_file)
    monkeypatch.setattr(env_cli, "get_project_env_file_path", lambda: project_env_file)
    monkeypatch.setenv("PUBLIC_SETTING", "enabled")

    result = runner.invoke(cli_app.app, ["env", "list", "--prefix", "PUBLIC_SETTING"])

    assert result.exit_code == 0
    assert "Environment Variables (" in result.output
    assert "+-" in result.output
    assert "| Key" in result.output
    assert "PUBLIC_SETTING" in result.output
    assert "enabled" in result.output


def test_env_list_defaults_to_table_format(monkeypatch, tmp_path: Path) -> None:
    user_env_file = tmp_path / "user.env"
    project_env_file = tmp_path / "project.env"
    user_env_file.write_text("", encoding="utf-8")
    project_env_file.write_text("", encoding="utf-8")

    monkeypatch.setattr(env_cli, "get_user_env_file_path", lambda: user_env_file)
    monkeypatch.setattr(env_cli, "get_project_env_file_path", lambda: project_env_file)
    monkeypatch.setenv("AT_UT_FORMAT_SWITCH", "enabled")

    result = runner.invoke(
        cli_app.app, ["env", "list", "--prefix", "AT_UT_FORMAT_SWITCH"]
    )

    assert result.exit_code == 0
    assert result.output.startswith("Environment Variables (")
    assert "| Key" in result.output
    assert "AT_UT_FORMAT_SWITCH" in result.output
    assert '"key":' not in result.output


def test_env_list_supports_json_format(monkeypatch, tmp_path: Path) -> None:
    user_env_file = tmp_path / "user.env"
    project_env_file = tmp_path / "project.env"
    user_env_file.write_text("", encoding="utf-8")
    project_env_file.write_text("", encoding="utf-8")

    monkeypatch.setattr(env_cli, "get_user_env_file_path", lambda: user_env_file)
    monkeypatch.setattr(env_cli, "get_project_env_file_path", lambda: project_env_file)
    monkeypatch.setenv("AT_UT_FORMAT_SWITCH", "enabled")

    result = runner.invoke(
        cli_app.app,
        [
            "env",
            "list",
            "--format",
            "json",
            "--prefix",
            "AT_UT_FORMAT_SWITCH",
            "--show-secrets",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == [
        {
            "key": "AT_UT_FORMAT_SWITCH",
            "value": "enabled",
            "source": "process",
            "masked": False,
        }
    ]
    assert "Environment Variables (" not in result.output


def test_env_proxy_reload_calls_server(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    monkeypatch.setattr(env_cli, "_auto_start_if_needed", lambda *_args: None)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object]:
        _ = (base_url, timeout_seconds)
        calls.append((method, path, payload))
        return {"status": "ok"}

    monkeypatch.setattr(env_cli, "_request_json", fake_request_json)

    result = runner.invoke(cli_app.app, ["env", "proxy-reload"])

    assert result.exit_code == 0
    assert calls == [("POST", "/api/system/configs/proxy:reload", None)]
    assert json.loads(result.output) == {"status": "ok"}


def test_env_probe_web_supports_json_output(monkeypatch) -> None:
    monkeypatch.setattr(env_cli, "_auto_start_if_needed", lambda *_args: None)
    monkeypatch.setattr(
        env_cli,
        "_request_json",
        lambda *args, **kwargs: {
            "ok": True,
            "url": "https://example.com",
            "final_url": "https://example.com",
            "status_code": 200,
            "latency_ms": 20,
            "used_method": "HEAD",
            "diagnostics": {
                "endpoint_reachable": True,
                "used_proxy": False,
                "redirected": False,
            },
            "retryable": False,
        },
    )

    result = runner.invoke(
        cli_app.app,
        ["env", "probe-web", "https://example.com", "--format", "json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["used_method"] == "HEAD"
