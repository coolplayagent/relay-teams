# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import logging
import subprocess

from pydantic import JsonValue

from relay_teams.logger import get_logger, log_event
from relay_teams.mcp.mcp_config_manager import McpConfigManager
from relay_teams.mcp.mcp_models import McpServerSpec
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.trace import trace_span

LOGGER = get_logger(__name__)


class McpConfigReloadService:
    def __init__(
        self,
        *,
        mcp_config_manager: McpConfigManager,
        role_registry: RoleRegistry,
        on_mcp_reloaded: Callable[[McpRegistry], None],
    ) -> None:
        self._mcp_config_manager: McpConfigManager = mcp_config_manager
        self._role_registry: RoleRegistry = role_registry
        self._on_mcp_reloaded: Callable[[McpRegistry], None] = on_mcp_reloaded

    def reload_mcp_config(self) -> None:
        with trace_span(
            LOGGER,
            component="mcp.config",
            operation="reload",
        ):
            mcp_registry = self._mcp_config_manager.load_registry()
            self._clean_uv_tool_cache(mcp_registry.list_specs())
            for role in self._role_registry.list_roles():
                mcp_registry.resolve_server_names(
                    role.mcp_servers,
                    strict=False,
                    consumer=f"mcp.config_reload.role:{role.role_id}",
                )
            self._on_mcp_reloaded(mcp_registry)

    def _clean_uv_tool_cache(self, specs: tuple[McpServerSpec, ...]) -> None:
        grouped_commands: dict[tuple[str, ...], list[str]] = {}
        for spec in specs:
            argv = _resolve_uv_cache_clean_argv(spec)
            if argv is None:
                continue
            grouped_commands.setdefault(argv, []).append(spec.name)
        for argv, server_names in grouped_commands.items():
            _run_uv_cache_clean_command(argv=argv, server_names=tuple(server_names))


def _resolve_uv_cache_clean_argv(spec: McpServerSpec) -> tuple[str, ...] | None:
    command_name = _normalize_command_name(
        _required_server_config_string(spec.server_config, "command")
    )
    args = _server_config_args(spec.server_config)
    if command_name == "uvx":
        return _build_uv_cache_clean_argv(_resolve_uv_tool_package(args))
    if command_name != "uv":
        return None
    if len(args) >= 2 and args[:2] == ("tool", "run"):
        return _build_uv_cache_clean_argv(_resolve_uv_tool_package(args[2:]))
    if args and args[0] == "x":
        return _build_uv_cache_clean_argv(_resolve_uv_tool_package(args[1:]))
    return None


def _build_uv_cache_clean_argv(package_name: str | None) -> tuple[str, ...]:
    if package_name is None:
        return ("uv", "cache", "clean", "--force")
    return ("uv", "cache", "clean", "--force", package_name)


def _resolve_uv_tool_package(args: tuple[str, ...]) -> str | None:
    for arg in args:
        if arg.startswith("--from="):
            resolved = arg.partition("=")[2].strip()
            return resolved or None
    for index, arg in enumerate(args):
        if arg != "--from":
            continue
        next_index = index + 1
        if next_index >= len(args):
            return None
        resolved = args[next_index].strip()
        return resolved or None
    if not args:
        return None
    first_arg = args[0].strip()
    if not first_arg or first_arg.startswith("-"):
        return None
    return first_arg


def _required_server_config_string(
    server_config: dict[str, JsonValue],
    key: str,
) -> str:
    value = server_config.get(key)
    if isinstance(value, str):
        return value
    return ""


def _server_config_args(server_config: dict[str, JsonValue]) -> tuple[str, ...]:
    raw_args = server_config.get("args")
    if not isinstance(raw_args, list):
        return ()
    return tuple(str(item).strip() for item in raw_args if str(item).strip())


def _normalize_command_name(command: str) -> str:
    resolved_name = Path(command.strip()).name.lower()
    return resolved_name.removesuffix(".exe")


def _run_uv_cache_clean_command(
    *,
    argv: tuple[str, ...],
    server_names: tuple[str, ...],
) -> None:
    cache_target = argv[4] if len(argv) > 4 else "all"
    payload: dict[str, JsonValue] = {
        "server_names": list(server_names),
        "command": list(argv),
        "cache_target": cache_target,
    }
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            check=False,
            text=True,
            timeout=60,
        )
    except FileNotFoundError as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="mcp.config.uv_cache_clean_missing",
            message="Skipping uv cache clean because uv is unavailable",
            payload=payload,
            exc_info=exc,
        )
        return
    except subprocess.TimeoutExpired as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="mcp.config.uv_cache_clean_timeout",
            message="Timed out while clearing uv cache for MCP reload",
            payload=payload,
            exc_info=exc,
        )
        return

    payload["returncode"] = result.returncode
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stdout:
        payload["stdout"] = stdout
    if stderr:
        payload["stderr"] = stderr
    if result.returncode != 0:
        log_event(
            LOGGER,
            logging.WARNING,
            event="mcp.config.uv_cache_clean_failed",
            message="uv cache clean failed during MCP reload",
            payload=payload,
        )
        return
    log_event(
        LOGGER,
        logging.INFO,
        event="mcp.config.uv_cache_cleaned",
        message="Cleared uv cache before MCP reload",
        payload=payload,
    )
