# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from json import loads
from pathlib import Path

from pydantic import JsonValue

from relay_teams.plugins.path_resolution import resolve_plugin_component_path
from relay_teams.plugins.plugin_models import (
    PluginComponentKind,
    PluginComponentCounts,
    PluginComponentSource,
    PluginDiagnostic,
    PluginDiagnosticSeverity,
    PluginManifest,
    PluginMonitorDefinition,
    PluginRecord,
    PluginScope,
    PluginSettings,
    PluginSettingsSource,
)
from relay_teams.plugins.substitution import substitute_plugin_vars

_DEFAULT_RELAY_MANIFEST_CONFIG_DIR_NAME = ".relay-teams"
_CLAUDE_MANIFEST_RELATIVE_PATH = Path(".claude-plugin") / "plugin.json"
_INLINE_COMPONENT_KINDS = {
    PluginComponentKind.HOOKS,
    PluginComponentKind.MCP_SERVERS,
    PluginComponentKind.MONITORS,
}


def load_plugin_record(
    *,
    plugin_root: Path,
    data_root: Path,
    manifest_config_dir_name: str = _DEFAULT_RELAY_MANIFEST_CONFIG_DIR_NAME,
    scope: PluginScope = PluginScope.LOCAL,
    require_manifest: bool = False,
    strict_explicit_paths: bool = False,
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
            require_manifest=require_manifest,
        )
        data_dir = data_root.expanduser().resolve() / manifest.name
        user_config = _default_user_config(manifest)
        skill_sources = _component_sources(
            manifest_value=manifest.skills,
            default_relative_path="./skills",
            component=PluginComponentKind.SKILLS,
            plugin_name=manifest.name,
            scope=scope,
            root_dir=resolved_root,
            data_dir=data_dir,
            manifest_path=manifest_path,
            user_config=user_config,
            diagnostics=diagnostics,
            require_directory=True,
            strict_explicit_paths=strict_explicit_paths,
        )
        role_sources = _component_sources(
            manifest_value=manifest.roles,
            default_relative_path=("./roles", "./agents"),
            component=PluginComponentKind.ROLES,
            plugin_name=manifest.name,
            scope=scope,
            root_dir=resolved_root,
            data_dir=data_dir,
            manifest_path=manifest_path,
            user_config=user_config,
            diagnostics=diagnostics,
            require_directory=True,
            strict_explicit_paths=strict_explicit_paths,
        )
        command_sources = _component_sources(
            manifest_value=manifest.commands,
            default_relative_path="./commands",
            component=PluginComponentKind.COMMANDS,
            plugin_name=manifest.name,
            scope=scope,
            root_dir=resolved_root,
            data_dir=data_dir,
            manifest_path=manifest_path,
            user_config=user_config,
            diagnostics=diagnostics,
            require_directory=True,
            strict_explicit_paths=strict_explicit_paths,
        )
        hook_sources = _component_sources(
            manifest_value=manifest.hooks,
            default_relative_path="./hooks/hooks.json",
            component=PluginComponentKind.HOOKS,
            plugin_name=manifest.name,
            scope=scope,
            root_dir=resolved_root,
            data_dir=data_dir,
            manifest_path=manifest_path,
            user_config=user_config,
            diagnostics=diagnostics,
            require_directory=False,
            strict_explicit_paths=strict_explicit_paths,
        )
        mcp_sources = _component_sources(
            manifest_value=manifest.mcp_servers,
            default_relative_path=("./.mcp.json", "./mcp.json"),
            component=PluginComponentKind.MCP_SERVERS,
            plugin_name=manifest.name,
            scope=scope,
            root_dir=resolved_root,
            data_dir=data_dir,
            manifest_path=manifest_path,
            user_config=user_config,
            diagnostics=diagnostics,
            require_directory=False,
            strict_explicit_paths=strict_explicit_paths,
        )
        monitor_sources = _component_sources(
            manifest_value=manifest.monitors,
            default_relative_path="./monitors/monitors.json",
            component=PluginComponentKind.MONITORS,
            plugin_name=manifest.name,
            scope=scope,
            root_dir=resolved_root,
            data_dir=data_dir,
            manifest_path=manifest_path,
            user_config=user_config,
            diagnostics=diagnostics,
            require_directory=False,
            strict_explicit_paths=strict_explicit_paths,
        )
        monitor_definitions = _monitor_definitions(
            sources=monitor_sources,
            diagnostics=diagnostics,
            strict=strict_explicit_paths,
        )
        settings_sources = _settings_sources(
            manifest_value=manifest.settings,
            plugin_name=manifest.name,
            scope=scope,
            root_dir=resolved_root,
            data_dir=data_dir,
            manifest_path=manifest_path,
            user_config=user_config,
            diagnostics=diagnostics,
            strict_explicit_paths=strict_explicit_paths,
        )
        record = PluginRecord(
            name=manifest.name,
            version=manifest.version or "local",
            scope=scope,
            enabled=True,
            root_dir=resolved_root,
            data_dir=data_dir,
            user_config=user_config,
            manifest_path=manifest_path if manifest_path.exists() else None,
            manifest=manifest,
            skill_sources=skill_sources,
            role_sources=role_sources,
            command_sources=command_sources,
            hook_sources=hook_sources,
            mcp_sources=mcp_sources,
            monitor_sources=monitor_sources,
            monitor_definitions=monitor_definitions,
            settings_sources=settings_sources,
            component_counts=PluginComponentCounts(
                skills=len(skill_sources),
                roles=len(role_sources),
                commands=len(command_sources),
                hooks=len(hook_sources),
                mcp_servers=len(mcp_sources),
                monitors=len(monitor_sources),
                settings=len(settings_sources),
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
    require_manifest: bool,
) -> PluginManifest:
    if not manifest_path.exists():
        if require_manifest:
            raise ValueError(f"Plugin manifest is required: {manifest_path}")
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
    manifest_path: Path,
    user_config: dict[str, JsonValue],
    diagnostics: list[PluginDiagnostic],
    require_directory: bool,
    strict_explicit_paths: bool,
) -> tuple[PluginComponentSource, ...]:
    manifest_declares_paths = manifest_value is not None
    if isinstance(manifest_value, dict):
        if component not in _INLINE_COMPONENT_KINDS:
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
        return (
            PluginComponentSource(
                plugin_name=plugin_name,
                scope=scope,
                root_dir=root_dir,
                data_dir=data_dir,
                path=_inline_source_path(
                    manifest_path=manifest_path, root_dir=root_dir
                ),
                user_config=user_config,
                inline_config=_inline_component_config(
                    component=component,
                    manifest_value=manifest_value,
                ),
            ),
        )
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
            if strict_explicit_paths and manifest_declares_paths:
                expected = "directory" if require_directory else "file"
                diagnostics.append(
                    PluginDiagnostic(
                        plugin_name=plugin_name,
                        scope=scope,
                        severity=PluginDiagnosticSeverity.ERROR,
                        component=component,
                        path=path,
                        message=f"Plugin component {expected} does not exist: {path}",
                    )
                )
            continue
        if component in {PluginComponentKind.MCP_SERVERS, PluginComponentKind.MONITORS}:
            try:
                _ = _load_component_json_object(path)
            except Exception as exc:
                config_label = (
                    "MCP" if component == PluginComponentKind.MCP_SERVERS else "monitor"
                )
                diagnostics.append(
                    PluginDiagnostic(
                        plugin_name=plugin_name,
                        scope=scope,
                        severity=PluginDiagnosticSeverity.ERROR,
                        component=component,
                        path=path,
                        message=f"Invalid plugin {config_label} config: {exc}",
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
                user_config=user_config,
            )
        )
    return tuple(sources)


