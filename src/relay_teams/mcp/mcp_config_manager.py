# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from json import dumps, loads
from pathlib import Path
from typing import cast

from relay_teams.builtin import ensure_app_config_bootstrap
from relay_teams.env import (
    apply_proxy_env_to_process_env,
    extract_proxy_env_vars,
    load_merged_env_vars,
    sync_app_env_to_process_env,
)
from relay_teams.logger import get_logger
from relay_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.paths import get_app_config_dir

from relay_teams.trace import trace_span

logger = get_logger(__name__)
_MCP_FILE_NAME = "mcp.json"
_ENV_FILE_NAME = ".env"


def get_user_mcp_file_path(user_home_dir: Path | None = None) -> Path:
    return get_app_config_dir(user_home_dir=user_home_dir) / _MCP_FILE_NAME


def get_project_mcp_file_path(project_root: Path | None = None) -> Path:
    _ = project_root
    return get_app_config_dir() / _MCP_FILE_NAME


class McpConfigManager:
    def __init__(
        self,
        *,
        app_config_dir: Path,
        user_home_dir: Path | None = None,
    ) -> None:
        self._app_config_dir: Path = app_config_dir.expanduser().resolve()
        self._user_home_dir: Path | None = user_home_dir

    def load_registry(self) -> McpRegistry:
        ensure_app_config_bootstrap(self._app_config_dir)
        sync_app_env_to_process_env(self._app_config_dir / _ENV_FILE_NAME)
        with trace_span(
            logger,
            component="mcp.config",
            operation="load_registry",
            attributes={"app_config_dir": str(self._app_config_dir)},
        ):
            merged_specs: dict[str, McpServerSpec] = {}
            merged_env = load_merged_env_vars(
                extra_env_files=(self._app_config_dir / _ENV_FILE_NAME,),
            )
            proxy_env = apply_proxy_env_to_process_env(merged_env)
            for spec in _load_specs_from_file(
                file_path=self._app_config_dir / _MCP_FILE_NAME,
                source=McpConfigScope.APP,
                proxy_env=proxy_env,
            ):
                merged_specs[spec.name] = spec
            return McpRegistry(tuple(merged_specs.values()))

    def add_server(
        self,
        *,
        name: str,
        server_config: dict[str, JsonValue],
        overwrite: bool = False,
    ) -> Path:
        ensure_app_config_bootstrap(self._app_config_dir)
        with trace_span(
            logger,
            component="mcp.config",
            operation="add_server",
            attributes={"app_config_dir": str(self._app_config_dir), "name": name},
        ):
            normalized_name = name.strip()
            if not normalized_name:
                raise ValueError("MCP server name must be a non-empty string")

            config_path = self._app_config_dir / _MCP_FILE_NAME
            payload = _load_json_object(config_path) if config_path.exists() else {}
            payload, servers = _writable_mcp_servers_payload(payload)

            if normalized_name in servers and not overwrite:
                raise ValueError(f"MCP server already exists: {normalized_name}")

            servers[normalized_name] = _normalize_mcp_server_config(
                normalized_name,
                server_config,
            )
            config_path.write_text(
                json_dumps(payload),
                encoding="utf-8",
            )
            return config_path

    def get_server_config(self, name: str) -> dict[str, JsonValue]:
        ensure_app_config_bootstrap(self._app_config_dir)
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("MCP server name must be a non-empty string")
        config_path = self._app_config_dir / _MCP_FILE_NAME
        payload = _load_json_object(config_path) if config_path.exists() else {}
        existing_servers = _extract_mcp_servers(payload)
        if not isinstance(existing_servers, dict):
            raise ValueError(f"Unknown MCP server: {normalized_name}")
        existing_config = existing_servers.get(normalized_name)
        if existing_config is None:
            raise ValueError(f"Unknown MCP server: {normalized_name}")
        return _normalize_mcp_server_config(
            normalized_name,
            _normalize_to_json_object(existing_config),
        )

    def update_server(
        self,
        *,
        name: str,
        server_config: dict[str, JsonValue],
    ) -> Path:
        ensure_app_config_bootstrap(self._app_config_dir)
        with trace_span(
            logger,
            component="mcp.config",
            operation="update_server",
            attributes={"app_config_dir": str(self._app_config_dir), "name": name},
        ):
            normalized_name = name.strip()
            if not normalized_name:
                raise ValueError("MCP server name must be a non-empty string")

            config_path = self._app_config_dir / _MCP_FILE_NAME
            raw_payload = _load_json_object(config_path) if config_path.exists() else {}
            payload, existing_servers = _writable_mcp_servers_payload(raw_payload)
            if not existing_servers:
                raise ValueError(f"Unknown MCP server: {normalized_name}")
            existing_config = existing_servers.get(normalized_name)
            if existing_config is None:
                raise ValueError(f"Unknown MCP server: {normalized_name}")

            existing_enabled = _is_mcp_server_enabled(
                _normalize_to_json_object(existing_config)
            )
            normalized_config = _normalize_mcp_server_config(
                normalized_name,
                server_config,
            )
            if (
                "enabled" not in normalized_config
                and "disabled" not in normalized_config
            ):
                normalized_config["enabled"] = existing_enabled
            else:
                normalized_config["enabled"] = _is_mcp_server_enabled(normalized_config)
                normalized_config.pop("disabled", None)
            existing_servers[normalized_name] = normalized_config
            config_path.write_text(json_dumps(payload), encoding="utf-8")
            return config_path

    def set_server_enabled(self, *, name: str, enabled: bool) -> Path:
        ensure_app_config_bootstrap(self._app_config_dir)
        with trace_span(
            logger,
            component="mcp.config",
            operation="set_server_enabled",
            attributes={
                "app_config_dir": str(self._app_config_dir),
                "name": name,
                "enabled": enabled,
            },
        ):
            normalized_name = name.strip()
            if not normalized_name:
                raise ValueError("MCP server name must be a non-empty string")

            config_path = self._app_config_dir / _MCP_FILE_NAME
            raw_payload = _load_json_object(config_path) if config_path.exists() else {}
            payload, existing_servers = _writable_mcp_servers_payload(raw_payload)
            if not existing_servers:
                raise ValueError(f"Unknown MCP server: {normalized_name}")

            existing_config = existing_servers.get(normalized_name)
            if existing_config is None:
                raise ValueError(f"Unknown MCP server: {normalized_name}")

            server_config = _normalize_to_json_object(existing_config)
            server_config["enabled"] = enabled
            server_config.pop("disabled", None)
            existing_servers[normalized_name] = server_config
            config_path.write_text(json_dumps(payload), encoding="utf-8")
            return config_path


