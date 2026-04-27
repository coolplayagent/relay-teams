# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from typer.testing import CliRunner

from relay_teams.interfaces.cli import app as cli_app
from relay_teams.mcp.mcp_cli import _build_server_config
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


class _UnhealthyMcpService(_FakeMcpService):
    async def test_server_connection(self, name: str) -> McpServerConnectionTestResult:
        return McpServerConnectionTestResult(
            server=name,
            source=McpConfigScope.APP,
            transport="http",
            ok=False,
            error="connection failed",
        )


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


def test_mcp_list_renders_table_by_default(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_cli.load_mcp_service",
        _FakeMcpService,
    )

    result = runner.invoke(cli_app.app, ["mcp", "list"])

    assert result.exit_code == 0
    assert result.output.startswith("MCP Servers (2 total)")
    assert "filesystem" in result.output
    assert "enabled" in result.output


def test_mcp_list_reports_empty_table(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_cli.load_mcp_service",
        _FailingMcpService,
    )

    result = runner.invoke(cli_app.app, ["mcp", "list"])

    assert result.exit_code == 0
    assert result.output.strip() == "No MCP servers discovered."


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
        _FakeMcpService,
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


def test_mcp_add_rejects_missing_transport_source(monkeypatch) -> None:
    fake_service = _FakeMcpService()
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_cli.load_mcp_service",
        lambda: fake_service,
    )

    result = runner.invoke(cli_app.app, ["mcp", "add", "filesystem"])

    assert result.exit_code != 0
    assert fake_service.added_config is None


def test_mcp_add_rejects_duplicate_transport_sources(monkeypatch) -> None:
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
            "--url",
            "https://example.com/mcp",
        ],
    )

    assert result.exit_code != 0
    assert fake_service.added_config is None


def test_mcp_add_surfaces_duplicate_server(monkeypatch) -> None:
    class _DuplicateMcpService(_FakeMcpService):
        def add_server(
            self,
            *,
            name: str,
            server_config: dict[str, object],
            overwrite: bool = False,
        ) -> McpServerAddResult:
            _ = name, server_config, overwrite
            raise ValueError("MCP server already exists: filesystem")

    monkeypatch.setattr(
        "relay_teams.mcp.mcp_cli.load_mcp_service",
        _DuplicateMcpService,
    )

    result = runner.invoke(
        cli_app.app,
        ["mcp", "add", "filesystem", "--command", "npx"],
    )

    assert result.exit_code != 0
    assert "MCP server already exists: filesystem" in result.output


def test_mcp_add_rejects_http_transport_for_stdio_command(monkeypatch) -> None:
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
            "--transport",
            "http",
        ],
    )

    assert result.exit_code != 0
    assert fake_service.added_config is None


def test_mcp_add_rejects_stdio_transport_for_remote_url(monkeypatch) -> None:
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
            "docs",
            "--url",
            "https://example.com/mcp",
            "--transport",
            "stdio",
        ],
    )

    assert result.exit_code != 0
    assert fake_service.added_config is None


def test_mcp_enable_supports_json_output(monkeypatch) -> None:
    fake_service = _FakeMcpService()
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_cli.load_mcp_service",
        lambda: fake_service,
    )

    result = runner.invoke(
        cli_app.app,
        ["mcp", "enable", "filesystem", "--format", "json"],
    )

    assert result.exit_code == 0
    assert fake_service.enabled_update == {"name": "filesystem", "enabled": True}
    assert json.loads(result.output)["enabled"] is True


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
        _FakeMcpService,
    )

    result = runner.invoke(
        cli_app.app, ["mcp", "test", "filesystem", "--format", "json"]
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["ok"] is True


def test_mcp_test_renders_success_by_default(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_cli.load_mcp_service",
        _FakeMcpService,
    )

    result = runner.invoke(cli_app.app, ["mcp", "test", "filesystem"])

    assert result.exit_code == 0
    assert result.output.strip() == "MCP server filesystem connected (stdio, 1 tools)"


def test_mcp_test_renders_connection_failure_by_default(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_cli.load_mcp_service",
        _UnhealthyMcpService,
    )

    result = runner.invoke(cli_app.app, ["mcp", "test", "docs"])

    assert result.exit_code == 0
    assert "MCP server docs failed (http)" in result.output
    assert "connection failed" in result.output


def test_mcp_test_surfaces_unknown_server(monkeypatch) -> None:
    class _UnknownMcpService(_FakeMcpService):
        async def test_server_connection(
            self,
            name: str,
        ) -> McpServerConnectionTestResult:
            raise ValueError(f"Unknown MCP server: {name}")

    monkeypatch.setattr(
        "relay_teams.mcp.mcp_cli.load_mcp_service",
        _UnknownMcpService,
    )

    result = runner.invoke(cli_app.app, ["mcp", "test", "missing"])

    assert result.exit_code != 0
    assert "Unknown MCP server: missing" in result.output


def test_build_server_config_rejects_empty_command() -> None:
    try:
        _build_server_config(
            command="",
            server_args=(),
            url=None,
            transport=None,
            env=(),
            header=(),
        )
    except Exception as exc:
        assert "--command must be non-empty" in str(exc)
    else:
        raise AssertionError("Expected empty command to be rejected")


def test_build_server_config_rejects_empty_url() -> None:
    try:
        _build_server_config(
            command=None,
            server_args=(),
            url=" ",
            transport=None,
            env=(),
            header=(),
        )
    except Exception as exc:
        assert "--url must be non-empty" in str(exc)
    else:
        raise AssertionError("Expected empty URL to be rejected")


def test_build_server_config_rejects_invalid_key_value_options() -> None:
    try:
        _build_server_config(
            command="npx",
            server_args=(),
            url=None,
            transport=None,
            env=("TOKEN",),
            header=(),
        )
    except Exception as exc:
        assert "--env values must use KEY=VALUE" in str(exc)
    else:
        raise AssertionError("Expected invalid env option to be rejected")


def test_mcp_help_uses_relay_teams_examples() -> None:
    result = runner.invoke(cli_app.app, ["mcp", "--help"])

    assert result.exit_code == 0
    assert "relay-teams mcp list" in result.output
    assert "relay-teams mcp add filesystem" in result.output
    assert "relay-teams mcp tools filesystem" in result.output
