# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from enum import Enum
import json
import logging
import shlex

from pydantic import JsonValue
import typer

from relay_teams.mcp.mcp_config_manager import McpConfigManager
from relay_teams.mcp.mcp_models import (
    McpServerConnectionTestResult,
    McpServerEnabledUpdateRequest,
    McpServerSummary,
    McpServerToolsSummary,
)
from relay_teams.mcp.mcp_service import McpService
from relay_teams.paths import get_app_config_dir

mcp_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help=(
        "Inspect MCP servers from the app config directory.\n\n"
        "Config file:\n"
        "1. ~/.relay-teams/mcp.json (app scope)\n\n"
        "Common usage:\n"
        "- relay-teams mcp list\n"
        "- relay-teams mcp list --format json\n"
        "- relay-teams mcp add filesystem --command npx --arg -y --arg @modelcontextprotocol/server-filesystem\n"
        "- relay-teams mcp add context7 --url https://example.com/mcp\n"
        "- relay-teams mcp test filesystem\n"
        "- relay-teams mcp disable filesystem\n"
        "- relay-teams mcp enable filesystem\n"
        "- relay-teams mcp tools filesystem"
    ),
)

_JOB_OBJECT_WARNING = "Failed to create Job Object for process tree management"
_STDIO_PARSE_WARNING = "Failed to parse JSONRPC message from server"


class McpOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


class _SuppressKnownMcpNoise(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if message.startswith(_JOB_OBJECT_WARNING):
            return False
        if message.startswith(_STDIO_PARSE_WARNING):
            return False
        return True


@mcp_app.command(
    "list",
    help=("List effective MCP servers from app scope."),
)
def mcp_list(
    output_format: McpOutputFormat = typer.Option(
        McpOutputFormat.TABLE,
        "--format",
        help="Render as an ASCII table or JSON.",
        case_sensitive=False,
    ),
) -> None:
    service = load_mcp_service()
    servers = service.list_servers()
    if output_format == McpOutputFormat.JSON:
        typer.echo(
            json.dumps(
                [server.model_dump(mode="json") for server in servers],
                ensure_ascii=False,
            )
        )
        return
    render_server_table(servers)


@mcp_app.command(
    "tools",
    help=(
        "Connect to one MCP server and list the tools it exposes.\n\n"
        "This command validates the effective MCP server configuration by establishing a real connection.\n\n"
        "Example:\n"
        "- relay-teams mcp tools filesystem --format json"
    ),
)
def mcp_tools(
    server_name: str = typer.Argument(..., help="MCP server name."),
    output_format: McpOutputFormat = typer.Option(
        McpOutputFormat.TABLE,
        "--format",
        help="Render as an ASCII table or JSON.",
        case_sensitive=False,
    ),
) -> None:
    service = load_mcp_service()
    try:
        with suppress_known_mcp_noise():
            summary = asyncio.run(service.list_server_tools(server_name))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except Exception as exc:
        raise typer.BadParameter(
            f"Failed to connect MCP server '{server_name}': {exc}"
        ) from exc

    if output_format == McpOutputFormat.JSON:
        typer.echo(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False))
        return
    render_tool_table(summary)


