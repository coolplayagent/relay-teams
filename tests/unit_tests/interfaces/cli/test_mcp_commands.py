# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from typer.testing import CliRunner

from agent_teams.interfaces.cli import app as cli_app
from agent_teams.mcp.mcp_models import (
    McpConfigScope,
    McpServerSummary,
    McpServerToolsSummary,
    McpToolInfo,
)

runner = CliRunner()


class _FakeMcpService:
    def list_servers(self) -> tuple[McpServerSummary, ...]:
        return (
            McpServerSummary(
                name="filesystem",
                source=McpConfigScope.APP,
                transport="stdio",
            ),
            McpServerSummary(
                name="browser",
                source=McpConfigScope.APP,
                transport="http",
            ),
        )

    async def list_server_tools(self, name: str) -> McpServerToolsSummary:
        if name != "filesystem":
            raise ValueError(f"Unknown MCP server: {name}")
        return McpServerToolsSummary(
            server="filesystem",
            source=McpConfigScope.APP,
            transport="stdio",
            tools=(
                McpToolInfo(name="filesystem_read_file", description="Read a file"),
                McpToolInfo(name="filesystem_write_file", description="Write a file"),
            ),
        )


class _FailingMcpService:
    def list_servers(self) -> tuple[McpServerSummary, ...]:
        return ()

    async def list_server_tools(self, name: str) -> McpServerToolsSummary:
        raise RuntimeError(f"connection failed for {name}")


def test_mcp_list_supports_json_output(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_teams.mcp.mcp_cli.load_mcp_service",
        lambda: _FakeMcpService(),
    )

    result = runner.invoke(cli_app.app, ["mcp", "list", "--format", "json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == [
        {"name": "filesystem", "source": "app", "transport": "stdio"},
        {"name": "browser", "source": "app", "transport": "http"},
    ]


def test_mcp_tools_renders_table_by_default(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_teams.mcp.mcp_cli.load_mcp_service",
        lambda: _FakeMcpService(),
    )

    result = runner.invoke(cli_app.app, ["mcp", "tools", "filesystem"])

    assert result.exit_code == 0
    assert result.output.startswith("MCP Tools for filesystem (2 total)")
    assert "filesystem_read_file" in result.output
    assert "filesystem_write_file" in result.output


def test_mcp_tools_surfaces_connection_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_teams.mcp.mcp_cli.load_mcp_service",
        lambda: _FailingMcpService(),
    )

    result = runner.invoke(cli_app.app, ["mcp", "tools", "broken-server"])

    assert result.exit_code != 0
    assert "Failed to connect MCP server 'broken-server'" in result.output


def test_mcp_help_uses_relay_teams_examples() -> None:
    result = runner.invoke(cli_app.app, ["mcp", "--help"])

    assert result.exit_code == 0
    assert "relay-teams mcp list" in result.output
    assert "relay-teams mcp tools filesystem" in result.output
