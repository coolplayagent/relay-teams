# -*- coding: utf-8 -*-
from __future__ import annotations

from json import loads
from pathlib import Path

from pydantic import JsonValue

from relay_teams.plugins.path_resolution import resolve_plugin_component_path
from relay_teams.plugins.plugin_models import (
    PluginComponentKind,
    PluginComponentSource,
    PluginDiagnostic,
    PluginDiagnosticSeverity,
    PluginManifest,
    PluginRecord,
    PluginScope,
)

_DEFAULT_RELAY_MANIFEST_CONFIG_DIR_NAME = ".relay-teams"
_CLAUDE_MANIFEST_RELATIVE_PATH = Path(".claude-plugin") / "plugin.json"


def load_plugin_record(
    *,
    plugin_root: Path,
    data_root: Path,
    manifest_config_dir_name: str = _DEFAULT_RELAY_MANIFEST_CONFIG_DIR_NAME,
    scope: PluginScope = PluginScope.LOCAL,
) -> tuple[PluginRecord | None, tuple[PluginDiagnostic, ...]]:
    resolved_root = plugin_root.expanduser().resolve()
    diagnostics: list[PluginDiagnostic] = []
    manifest_path = _find_manifest_path(
        plugin_root=resolved_root,
        manifest_config_dir_name=manifest_config_dir_name,
    )
    try:
        manifest = _load_manifest_or_default(
            plugin_root=resolved_root,
            manifest_path=manifest_path,
        )
        data_dir = data_root.expanduser().resolve() / manifest.name
        record = PluginRecord(
            name=manifest.name,
            version=manifest.version or "local",
            scope=scope,
            enabled=True,
            root_dir=resolved_root,
            data_dir=data_dir,
            manifest_path=manifest_path if manifest_path.exists() else None,
            manifest=manifest,
            skill_sources=_component_sources(
                manifest_value=manifest.skills,
                default_relative_path="./skills",
                component=PluginComponentKind.SKILLS,
                plugin_name=manifest.name,
                scope=scope,
                root_dir=resolved_root,
                data_dir=data_dir,
                diagnostics=diagnostics,
                require_directory=True,
            ),
            role_sources=_component_sources(
                manifest_value=manifest.roles,
                default_relative_path=("./roles", "./agents"),
                component=PluginComponentKind.ROLES,
                plugin_name=manifest.name,
                scope=scope,
                root_dir=resolved_root,
                data_dir=data_dir,
                diagnostics=diagnostics,
                require_directory=True,
            ),
            command_sources=_component_sources(
                manifest_value=manifest.commands,
                default_relative_path="./commands",
                component=PluginComponentKind.COMMANDS,
                plugin_name=manifest.name,
                scope=scope,
                root_dir=resolved_root,
                data_dir=data_dir,
                diagnostics=diagnostics,
                require_directory=True,
            ),
            hook_sources=_component_sources(
                manifest_value=manifest.hooks,
                default_relative_path="./hooks/hooks.json",
                component=PluginComponentKind.HOOKS,
                plugin_name=manifest.name,
                scope=scope,
                root_dir=resolved_root,
                data_dir=data_dir,
                diagnostics=diagnostics,
                require_directory=False,
            ),
            mcp_sources=_component_sources(
                manifest_value=manifest.mcp_servers,
                default_relative_path=("./.mcp.json", "./mcp.json"),
                component=PluginComponentKind.MCP_SERVERS,
                plugin_name=manifest.name,
                scope=scope,
                root_dir=resolved_root,
                data_dir=data_dir,
                diagnostics=diagnostics,
                require_directory=False,
            ),
        )
        return record, tuple(diagnostics)
    except Exception as exc:
        diagnostics.append(
            PluginDiagnostic(
                plugin_name=resolved_root.name,
                scope=scope,
                severity=PluginDiagnosticSeverity.ERROR,
                path=resolved_root,
                message=str(exc),
            )
        )
        return None, tuple(diagnostics)


def _load_manifest_or_default(
    *,
    plugin_root: Path,
    manifest_path: Path,
) -> PluginManifest:
    if not manifest_path.exists():
        return PluginManifest(name=plugin_root.name)
    raw = loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError(f"Plugin manifest must be a JSON object: {manifest_path}")
    return PluginManifest.model_validate(_json_object(raw))