@mcp_app.command(
    "add",
    help=(
        "Add or update an app-scoped MCP server.\n\n"
        "Use --command for stdio servers or --url for remote HTTP/SSE servers."
    ),
)
def mcp_add(
    server_name: str = typer.Argument(..., help="MCP server name."),
    command: str | None = typer.Option(
        None,
        "--command",
        help="Stdio command. If it contains spaces, it is split shell-style.",
    ),
    server_args: list[str] = typer.Option(
        [],
        "--arg",
        help="Argument for the stdio command. Repeat for multiple args.",
    ),
    url: str | None = typer.Option(
        None,
        "--url",
        help="Remote MCP URL.",
    ),
    transport: str | None = typer.Option(
        None,
        "--transport",
        help="Transport override: stdio, http, sse, or streamable-http.",
    ),
    env: list[str] = typer.Option(
        [],
        "--env",
        help="Environment variable for stdio servers as KEY=VALUE. Repeatable.",
    ),
    header: list[str] = typer.Option(
        [],
        "--header",
        help="HTTP header for remote servers as KEY=VALUE. Repeatable.",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Replace an existing server with the same name.",
    ),
    output_format: McpOutputFormat = typer.Option(
        McpOutputFormat.TABLE,
        "--format",
        help="Render as text or JSON.",
        case_sensitive=False,
    ),
) -> None:
    if bool(command) == bool(url):
        raise typer.BadParameter("Specify exactly one of --command or --url")

    service = load_mcp_service()
    server_config = _build_server_config(
        command=command,
        server_args=tuple(server_args),
        url=url,
        transport=transport,
        env=tuple(env),
        header=tuple(header),
    )
    try:
        result = service.add_server(
            name=server_name,
            server_config=server_config,
            overwrite=overwrite,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if output_format == McpOutputFormat.JSON:
        typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
        return
    typer.echo(
        f"Added MCP server {result.server.name} "
        f"({result.server.transport}) to {result.config_path}"
    )


@mcp_app.command(
    "test",
    help="Connect to one MCP server and report whether the connection succeeds.",
)
def mcp_test(
    server_name: str = typer.Argument(..., help="MCP server name."),
    output_format: McpOutputFormat = typer.Option(
        McpOutputFormat.TABLE,
        "--format",
        help="Render as text or JSON.",
        case_sensitive=False,
    ),
) -> None:
    service = load_mcp_service()
    try:
        with suppress_known_mcp_noise():
            result = asyncio.run(service.test_server_connection(server_name))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if output_format == McpOutputFormat.JSON:
        typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
        return
    render_connection_test(result)


@mcp_app.command(
    "enable",
    help="Enable an app-scoped MCP server in mcp.json.",
)
def mcp_enable(
    server_name: str = typer.Argument(..., help="MCP server name."),
    output_format: McpOutputFormat = typer.Option(
        McpOutputFormat.TABLE,
        "--format",
        help="Render as text or JSON.",
        case_sensitive=False,
    ),
) -> None:
    _set_mcp_server_enabled(
        server_name=server_name,
        enabled=True,
        output_format=output_format,
    )


@mcp_app.command(
    "disable",
    help="Disable an app-scoped MCP server in mcp.json without deleting it.",
)
def mcp_disable(
    server_name: str = typer.Argument(..., help="MCP server name."),
    output_format: McpOutputFormat = typer.Option(
        McpOutputFormat.TABLE,
        "--format",
        help="Render as text or JSON.",
        case_sensitive=False,
    ),
) -> None:
    _set_mcp_server_enabled(
        server_name=server_name,
        enabled=False,
        output_format=output_format,
    )


def build_mcp_app() -> typer.Typer:
    return mcp_app


def load_mcp_service() -> McpService:
    config_manager = McpConfigManager(app_config_dir=get_app_config_dir())
    return McpService(
        registry=config_manager.load_registry(),
        config_manager=config_manager,
    )


@contextmanager
def suppress_known_mcp_noise():
    filter_instance = _SuppressKnownMcpNoise()
    logger_names = ("client.stdio.win32", "mcp.client.stdio")
    loggers = [logging.getLogger(name) for name in logger_names]
    for logger in loggers:
        logger.addFilter(filter_instance)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(filter_instance)


def render_server_table(servers: tuple[McpServerSummary, ...]) -> None:
    if not servers:
        typer.echo("No MCP servers discovered.")
        return

    typer.echo(f"MCP Servers ({len(servers)} total)")
    name_width = max(len("Name"), *(len(server.name) for server in servers))
    source_width = max(len("Source"), *(len(server.source.value) for server in servers))
    transport_width = max(
        len("Transport"),
        *(len(server.transport) for server in servers),
    )
    status_width = max(
        len("Status"),
        *(len(_format_enabled_status(server.enabled)) for server in servers),
    )
    border = (
        f"+-{'-' * name_width}-+-{'-' * source_width}-+"
        f"-{'-' * transport_width}-+-{'-' * status_width}-+"
    )
    typer.echo(border)
    typer.echo(
        f"| {'Name'.ljust(name_width)} | "
        f"{'Source'.ljust(source_width)} | "
        f"{'Transport'.ljust(transport_width)} | "
        f"{'Status'.ljust(status_width)} |"
    )
    typer.echo(border)
    for server in servers:
        typer.echo(
            f"| {server.name.ljust(name_width)} | "
            f"{server.source.value.ljust(source_width)} | "
            f"{server.transport.ljust(transport_width)} | "
            f"{_format_enabled_status(server.enabled).ljust(status_width)} |"
        )
    typer.echo(border)


def render_tool_table(summary: McpServerToolsSummary) -> None:
    typer.echo(f"MCP Tools for {summary.server} ({len(summary.tools)} total)")
    if not summary.tools:
        typer.echo("No tools exposed by this MCP server.")
        return

    name_width = max(len("Name"), *(len(tool.name) for tool in summary.tools))
    description_width = max(
        len("Description"),
        *(len(tool.description) for tool in summary.tools),
    )
    border = f"+-{'-' * name_width}-+-{'-' * description_width}-+"
    typer.echo(border)
    typer.echo(
        f"| {'Name'.ljust(name_width)} | {'Description'.ljust(description_width)} |"
    )
    typer.echo(border)
    for tool in summary.tools:
        typer.echo(
            f"| {tool.name.ljust(name_width)} | "
            f"{tool.description.ljust(description_width)} |"
        )
    typer.echo(border)


def render_connection_test(result: McpServerConnectionTestResult) -> None:
    if result.ok:
        typer.echo(
            f"MCP server {result.server} connected "
            f"({result.transport}, {result.tool_count} tools)"
        )
        return
    typer.echo(f"MCP server {result.server} failed ({result.transport})")
    if result.error:
        typer.echo(result.error)


def _set_mcp_server_enabled(
    *,
    server_name: str,
    enabled: bool,
    output_format: McpOutputFormat,
) -> None:
    service = load_mcp_service()
    try:
        summary = service.set_server_enabled(
            server_name,
            McpServerEnabledUpdateRequest(enabled=enabled),
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if output_format == McpOutputFormat.JSON:
        typer.echo(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False))
        return
    typer.echo(
        f"MCP server {summary.name} is now {_format_enabled_status(summary.enabled)}"
    )


def _format_enabled_status(enabled: bool) -> str:
    return "enabled" if enabled else "disabled"


def _build_server_config(
    *,
    command: str | None,
    server_args: tuple[str, ...],
    url: str | None,
    transport: str | None,
    env: tuple[str, ...],
    header: tuple[str, ...],
) -> dict[str, JsonValue]:
    if command is not None:
        command_parts = shlex.split(command, posix=False)
        if not command_parts:
            raise typer.BadParameter("--command must be non-empty")
        config: dict[str, JsonValue] = {
            "transport": transport or "stdio",
            "command": command_parts[0],
            "args": [*command_parts[1:], *server_args],
        }
        parsed_env = _parse_key_value_options(env, "--env")
        if parsed_env:
            config["env"] = parsed_env
        return config

    if url is None or not url.strip():
        raise typer.BadParameter("--url must be non-empty")
    config = {
        "transport": transport or ("sse" if "/sse" in url else "http"),
        "url": url.strip(),
    }
    parsed_headers = _parse_key_value_options(header, "--header")
    if parsed_headers:
        config["headers"] = parsed_headers
    return config


def _parse_key_value_options(
    values: tuple[str, ...],
    option_name: str,
) -> dict[str, JsonValue]:
    parsed: dict[str, JsonValue] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key.strip():
            raise typer.BadParameter(f"{option_name} values must use KEY=VALUE")
        parsed[key.strip()] = item
    return parsed
