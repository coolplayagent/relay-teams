# -*- coding: utf-8 -*-
from __future__ import annotations

from json import loads
from pathlib import Path

from pydantic import JsonValue

from relay_teams.logger import get_logger
from relay_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
from relay_teams.plugins.path_resolution import namespace_plugin_ref
from relay_teams.plugins.plugin_models import PluginComponentSource
from relay_teams.plugins.substitution import substitute_plugin_vars

LOGGER = get_logger(__name__)


def load_plugin_mcp_specs(
    sources: tuple[PluginComponentSource, ...],
) -> tuple[McpServerSpec, ...]:
    specs: list[McpServerSpec] = []
    for source in sources:
        specs.extend(_load_source(source))
    return tuple(specs)


def _load_source(source: PluginComponentSource) -> tuple[McpServerSpec, ...]:
    try:
        payload = (
            source.inline_config
            if source.inline_config is not None
            else _load_json_object(source.path)
        )
    except Exception as exc:
        LOGGER.warning(
            "Skipping invalid plugin MCP config",
            extra={
                "plugin_name": source.plugin_name,
                "path": str(source.path),
                "error": str(exc),
            },
        )
        return ()
    raw_servers = payload.get("mcpServers", payload)
    if not isinstance(raw_servers, dict):
        return ()
    specs: list[McpServerSpec] = []
    for raw_name, raw_config in raw_servers.items():
        local_name = str(raw_name).strip()
        if not local_name:
            continue
        server_name = namespace_plugin_ref(
            plugin_name=source.plugin_name,
            local_name=local_name,
        )
        normalized_config = _json_object(raw_config)
        substituted_config = substitute_plugin_vars(
            value=normalized_config,
            plugin_root=source.root_dir,
            plugin_data=source.data_dir,
            user_config=source.user_config,
            allow_env=True,
        )
        server_config = (
            substituted_config if isinstance(substituted_config, dict) else {}
        )
        specs.append(
            McpServerSpec(
                name=server_name,
                config={"mcpServers": {server_name: server_config}},
                server_config=server_config,
                source=McpConfigScope.PLUGIN,
                enabled=_is_enabled(server_config),
            )
        )
    return tuple(specs)


def _load_json_object(path: Path) -> dict[str, JsonValue]:
    raw = loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        return {}
    return {str(key): _json_value(value) for key, value in raw.items()}


def _json_object(value: object) -> dict[str, JsonValue]:
    normalized = _json_value(value)
    if isinstance(normalized, dict):
        return normalized
    return {}


def _json_value(value: object) -> JsonValue:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


def _is_enabled(server_config: dict[str, JsonValue]) -> bool:
    raw_enabled = server_config.get("enabled")
    if isinstance(raw_enabled, bool):
        return raw_enabled
    raw_disabled = server_config.get("disabled")
    if isinstance(raw_disabled, bool):
        return not raw_disabled
    return True
