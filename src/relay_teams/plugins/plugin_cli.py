# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
import json
from pathlib import Path
from typing import TypedDict

from pydantic import JsonValue
import typer

from relay_teams.paths import get_app_config_dir, get_project_root_or_none
from relay_teams.plugins.config_manager import PluginConfigManager
from relay_teams.plugins.marketplace_models import PluginMarketplaceEntry
from relay_teams.plugins.marketplace_service import PluginMarketplaceService
from relay_teams.plugins.plugin_models import (
    PluginDiagnostic,
    PluginDiagnosticSeverity,
    PluginInstallSourceKind,
    PluginRecord,
    PluginScope,
    PluginStateRecord,
)

plugin_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Install, inspect, enable, and disable Relay Teams plugins.",
)


class PluginOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


class PluginCliScope(str, Enum):
    USER = "user"
    PROJECT = "project"
    PROJECT_LOCAL = "project-local"


class PluginCliSourceKind(str, Enum):
    LOCAL = "local"
    GIT = "git"


class PluginListEntry(TypedDict):
    name: str
    version: str
    scope: str
    enabled: bool
    root_dir: str
    source: str
    user_config: dict[str, JsonValue]


class PluginAvailableEntry(TypedDict):
    name: str
    description: str
    latest: str
    versions: list[str]


class PluginValidateEntry(TypedDict):
    valid: bool
    name: str
    version: str
    root_dir: str
    diagnostics: list[dict[str, str]]


@plugin_app.command("install")
def plugin_install(
    source: str = typer.Argument(
        ..., help="Local plugin directory or marketplace name."
    ),
    scope: PluginCliScope = typer.Option(
        PluginCliScope.USER,
        "--scope",
        help="Installation scope.",
        case_sensitive=False,
    ),
    disabled: bool = typer.Option(
        False,
        "--disabled",
        help="Install the plugin but leave it disabled.",
    ),
    marketplace: Path | None = typer.Option(
        None,
        "--marketplace",
        help="Marketplace JSON file used when source is a marketplace plugin name.",
    ),
    source_kind: PluginCliSourceKind | None = typer.Option(
        None,
        "--source-kind",
        help="Source kind for non-marketplace installs.",
        case_sensitive=False,
    ),
    ref: str = typer.Option(
        "",
        "--ref",
        help="Git branch, tag, or commit to install.",
    ),
    version: str | None = typer.Option(
        None,
        "--version",
        help="Version to install from a marketplace.",
    ),
) -> None:
    manager = _build_manager()
    try:
        if marketplace is None:
            resolved_source_kind = _to_install_source_kind(
                source_kind
            ) or _infer_plugin_install_source_kind(source)
            if resolved_source_kind == PluginInstallSourceKind.GIT:
                record = manager.install_git_plugin(
                    source=source,
                    scope=_to_model_scope(scope),
                    ref=ref,
                    enabled=not disabled,
                )
            else:
                record = manager.install_plugin(
                    source=Path(source),
                    scope=_to_model_scope(scope),
                    enabled=not disabled,
                )
        else:
            record = manager.install_marketplace_plugin(
                name=source,
                marketplace=marketplace,
                scope=_to_model_scope(scope),
                version=version,
                enabled=not disabled,
            )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        f"Installed plugin {record.name} in {record.scope.value} scope "
        f"({'enabled' if record.enabled else 'disabled'})."
    )