def _monitor_definitions(
    *,
    sources: tuple[PluginComponentSource, ...],
    diagnostics: list[PluginDiagnostic],
    strict: bool,
    allow_env: bool = True,
) -> tuple[PluginMonitorDefinition, ...]:
    definitions: list[PluginMonitorDefinition] = []
    for source in sources:
        payload = _load_plugin_monitor_payload(source=source, diagnostics=diagnostics)
        if payload is None:
            continue
        raw_monitors = payload.get("monitors", ())
        if not isinstance(raw_monitors, list):
            diagnostics.append(
                PluginDiagnostic(
                    plugin_name=source.plugin_name,
                    scope=source.scope,
                    severity=PluginDiagnosticSeverity.ERROR
                    if strict
                    else PluginDiagnosticSeverity.WARNING,
                    component=PluginComponentKind.MONITORS,
                    path=source.path,
                    message="Plugin monitors config must contain a monitors list",
                )
            )
            continue
        for index, raw_monitor in enumerate(raw_monitors):
            if not isinstance(raw_monitor, dict):
                diagnostics.append(
                    PluginDiagnostic(
                        plugin_name=source.plugin_name,
                        scope=source.scope,
                        severity=PluginDiagnosticSeverity.ERROR,
                        component=PluginComponentKind.MONITORS,
                        path=source.path,
                        message=f"Plugin monitor entry {index} must be an object",
                    )
                )
                continue
            substituted = substitute_plugin_vars(
                value=_json_object(raw_monitor),
                plugin_root=source.root_dir,
                plugin_data=source.data_dir,
                user_config=source.user_config,
                allow_env=allow_env,
            )
            if not isinstance(substituted, dict):
                continue
            try:
                definitions.append(PluginMonitorDefinition.model_validate(substituted))
            except Exception as exc:
                diagnostics.append(
                    PluginDiagnostic(
                        plugin_name=source.plugin_name,
                        scope=source.scope,
                        severity=PluginDiagnosticSeverity.ERROR,
                        component=PluginComponentKind.MONITORS,
                        path=source.path,
                        message=f"Invalid plugin monitor entry {index}: {exc}",
                    )
                )
    return tuple(definitions)