def _find_manifest_path(*, plugin_root: Path, manifest_config_dir_name: str) -> Path:
    relay_path = plugin_root / _relay_manifest_relative_path(manifest_config_dir_name)
    if relay_path.exists():
        return relay_path
    claude_path = plugin_root / _CLAUDE_MANIFEST_RELATIVE_PATH
    if claude_path.exists():
        return claude_path
    return relay_path


def _relay_manifest_relative_path(manifest_config_dir_name: str) -> Path:
    normalized = manifest_config_dir_name.strip()
    if not normalized:
        normalized = _DEFAULT_RELAY_MANIFEST_CONFIG_DIR_NAME
    if normalized in {".", ".."} or Path(normalized).name != normalized:
        raise ValueError("Plugin manifest config directory name must be a safe segment")
    return Path(normalized) / "plugin.json"


def _component_sources(
    *,
    manifest_value: str | tuple[str, ...] | dict[str, JsonValue] | None,
    default_relative_path: str | tuple[str, ...],
    component: PluginComponentKind,
    plugin_name: str,
    scope: PluginScope,
    root_dir: Path,
    data_dir: Path,
    diagnostics: list[PluginDiagnostic],
    require_directory: bool,
) -> tuple[PluginComponentSource, ...]:
    raw_paths = _component_path_values(
        manifest_value=manifest_value,
        default_relative_path=default_relative_path,
        component=component,
        plugin_name=plugin_name,
        scope=scope,
        root_dir=root_dir,
        diagnostics=diagnostics,
    )
    sources: list[PluginComponentSource] = []
    for raw_path in raw_paths:
        try:
            path = resolve_plugin_component_path(
                plugin_root=root_dir,
                raw_path=raw_path,
            )
        except ValueError as exc:
            diagnostics.append(
                PluginDiagnostic(
                    plugin_name=plugin_name,
                    scope=scope,
                    severity=PluginDiagnosticSeverity.ERROR,
                    component=component,
                    path=root_dir,
                    message=str(exc),
                )
            )
            continue
        if require_directory:
            exists = path.is_dir()
        else:
            exists = path.exists()
        if not exists:
            continue
        if component == PluginComponentKind.MCP_SERVERS:
            try:
                _ = _load_component_json_object(path)
            except Exception as exc:
                diagnostics.append(
                    PluginDiagnostic(
                        plugin_name=plugin_name,
                        scope=scope,
                        severity=PluginDiagnosticSeverity.ERROR,
                        component=component,
                        path=path,
                        message=f"Invalid plugin MCP config: {exc}",
                    )
                )
                continue
        sources.append(
            PluginComponentSource(
                plugin_name=plugin_name,
                scope=scope,
                root_dir=root_dir,
                data_dir=data_dir,
                path=path,
            )
        )
    return tuple(sources)


def _component_path_values(
    *,
    manifest_value: str | tuple[str, ...] | dict[str, JsonValue] | None,
    default_relative_path: str | tuple[str, ...],
    component: PluginComponentKind,
    plugin_name: str,
    scope: PluginScope,
    root_dir: Path,
    diagnostics: list[PluginDiagnostic],
) -> tuple[str, ...]:
    if manifest_value is None:
        if isinstance(default_relative_path, str):
            return (default_relative_path,)
        return default_relative_path
    if isinstance(manifest_value, str):
        return (manifest_value,)
    if isinstance(manifest_value, tuple):
        return manifest_value
    diagnostics.append(
        PluginDiagnostic(
            plugin_name=plugin_name,
            scope=scope,
            severity=PluginDiagnosticSeverity.ERROR,
            component=component,
            path=root_dir,
            message=(
                "Inline plugin component configs are not supported; "
                "provide a component path instead"
            ),
        )
    )
    return ()


def _json_object(raw: dict[object, object]) -> dict[str, JsonValue]:
    return {str(key): _json_value(value) for key, value in raw.items()}


def _load_component_json_object(path: Path) -> dict[str, JsonValue]:
    raw = loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError(f"Plugin component must be a JSON object: {path}")
    return _json_object(raw)


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
