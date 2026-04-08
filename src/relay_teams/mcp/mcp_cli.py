# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from enum import Enum
import json
import logging

import typer

from relay_teams.mcp.mcp_config_manager import McpConfigManager
from relay_teams.mcp.mcp_models import McpServerSummary, McpServerToolsSummary
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


def build_mcp_app() -> typer.Typer:
    return mcp_app


def load_mcp_service() -> McpService:
    config_manager = McpConfigManager(app_config_dir=get_app_config_dir())
    return McpService(registry=config_manager.load_registry())


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
    border = f"+-{'-' * name_width}-+-{'-' * source_width}-+-{'-' * transport_width}-+"
    typer.echo(border)
    typer.echo(
        f"| {'Name'.ljust(name_width)} | "
        f"{'Source'.ljust(source_width)} | "
        f"{'Transport'.ljust(transport_width)} |"
    )
    typer.echo(border)
    for server in servers:
        typer.echo(
            f"| {server.name.ljust(name_width)} | "
            f"{server.source.value.ljust(source_width)} | "
            f"{server.transport.ljust(transport_width)} |"
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