def _load_plugin_monitor_payload(
    *,
    source: PluginComponentSource,
    diagnostics: list[PluginDiagnostic],
) -> dict[str, JsonValue] | None:
    if source.inline_config is not None:
        return source.inline_config
    try:
        return _load_component_json_object(source.path)
    except Exception as exc:
        diagnostics.append(
            PluginDiagnostic(
                plugin_name=source.plugin_name,
                scope=source.scope,
                severity=PluginDiagnosticSeverity.ERROR,
                component=PluginComponentKind.MONITORS,
                path=source.path,
                message=f"Invalid plugin monitor config: {exc}",
            )
        )
        return None


def _default_user_config(manifest: PluginManifest) -> dict[str, JsonValue]:
    defaults: dict[str, JsonValue] = {}
    for key, field in manifest.user_config.items():
        if field.default is not None:
            defaults[key] = field.default
    return defaults


def reload_plugin_settings_source(
    source: PluginSettingsSource,
) -> tuple[PluginSettingsSource | None, tuple[PluginDiagnostic, ...]]:
    diagnostics: list[PluginDiagnostic] = []
    if source.inline_config is None:
        settings = _load_plugin_settings(
            path=source.path,
            plugin_name=source.plugin_name,
            scope=source.scope,
            root_dir=source.root_dir,
            data_dir=source.data_dir,
            user_config=source.user_config,
            diagnostics=diagnostics,
            strict=False,
        )
    else:
        settings = _load_plugin_settings_payload(
            payload=source.inline_config,
            path=source.path,
            plugin_name=source.plugin_name,
            scope=source.scope,
            root_dir=source.root_dir,
            data_dir=source.data_dir,
            user_config=source.user_config,
            diagnostics=diagnostics,
            strict=False,
        )
    if settings is None:
        return None, tuple(diagnostics)
    return source.model_copy(update={"settings": settings}), tuple(diagnostics)


def load_plugin_monitor_definitions(
    source: PluginComponentSource,
    *,
    strict: bool = False,
    allow_env: bool = True,
) -> tuple[tuple[PluginMonitorDefinition, ...], tuple[PluginDiagnostic, ...]]:
    diagnostics: list[PluginDiagnostic] = []
    definitions = _monitor_definitions(
        sources=(source,),
        diagnostics=diagnostics,
        strict=strict,
        allow_env=allow_env,
    )
    return definitions, tuple(diagnostics)


