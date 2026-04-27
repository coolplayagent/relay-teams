# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from typer.testing import CliRunner

from relay_teams.interfaces.cli import app as cli_app
from relay_teams.mcp.mcp_models import (
    McpConfigScope,
    McpServerAddResult,
    McpServerConnectionTestResult,
    McpServerEnabledUpdateRequest,
    McpServerSummary,
    McpServerToolsSummary,
    McpToolInfo,
)

runner = CliRunner()


class _FakeMcpService:
    def __init__(self) -> None:
        self.added_config: dict[str, object] | None = None
        self.enabled_update: dict[str, object] | None = None

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

    def add_server(
        self,
        *,
        name: str,
        server_config: dict[str, object],
        overwrite: bool = False,
    ) -> McpServerAddResult:
        self.added_config = {
            "name": name,
            "server_config": server_config,
            "overwrite": overwrite,
        }
        return McpServerAddResult(
            server=McpServerSummary(
                name=name,
                source=McpConfigScope.APP,
                transport=str(server_config.get("transport", "stdio")),
            ),
            config_path="C:/Users/test/.relay-teams/mcp.json",
        )

    def set_server_enabled(
        self,
        name: str,
        request: McpServerEnabledUpdateRequest,
    ) -> McpServerSummary:
        self.enabled_update = {"name": name, "enabled": request.enabled}
        return McpServerSummary(
            name=name,
            source=McpConfigScope.APP,
            transport="stdio",
            enabled=request.enabled,
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

    async def test_server_connection(self, name: str) -> McpServerConnectionTestResult:
        return McpServerConnectionTestResult(
            server=name,
            source=McpConfigScope.APP,
            transport="stdio",
            ok=True,
            tool_count=1,
            tools=(McpToolInfo(name=f"{name}_read_file", description="Read"),),
        )


class _FailingMcpService:
    def list_servers(self) -> tuple[McpServerSummary, ...]:
        return ()

    async def list_server_tools(self, name: str) -> McpServerToolsSummary:
        raise RuntimeError(f"connection failed for {name}")


def test_mcp_list_supports_json_output(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_cli.load_mcp_service",
        _FakeMcpService,
    )

    result = runner.invoke(cli_app.app, ["mcp", "list", "--format", "json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == [
        {"name": "filesystem", "source": "app", "transport": "stdio", "enabled": True},
        {"name": "browser", "source": "app", "transport": "http", "enabled": True},
    ]


def test_mcp_tools_renders_table_by_default(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_cli.load_mcp_service",
        _FakeMcpService,
    )

    result = runner.invoke(cli_app.app, ["mcp", "tools", "filesystem"])

    assert result.exit_code == 0
    assert result.output.startswith("MCP Tools for filesystem (2 total)")
    assert "filesystem_read_file" in result.output
    assert "filesystem_write_file" in result.output


def test_mcp_tools_surfaces_connection_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_cli.load_mcp_service",
        lambda: _FailingMcpService(),
    )

    result = runner.invoke(cli_app.app, ["mcp", "tools", "broken-server"])

    assert result.exit_code != 0
    assert "Failed to connect MCP server 'broken-server'" in result.output


def test_mcp_add_supports_stdio_config(monkeypatch) -> None:
    fake_service = _FakeMcpService()
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_cli.load_mcp_service",
        lambda: fake_service,
    )

    result = runner.invoke(
        cli_app.app,
        [
            "mcp",
            "add",
            "filesystem",
            "--command",
            "npx",
            "--arg",
            "-y",
            "--arg",
            "@modelcontextprotocol/server-filesystem",
            "--env",
            "TOKEN=secret",
        ],
    )

    assert result.exit_code == 0
    assert fake_service.added_config == {
        "name": "filesystem",
        "server_config": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
            "env": {"TOKEN": "secret"},
        },
        "overwrite": False,
    }


def test_mcp_add_supports_remote_config_json(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_cli.load_mcp_service",
        lambda: _FakeMcpService(),
    )

    result = runner.invoke(
        cli_app.app,
        [
            "mcp",
            "add",
            "docs",
            "--url",
            "https://example.com/mcp",
            "--header",
            "Authorization=Bearer token",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["server"] == {
        "name": "docs",
        "source": "app",
        "transport": "http",
        "enabled": True,
    }


def test_mcp_disable_updates_enabled_state(monkeypatch) -> None:
    fake_service = _FakeMcpService()
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_cli.load_mcp_service",
        lambda: fake_service,
    )

    result = runner.invoke(cli_app.app, ["mcp", "disable", "filesystem"])

    assert result.exit_code == 0
    assert fake_service.enabled_update == {"name": "filesystem", "enabled": False}
    assert "filesystem is now disabled" in result.output


def test_mcp_test_supports_json_output(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_cli.load_mcp_service",
        lambda: _FakeMcpService(),
    )

    result = runner.invoke(
        cli_app.app, ["mcp", "test", "filesystem", "--format", "json"]
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["ok"] is True


def test_mcp_help_uses_relay_teams_examples() -> None:
    result = runner.invoke(cli_app.app, ["mcp", "--help"])

    assert result.exit_code == 0
    assert "relay-teams mcp list" in result.output
    assert "relay-teams mcp add filesystem" in result.output
    assert "relay-teams mcp tools filesystem" in result.output