def _load_specs_from_file(
    *, file_path: Path, source: McpConfigScope, proxy_env: dict[str, str]
) -> tuple[McpServerSpec, ...]:
    with trace_span(
        logger,
        component="mcp.config",
        operation="load_specs_from_file",
        attributes={"file_path": str(file_path), "source": source.value},
    ):
        if not file_path.exists():
            return ()

        try:
            payload = _load_json_object(file_path)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", file_path.name, exc)
            return ()

        maybe_servers = payload.get("mcpServers", payload)
        if not isinstance(maybe_servers, dict):
            return ()

        specs: list[McpServerSpec] = []
        for raw_name, raw_config in maybe_servers.items():
            name = str(raw_name)
            normalized_server_config = _normalize_mcp_server_config(
                name,
                _normalize_to_json_object(raw_config),
            )
            effective_server_config = _apply_proxy_env_to_mcp_server_config(
                normalized_server_config,
                proxy_env,
            )
            wrapped_config: dict[str, JsonValue] = {
                "mcpServers": {name: effective_server_config},
            }
            specs.append(
                McpServerSpec(
                    name=name,
                    config=wrapped_config,
                    server_config=effective_server_config,
                    source=source,
                    enabled=_is_mcp_server_enabled(normalized_server_config),
                )
            )
        return tuple(specs)


