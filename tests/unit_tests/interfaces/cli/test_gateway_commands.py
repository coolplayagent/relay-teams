# -*- coding: utf-8 -*-
from __future__ import annotations

import re

from typer.testing import CliRunner

from agent_teams.interfaces.cli import app as cli_app

runner = CliRunner()
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _normalized_output(text: str) -> str:
    return " ".join(_ANSI_ESCAPE_RE.sub("", text).split())


def test_root_help_lists_gateway_module() -> None:
    result = runner.invoke(cli_app.app, ["--help"])
    normalized_output = _normalized_output(result.output)

    assert result.exit_code == 0
    assert "gateway" in normalized_output


def test_gateway_acp_help_lists_stdio_command() -> None:
    result = runner.invoke(cli_app.app, ["gateway", "acp", "stdio", "--help"])
    normalized_output = _normalized_output(result.output)

    assert result.exit_code == 0
    assert "--role" in normalized_output


def test_gateway_feishu_help_lists_management_commands() -> None:
    result = runner.invoke(cli_app.app, ["gateway", "feishu", "--help"])
    normalized_output = _normalized_output(result.output)

    assert result.exit_code == 0
    assert "create" in normalized_output
    assert "list" in normalized_output


def test_gateway_wechat_help_lists_management_commands() -> None:
    result = runner.invoke(cli_app.app, ["gateway", "wechat", "--help"])
    normalized_output = _normalized_output(result.output)

    assert result.exit_code == 0
    assert "connect" in normalized_output
    assert "list" in normalized_output
