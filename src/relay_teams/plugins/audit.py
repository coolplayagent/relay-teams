# -*- coding: utf-8 -*-
from __future__ import annotations

from json import loads
from json import JSONDecodeError
from pathlib import Path

from pydantic import JsonValue
from pydantic import ValidationError

from relay_teams.hooks.hook_normalization import normalize_hooks_payload
from relay_teams.hooks.hook_models import HookHandlerType, HooksConfig
from relay_teams.plugins.path_resolution import namespace_plugin_ref
from relay_teams.plugins.plugin_models import (
    PluginComponentKind,
    PluginComponentSource,
    PluginDiagnostic,
    PluginDiagnosticSeverity,
    PluginMonitorDefinition,
    PluginRecord,
)


def plugin_command_audit_diagnostics(
    record: PluginRecord,
) -> tuple[PluginDiagnostic, ...]:
    diagnostics: list[PluginDiagnostic] = []
    diagnostics.extend(_hook_command_diagnostics(record.hook_sources))
    diagnostics.extend(_mcp_command_diagnostics(record.mcp_sources))
    diagnostics.extend(
        _monitor_command_diagnostics(
            record=record,
            definitions=record.monitor_definitions,
        )
    )
    return tuple(diagnostics)


def _hook_command_diagnostics(
    sources: tuple[PluginComponentSource, ...],
) -> tuple[PluginDiagnostic, ...]:
    diagnostics: list[PluginDiagnostic] = []
    for source in sources:
        payload = _component_payload(source)
        if payload is None:
            continue
        normalized_payload = normalize_hooks_payload(payload, tolerant=True)
        try:
            config = HooksConfig.model_validate(normalized_payload)
        except ValidationError:
            continue
        for event_name, groups in config.hooks.items():
            for group in groups:
                for handler in group.hooks:
                    if handler.type != HookHandlerType.COMMAND:
                        continue
                    command = str(handler.command or "").strip()
                    if not command:
                        continue
                    diagnostics.append(
                        _audit_diagnostic(
                            source=source,
                            component=PluginComponentKind.HOOKS,
                            message=(
                                "Plugin command hook: "
                                f"{event_name.value}/{group.matcher} -> {command}"
                            ),
                        )
                    )
    return tuple(diagnostics)


def _mcp_command_diagnostics(
    sources: tuple[PluginComponentSource, ...],
) -> tuple[PluginDiagnostic, ...]:
    diagnostics: list[PluginDiagnostic] = []
    for source in sources:
        payload = _component_payload(source)
        if payload is None:
            continue
        raw_servers = payload.get("mcpServers", payload)
        if not isinstance(raw_servers, dict):
            continue
        for raw_name, raw_config in raw_servers.items():
            local_name = str(raw_name).strip()
            if not local_name or not isinstance(raw_config, dict):
                continue
            command = _string_value(raw_config.get("command")).strip()
            if not command:
                continue
            server_name = namespace_plugin_ref(
                plugin_name=source.plugin_name,
                local_name=local_name,
            )
            args = _string_sequence(raw_config.get("args"))
            diagnostics.append(
                _audit_diagnostic(
                    source=source,
                    component=PluginComponentKind.MCP_SERVERS,
                    message=(
                        "Plugin MCP command: "
                        f"{server_name} -> {_command_summary(command, args)}"
                    ),
                )
            )
    return tuple(diagnostics)


def _monitor_command_diagnostics(
    *,
    record: PluginRecord,
    definitions: tuple[PluginMonitorDefinition, ...],
) -> tuple[PluginDiagnostic, ...]:
    diagnostics: list[PluginDiagnostic] = []
    source_path = record.monitor_sources[0].path if record.monitor_sources else None
    for definition in definitions:
        diagnostics.append(
            PluginDiagnostic(
                plugin_name=record.name,
                scope=record.scope,
                severity=PluginDiagnosticSeverity.INFO,
                component=PluginComponentKind.MONITORS,
                path=source_path,
                message=(
                    "Plugin monitor command: "
                    f"{definition.name} -> "
                    f"{_command_summary(definition.command, definition.args)}"
                ),
            )
        )
    return tuple(diagnostics)


def _component_payload(source: PluginComponentSource) -> dict[str, JsonValue] | None:
    if source.inline_config is not None:
        return source.inline_config
    return _load_json_object(source.path)


def _audit_diagnostic(
    *,
    source: PluginComponentSource,
    component: PluginComponentKind,
    message: str,
) -> PluginDiagnostic:
    return PluginDiagnostic(
        plugin_name=source.plugin_name,
        scope=source.scope,
        severity=PluginDiagnosticSeverity.INFO,
        component=component,
        path=source.path,
        message=message,
    )


def _load_json_object(path: Path) -> dict[str, JsonValue] | None:
    try:
        raw = loads(path.read_text(encoding="utf-8-sig"))
    except (JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return {str(key): _json_value(value) for key, value in raw.items()}


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


def _string_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""


def _string_sequence(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    if isinstance(value, tuple):
        return tuple(str(item) for item in value)
    return ()


def _command_summary(command: str, args: tuple[str, ...]) -> str:
    if not args:
        return command
    return f"{command} {' '.join(args)}"