@plugin_app.command("uninstall")
def plugin_uninstall(
    name: str = typer.Argument(..., help="Plugin name to uninstall."),
    scope: PluginCliScope = typer.Option(
        PluginCliScope.USER,
        "--scope",
        help="Installation scope.",
        case_sensitive=False,
    ),
    prune: bool = typer.Option(
        False,
        "--prune",
        help="Remove the plugin data directory from app config.",
    ),
) -> None:
    manager = _build_manager()
    try:
        record = manager.uninstall_plugin(
            name=name,
            scope=_to_model_scope(scope),
            prune=prune,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    suffix = " and pruned installed copies" if prune else ""
    typer.echo(f"Uninstalled plugin {record.name} from {record.scope.value}{suffix}.")


@plugin_app.command("enable")
def plugin_enable(
    name: str = typer.Argument(..., help="Plugin name to enable."),
    scope: PluginCliScope = typer.Option(
        PluginCliScope.USER,
        "--scope",
        help="Installation scope.",
        case_sensitive=False,
    ),
) -> None:
    record = _set_enabled(name=name, scope=scope, enabled=True)
    typer.echo(f"Enabled plugin {record.name} in {record.scope.value} scope.")


@plugin_app.command("disable")
def plugin_disable(
    name: str = typer.Argument(..., help="Plugin name to disable."),
    scope: PluginCliScope = typer.Option(
        PluginCliScope.USER,
        "--scope",
        help="Installation scope.",
        case_sensitive=False,
    ),
) -> None:
    record = _set_enabled(name=name, scope=scope, enabled=False)
    typer.echo(f"Disabled plugin {record.name} in {record.scope.value} scope.")


@plugin_app.command("update")
def plugin_update(
    name: str = typer.Argument(..., help="Plugin name to update."),
    scope: PluginCliScope = typer.Option(
        PluginCliScope.USER,
        "--scope",
        help="Installation scope.",
        case_sensitive=False,
    ),
    version: str | None = typer.Option(
        None,
        "--version",
        help="Version to install when updating marketplace plugins.",
    ),
) -> None:
    try:
        record = _build_manager().update_plugin(
            name=name,
            scope=_to_model_scope(scope),
            version=version,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Updated plugin {record.name} to {record.version}.")


@plugin_app.command("configure")
def plugin_configure(
    name: str = typer.Argument(..., help="Plugin name to configure."),
    scope: PluginCliScope = typer.Option(
        PluginCliScope.USER,
        "--scope",
        help="Installation scope.",
        case_sensitive=False,
    ),
    values: list[str] = typer.Option(
        (),
        "--set",
        help="Set a user_config value as key=value. JSON values are accepted.",
    ),
) -> None:
    manager = _build_manager()
    model_scope = _to_model_scope(scope)
    try:
        installed_record = _configured_plugin_record(
            manager=manager,
            name=name,
            scope=model_scope,
        )
        record = manager.set_plugin_user_config(
            name=name,
            scope=model_scope,
            user_config=_parse_user_config_values(
                values,
                field_types={
                    key: field.type
                    for key, field in installed_record.manifest.user_config.items()
                },
            ),
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Configured plugin {record.name} in {record.scope.value} scope.")


@plugin_app.command("prune")
def plugin_prune() -> None:
    removed = _build_manager().prune_installed_plugins()
    if not removed:
        typer.echo("No installed plugin versions pruned.")
        return
    typer.echo(f"Pruned {len(removed)} installed plugin version(s).")


@plugin_app.command("list")
def plugin_list(
    output_format: PluginOutputFormat = typer.Option(
        PluginOutputFormat.TABLE,
        "--format",
        help="Render as an ASCII table or JSON.",
        case_sensitive=False,
    ),
    available: bool = typer.Option(
        False,
        "--available",
        help="List marketplace plugins from --marketplace.",
    ),
    marketplace: Path | None = typer.Option(
        None,
        "--marketplace",
        help="Marketplace JSON file to list.",
    ),
) -> None:
    if available:
        if marketplace is None:
            raise typer.BadParameter("--available requires --marketplace")
        entries = PluginMarketplaceService().load_index(marketplace).plugins
        rows = [_to_available_entry(entry) for entry in entries]
        if output_format == PluginOutputFormat.JSON:
            typer.echo(json.dumps(rows, ensure_ascii=False))
            return
        _render_available_table(rows)
        return
    registry = _build_manager().load_registry()
    rows = [_to_list_entry(plugin) for plugin in registry.plugins]
    if output_format == PluginOutputFormat.JSON:
        typer.echo(
            json.dumps(
                {
                    "plugins": rows,
                    "diagnostics": [
                        _diagnostic_json(diagnostic)
                        for diagnostic in registry.diagnostics
                    ],
                },
                ensure_ascii=False,
            )
        )
        return
    _render_plugin_table(rows)
    _render_diagnostics(registry.diagnostics)


@plugin_app.command("validate")
def plugin_validate(
    path: Path = typer.Argument(..., help="Local plugin directory to validate."),
    output_format: PluginOutputFormat = typer.Option(
        PluginOutputFormat.TABLE,
        "--format",
        help="Render as text or JSON.",
        case_sensitive=False,
    ),
) -> None:
    record, diagnostics = _build_manager().validate_plugin(
        plugin_root=path,
        require_manifest=True,
        strict_explicit_paths=True,
    )
    valid = record is not None and not any(
        diagnostic.severity == PluginDiagnosticSeverity.ERROR
        for diagnostic in diagnostics
    )
    if output_format == PluginOutputFormat.JSON:
        typer.echo(json.dumps(_to_validate_entry(record, diagnostics, valid)))
        if not valid:
            raise typer.Exit(code=1)
        return
    if valid and record is not None:
        typer.echo(f"Plugin is valid: {record.name} ({record.version})")
        return
    typer.echo("Plugin is invalid.")
    _render_diagnostics(diagnostics)
    raise typer.Exit(code=1)


def _set_enabled(
    *,
    name: str,
    scope: PluginCliScope,
    enabled: bool,
) -> PluginStateRecord:
    try:
        return _build_manager().set_plugin_enabled(
            name=name,
            scope=_to_model_scope(scope),
            enabled=enabled,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _build_manager() -> PluginConfigManager:
    project_root = get_project_root_or_none(start_dir=Path.cwd())
    return PluginConfigManager.from_environment(
        app_config_dir=get_app_config_dir(),
        project_root=project_root,
    )


def _configured_plugin_record(
    *,
    manager: PluginConfigManager,
    name: str,
    scope: PluginScope,
) -> PluginRecord:
    for plugin in manager.load_registry().plugins:
        if plugin.name == name and plugin.scope == scope:
            return plugin
    raise ValueError(f"Plugin is not installed in {scope.value}: {name}")


def _to_model_scope(scope: PluginCliScope) -> PluginScope:
    if scope in {PluginCliScope.PROJECT, PluginCliScope.PROJECT_LOCAL}:
        project_root = get_project_root_or_none(start_dir=Path.cwd())
        if project_root is None:
            raise typer.BadParameter("Project scope requires a git repository")
    if scope == PluginCliScope.PROJECT:
        return PluginScope.PROJECT
    if scope == PluginCliScope.PROJECT_LOCAL:
        return PluginScope.PROJECT_LOCAL
    if scope == PluginCliScope.USER:
        return PluginScope.USER
    raise typer.BadParameter(f"Unsupported plugin scope: {scope.value}")


def _to_install_source_kind(
    source_kind: PluginCliSourceKind | None,
) -> PluginInstallSourceKind | None:
    if source_kind is None:
        return None
    if source_kind == PluginCliSourceKind.GIT:
        return PluginInstallSourceKind.GIT
    return PluginInstallSourceKind.LOCAL


def _infer_plugin_install_source_kind(source: str) -> PluginInstallSourceKind:
    normalized = source.strip().lower()
    if normalized.startswith(("http://", "https://", "ssh://", "git@")):
        return PluginInstallSourceKind.GIT
    if normalized.endswith(".git"):
        return PluginInstallSourceKind.GIT
    return PluginInstallSourceKind.LOCAL


def _to_list_entry(plugin: PluginRecord) -> PluginListEntry:
    return PluginListEntry(
        name=plugin.name,
        version=plugin.version,
        scope=plugin.scope.value,
        enabled=plugin.enabled,
        root_dir=plugin.root_dir.as_posix(),
        source=plugin.manifest_path.as_posix()
        if plugin.manifest_path is not None
        else plugin.root_dir.as_posix(),
        user_config=plugin.user_config,
    )


def _to_available_entry(plugin: PluginMarketplaceEntry) -> PluginAvailableEntry:
    latest = plugin.latest
    if not latest and plugin.versions:
        latest = plugin.selected_version(None).version
    return PluginAvailableEntry(
        name=plugin.name,
        description=plugin.description,
        latest=latest,
        versions=[version.version for version in plugin.versions],
    )


def _to_validate_entry(
    record: PluginRecord | None,
    diagnostics: tuple[PluginDiagnostic, ...],
    valid: bool,
) -> PluginValidateEntry:
    return PluginValidateEntry(
        valid=valid,
        name="" if record is None else record.name,
        version="" if record is None else record.version,
        root_dir="" if record is None else record.root_dir.as_posix(),
        diagnostics=[_diagnostic_json(diagnostic) for diagnostic in diagnostics],
    )


def _diagnostic_json(diagnostic: PluginDiagnostic) -> dict[str, str]:
    return {
        "plugin_name": diagnostic.plugin_name,
        "scope": diagnostic.scope.value,
        "severity": diagnostic.severity.value,
        "component": "" if diagnostic.component is None else diagnostic.component.value,
        "path": "" if diagnostic.path is None else diagnostic.path.as_posix(),
        "message": diagnostic.message,
    }


def _parse_user_config_values(
    values: list[str],
    field_types: dict[str, str] | None = None,
) -> dict[str, JsonValue]:
    parsed: dict[str, JsonValue] = {}
    for value in values:
        key, separator, raw_value = value.partition("=")
        normalized_key = key.strip()
        if not separator or not normalized_key:
            raise ValueError("Plugin config values must use key=value")
        parsed[normalized_key] = _parse_json_value(
            raw_value,
            field_type=None if field_types is None else field_types.get(normalized_key),
        )
    return parsed


def _parse_json_value(raw_value: str, field_type: str | None = None) -> JsonValue:
    if _normalized_user_config_type(field_type) in {"string", "text", "password"}:
        return raw_value
    try:
        loaded = json.loads(raw_value)
    except json.JSONDecodeError:
        return raw_value
    return _json_value(loaded)


def _normalized_user_config_type(field_type: str | None) -> str:
    if field_type is None:
        return ""
    return field_type.strip().lower() or "string"


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


def _render_plugin_table(rows: list[PluginListEntry]) -> None:
    if not rows:
        typer.echo("No plugins installed or configured.")
        return
    typer.echo(f"Plugins ({len(rows)} total)")
    name_width = max(len("Name"), *(len(row["name"]) for row in rows))
    version_width = max(len("Version"), *(len(row["version"]) for row in rows))
    scope_width = max(len("Scope"), *(len(row["scope"]) for row in rows))
    enabled_width = len("Enabled")
    root_width = max(len("Root"), *(len(row["root_dir"]) for row in rows))
    border = (
        f"+-{'-' * name_width}-+-{'-' * version_width}-+-{'-' * scope_width}-"
        f"+-{'-' * enabled_width}-+-{'-' * root_width}-+"
    )
    typer.echo(border)
    typer.echo(
        f"| {'Name'.ljust(name_width)} | "
        f"{'Version'.ljust(version_width)} | "
        f"{'Scope'.ljust(scope_width)} | "
        f"{'Enabled'.ljust(enabled_width)} | "
        f"{'Root'.ljust(root_width)} |"
    )
    typer.echo(border)
    for row in rows:
        enabled = str(row["enabled"]).lower()
        typer.echo(
            f"| {row['name'].ljust(name_width)} | "
            f"{row['version'].ljust(version_width)} | "
            f"{row['scope'].ljust(scope_width)} | "
            f"{enabled.ljust(enabled_width)} | "
            f"{row['root_dir'].ljust(root_width)} |"
        )
    typer.echo(border)


def _render_available_table(rows: list[PluginAvailableEntry]) -> None:
    if not rows:
        typer.echo("No marketplace plugins available.")
        return
    typer.echo(f"Available plugins ({len(rows)} total)")
    name_width = max(len("Name"), *(len(row["name"]) for row in rows))
    latest_width = max(len("Latest"), *(len(row["latest"]) for row in rows))
    description_width = max(
        len("Description"), *(len(row["description"]) for row in rows)
    )
    border = (
        f"+-{'-' * name_width}-+-{'-' * latest_width}-+-{'-' * description_width}-+"
    )
    typer.echo(border)
    typer.echo(
        f"| {'Name'.ljust(name_width)} | "
        f"{'Latest'.ljust(latest_width)} | "
        f"{'Description'.ljust(description_width)} |"
    )
    typer.echo(border)
    for row in rows:
        typer.echo(
            f"| {row['name'].ljust(name_width)} | "
            f"{row['latest'].ljust(latest_width)} | "
            f"{row['description'].ljust(description_width)} |"
        )
    typer.echo(border)


def _render_diagnostics(diagnostics: tuple[PluginDiagnostic, ...]) -> None:
    if not diagnostics:
        return
    typer.echo("Diagnostics")
    for diagnostic in diagnostics:
        path = "" if diagnostic.path is None else f" ({diagnostic.path})"
        typer.echo(f"- {diagnostic.severity.value}: {diagnostic.message}{path}")