def _load_json_object(file_path: Path) -> dict[str, JsonValue]:
    raw = cast(object, loads(file_path.read_text(encoding="utf-8-sig")))
    if isinstance(raw, dict):
        return cast(dict[str, JsonValue], raw)
    return {}


def _extract_mcp_servers(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    maybe_servers = payload.get("mcpServers", payload)
    if isinstance(maybe_servers, dict):
        return maybe_servers
    return {}


def _writable_mcp_servers_payload(
    payload: dict[str, JsonValue],
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    existing_servers = payload.get("mcpServers")
    if isinstance(existing_servers, dict):
        return payload, existing_servers
    if "mcpServers" in payload:
        servers: dict[str, JsonValue] = {}
        empty_wrapped_payload: dict[str, JsonValue] = {"mcpServers": servers}
        return empty_wrapped_payload, servers
    servers = dict(payload)
    migrated_payload: dict[str, JsonValue] = {"mcpServers": servers}
    return migrated_payload, servers


def _normalize_to_json_object(value: object) -> dict[str, JsonValue]:
    normalized = _normalize_json_value(value)
    if isinstance(normalized, dict):
        return normalized
    return {}


def _normalize_mcp_server_config(
    name: str,
    raw_config: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    config = _extract_named_mcp_server_config(name, raw_config)
    normalized = _normalize_to_json_object(config)

    raw_type = normalized.get("type")
    if raw_type == "local":
        normalized["transport"] = "stdio"
    if raw_type == "remote":
        normalized["transport"] = _detect_url_transport(normalized.get("url"))

    raw_command = normalized.get("command")
    if isinstance(raw_command, list):
        command_parts = [str(item).strip() for item in raw_command if str(item).strip()]
        if command_parts:
            normalized["command"] = command_parts[0]
            existing_args = normalized.get("args")
            if not isinstance(existing_args, list):
                normalized["args"] = [item for item in command_parts[1:]]
    return normalized


def _extract_named_mcp_server_config(
    name: str,
    raw_config: dict[str, JsonValue],
) -> object:
    maybe_servers = raw_config.get("mcpServers")
    if isinstance(maybe_servers, dict):
        named_config = maybe_servers.get(name)
        if named_config is not None:
            return named_config
    return raw_config


def _detect_url_transport(value: object) -> str:
    if isinstance(value, str) and "/sse" in value:
        return "sse"
    return "http"


def _is_mcp_server_enabled(server_config: dict[str, JsonValue]) -> bool:
    raw_enabled = server_config.get("enabled")
    if isinstance(raw_enabled, bool):
        return raw_enabled
    raw_disabled = server_config.get("disabled")
    if isinstance(raw_disabled, bool):
        return not raw_disabled
    return True


def _normalize_json_value(value: object) -> JsonValue:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        items = cast(list[object], value)
        return [_normalize_json_value(item) for item in items]
    if isinstance(value, dict):
        entries = cast(dict[object, object], value)
        normalized: dict[str, JsonValue] = {}
        for key, item in entries.items():
            normalized[str(key)] = _normalize_json_value(item)
        return normalized
    return str(value)


def json_dumps(payload: dict[str, JsonValue]) -> str:
    return dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _apply_proxy_env_to_mcp_server_config(
    server_config: dict[str, JsonValue],
    proxy_env: dict[str, str],
) -> dict[str, JsonValue]:
    if not proxy_env:
        return server_config

    merged_config: dict[str, JsonValue] = dict(server_config)
    existing_env = server_config.get("env")
    normalized_env = dict(existing_env) if isinstance(existing_env, dict) else {}
    normalized_env_strings = {
        key: value for key, value in normalized_env.items() if isinstance(value, str)
    }
    explicit_proxy_env = extract_proxy_env_vars(normalized_env_strings)
    merged_env: dict[str, JsonValue] = {key: value for key, value in proxy_env.items()}
    for key, value in explicit_proxy_env.items():
        merged_env[key] = value
    for key, value in normalized_env.items():
        merged_env[key] = value
    merged_config["env"] = merged_env
    return merged_config