def _settings_sources(
    *,
    manifest_value: str | dict[str, JsonValue] | None,
    plugin_name: str,
    scope: PluginScope,
    root_dir: Path,
    data_dir: Path,
    manifest_path: Path,
    user_config: dict[str, JsonValue],
    diagnostics: list[PluginDiagnostic],
    strict_explicit_paths: bool,
) -> tuple[PluginSettingsSource, ...]:
    if isinstance(manifest_value, dict):
        inline_config = _json_object(manifest_value)
        path = _inline_source_path(manifest_path=manifest_path, root_dir=root_dir)
        settings = _load_plugin_settings_payload(
            payload=inline_config,
            path=path,
            plugin_name=plugin_name,
            scope=scope,
            root_dir=root_dir,
            data_dir=data_dir,
            user_config=user_config,
            diagnostics=diagnostics,
            strict=strict_explicit_paths,
        )
        if settings is None:
            return ()
        return (
            PluginSettingsSource(
                plugin_name=plugin_name,
                scope=scope,
                root_dir=root_dir,
                data_dir=data_dir,
                path=path,
                user_config=user_config,
                inline_config=inline_config,
                settings=settings,
            ),
        )
    raw_paths = _component_path_values(
        manifest_value=manifest_value,
        default_relative_path="./settings.json",
        component=PluginComponentKind.SETTINGS,
        plugin_name=plugin_name,
        scope=scope,
        root_dir=root_dir,
        diagnostics=diagnostics,
    )
    sources: list[PluginSettingsSource] = []
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
                    component=PluginComponentKind.SETTINGS,
                    path=root_dir,
                    message=str(exc),
                )
            )
            continue
        if not path.exists():
            if strict_explicit_paths and manifest_value is not None:
                diagnostics.append(
                    PluginDiagnostic(
                        plugin_name=plugin_name,
                        scope=scope,
                        severity=PluginDiagnosticSeverity.ERROR,
                        component=PluginComponentKind.SETTINGS,
                        path=path,
                        message=f"Plugin component file does not exist: {path}",
                    )
                )
            continue
        settings = _load_plugin_settings(
            path=path,
            plugin_name=plugin_name,
            scope=scope,
            root_dir=root_dir,
            data_dir=data_dir,
            user_config=user_config,
            diagnostics=diagnostics,
            strict=strict_explicit_paths,
        )
        if settings is None:
            continue
        sources.append(
            PluginSettingsSource(
                plugin_name=plugin_name,
                scope=scope,
                root_dir=root_dir,
                data_dir=data_dir,
                path=path,
                user_config=user_config,
                settings=settings,
            )
        )
    return tuple(sources)


def _load_plugin_settings(
    *,
    path: Path,
    plugin_name: str,
    scope: PluginScope,
    root_dir: Path,
    data_dir: Path,
    user_config: dict[str, JsonValue],
    diagnostics: list[PluginDiagnostic],
    strict: bool,
) -> PluginSettings | None:
    try:
        payload = _load_component_json_object(path)
    except Exception as exc:
        diagnostics.append(
            PluginDiagnostic(
                plugin_name=plugin_name,
                scope=scope,
                severity=PluginDiagnosticSeverity.ERROR,
                component=PluginComponentKind.SETTINGS,
                path=path,
                message=f"Invalid plugin settings config: {exc}",
            )
        )
        return None
    return _load_plugin_settings_payload(
        payload=payload,
        path=path,
        plugin_name=plugin_name,
        scope=scope,
        root_dir=root_dir,
        data_dir=data_dir,
        user_config=user_config,
        diagnostics=diagnostics,
        strict=strict,
    )


def _load_plugin_settings_payload(
    *,
    payload: dict[str, JsonValue],
    path: Path,
    plugin_name: str,
    scope: PluginScope,
    root_dir: Path,
    data_dir: Path,
    user_config: dict[str, JsonValue],
    diagnostics: list[PluginDiagnostic],
    strict: bool,
) -> PluginSettings | None:
    substituted = substitute_plugin_vars(
        value=payload,
        plugin_root=root_dir,
        plugin_data=data_dir,
        user_config=user_config,
        allow_env=False,
    )
    if not isinstance(substituted, dict):
        return PluginSettings()
    allowed_keys = {"agent"}
    unknown_keys = sorted(set(substituted) - allowed_keys)
    if unknown_keys:
        diagnostics.append(
            PluginDiagnostic(
                plugin_name=plugin_name,
                scope=scope,
                severity=PluginDiagnosticSeverity.ERROR
                if strict
                else PluginDiagnosticSeverity.WARNING,
                component=PluginComponentKind.SETTINGS,
                path=path,
                message="Unknown plugin settings field(s): " + ", ".join(unknown_keys),
            )
        )
        if strict:
            return None
    filtered = {key: value for key, value in substituted.items() if key in allowed_keys}
    try:
        return PluginSettings.model_validate(filtered)
    except Exception as exc:
        diagnostics.append(
            PluginDiagnostic(
                plugin_name=plugin_name,
                scope=scope,
                severity=PluginDiagnosticSeverity.ERROR,
                component=PluginComponentKind.SETTINGS,
                path=path,
                message=f"Invalid plugin settings config: {exc}",
            )
        )
        return None


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


def _inline_source_path(*, manifest_path: Path, root_dir: Path) -> Path:
    if manifest_path.exists():
        return manifest_path
    return root_dir


def _inline_component_config(
    *,
    component: PluginComponentKind,
    manifest_value: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    if component == PluginComponentKind.HOOKS and "hooks" not in manifest_value:
        return {"hooks": _json_object(manifest_value)}
    return _json_object(manifest_value)


def _json_object(raw: Mapping[str, object]) -> dict[str, JsonValue]:
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
