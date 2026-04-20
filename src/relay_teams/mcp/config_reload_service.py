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
    configured_command = _required_server_config_string(spec.server_config, "command")
    command_name = _normalize_command_name(configured_command)
    uv_cache_clean_command = _resolve_uv_cache_clean_command(
        configured_command=configured_command,
        command_name=command_name,
    )
    if uv_cache_clean_command is None:
        return None
    args = _server_config_args(spec.server_config)
    if command_name == "uvx":
        return _build_uv_cache_clean_argv(
            command=uv_cache_clean_command,
            package_name=_resolve_uv_tool_package(args),
        )
    subcommand_args = _strip_uv_global_args(args)
    if len(subcommand_args) >= 2 and subcommand_args[:2] == ("tool", "run"):
        return _build_uv_cache_clean_argv(
            command=uv_cache_clean_command,
            package_name=_resolve_uv_tool_package(subcommand_args[2:]),
        )
    if subcommand_args and subcommand_args[0] == "x":
        return _build_uv_cache_clean_argv(
            command=uv_cache_clean_command,
            package_name=_resolve_uv_tool_package(subcommand_args[1:]),
        )
    return None


def _resolve_uv_cache_clean_command(
    *,
    configured_command: str,
    command_name: str,
) -> str | None:
    stripped_command = configured_command.strip()
    if command_name == "uv":
        return stripped_command or "uv"
    if command_name != "uvx":
        return None
    return _replace_command_basename(
        command=stripped_command or "uvx",
        replacement_name="uv",
    )


def _build_uv_cache_clean_argv(
    *,
    command: str,
    package_name: str | None,
) -> tuple[str, ...]:
    if package_name is None:
        return (command, "cache", "clean", "--force")
    return (command, "cache", "clean", "--force", package_name)


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


def _strip_uv_global_args(args: tuple[str, ...]) -> tuple[str, ...]:
    index = 0
    while index < len(args):
        arg = args[index]
        if not arg.startswith("-"):
            break
        if arg == "--":
            index += 1
            break
        if _uv_option_requires_value(arg):
            next_index = index + 1
            if next_index >= len(args):
                return ()
            index += 2
            continue
        index += 1
    return args[index:]


def _uv_option_requires_value(arg: str) -> bool:
    if "=" in arg:
        return False
    return arg in _UV_OPTIONS_WITH_VALUE


_UV_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "--allow-insecure-host",
        "--color",
        "--config-file",
        "--directory",
        "--project",
    }
)


def _normalize_command_name(command: str) -> str:
    resolved_name = Path(command.strip()).name.lower()
    return resolved_name.removesuffix(".exe")


def _replace_command_basename(command: str, replacement_name: str) -> str:
    stripped_command = command.strip()
    if not stripped_command:
        return replacement_name
    separator_index = max(stripped_command.rfind("/"), stripped_command.rfind("\\"))
    if separator_index < 0:
        suffix = ".exe" if stripped_command.lower().endswith(".exe") else ""
        return replacement_name + suffix
    prefix = stripped_command[: separator_index + 1]
    suffix = ".exe" if stripped_command.lower().endswith(".exe") else ""
    return prefix + replacement_name + suffix


def _run_uv_cache_clean_command(
    *,
    argv: tuple[str, ...],
    server_names: tuple[str, ...],
) -> None:
    payload_base: dict[str, JsonValue] = {
        "server_names": list(server_names),
        "cache_target": _uv_cache_target(argv),
    }
    result = _run_uv_cache_clean_subprocess(
        argv=argv,
        payload={**payload_base, "command": list(argv)},
    )
    if result is None:
        return

    if result.returncode != 0 and _is_unsupported_uv_force_error(result.stderr):
        legacy_argv = _build_legacy_uv_cache_clean_argv(argv)
        if legacy_argv is not None:
            legacy_payload: dict[str, JsonValue] = {
                **payload_base,
                "command": list(legacy_argv),
                "fallback_from_command": list(argv),
            }
            initial_stderr = result.stderr.strip()
            if initial_stderr:
                legacy_payload["initial_stderr"] = initial_stderr
            legacy_result = _run_uv_cache_clean_subprocess(
                argv=legacy_argv,
                payload=legacy_payload,
            )
            if legacy_result is None:
                return
            _log_uv_cache_clean_result(result=legacy_result, payload=legacy_payload)
            return

    _log_uv_cache_clean_result(
        result=result,
        payload={**payload_base, "command": list(argv)},
    )


def _uv_cache_target(argv: tuple[str, ...]) -> str:
    trailing_args = argv[3:]
    if trailing_args and trailing_args[0] == "--force":
        trailing_args = trailing_args[1:]
    if not trailing_args:
        return "all"
    return trailing_args[0]


def _build_legacy_uv_cache_clean_argv(argv: tuple[str, ...]) -> tuple[str, ...] | None:
    if len(argv) < 4 or argv[3] != "--force":
        return None
    return argv[:3] + argv[4:]


def _is_unsupported_uv_force_error(stderr: str) -> bool:
    normalized = stderr.casefold()
    if "--force" not in normalized:
        return False
    return "unexpected argument" in normalized or "unexpected option" in normalized


def _run_uv_cache_clean_subprocess(
    *,
    argv: tuple[str, ...],
    payload: dict[str, JsonValue],
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
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
        return None
    except subprocess.TimeoutExpired as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="mcp.config.uv_cache_clean_timeout",
            message="Timed out while clearing uv cache for MCP reload",
            payload=payload,
            exc_info=exc,
        )
        return None


def _log_uv_cache_clean_result(
    *,
    result: subprocess.CompletedProcess[str],
    payload: dict[str, JsonValue],
) -> None:
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
