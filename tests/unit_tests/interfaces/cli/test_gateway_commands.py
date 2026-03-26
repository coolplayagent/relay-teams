# -*- coding: utf-8 -*-
from __future__ import annotations

from typer.testing import CliRunner

from agent_teams.interfaces.cli import app as cli_app

runner = CliRunner()


def test_root_help_lists_gateway_module() -> None:
    result = runner.invoke(cli_app.app, ["--help"])

    assert result.exit_code == 0
    assert "gateway" in result.output


def test_gateway_acp_help_lists_stdio_command() -> None:
    result = runner.invoke(cli_app.app, ["gateway", "acp", "--help"])

    assert result.exit_code == 0
    assert "stdio" in result.output


def test_gateway_wechat_help_lists_management_commands() -> None:
    result = runner.invoke(cli_app.app, ["gateway", "wechat", "--help"])

    assert result.exit_code == 0
    assert "connect" in result.output
    assert "list" in result.output
