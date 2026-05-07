# -*- coding: utf-8 -*-
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from pydantic import JsonValue

from relay_teams.plugins.manifest_loader import (
    load_plugin_monitor_definitions,
    reload_plugin_settings_source,
)
from relay_teams.plugins.plugin_models import (
    PluginComponentSource,
    PluginDiagnostic,
    PluginInstallSource,
    PluginMonitorDefinition,
    PluginRecord,
    PluginRegistry,
    PluginSettingsSource,
)

_MASKED_CONFIG_VALUE = "<configured>"


def build_public_plugin_registry(registry: PluginRegistry) -> PluginRegistry:
    return registry.model_copy(
        update={
            "plugins": tuple(
                _public_plugin_record(plugin) for plugin in registry.plugins
            ),
            "diagnostics": _public_diagnostics(registry),
        }
    )


def _public_plugin_record(plugin: PluginRecord) -> PluginRecord:
    public_user_config = _public_user_config(
        plugin=plugin, user_config=plugin.user_config
    )
    return plugin.model_copy(
        update={
            "source": _public_install_source(plugin.source),
            "user_config": public_user_config,
            "skill_sources": _public_component_sources(
                sources=plugin.skill_sources,
                public_user_config=public_user_config,
            ),
            "role_sources": _public_component_sources(
                sources=plugin.role_sources,
                public_user_config=public_user_config,
            ),
            "command_sources": _public_component_sources(
                sources=plugin.command_sources,
                public_user_config=public_user_config,
            ),
            "hook_sources": _public_component_sources(
                sources=plugin.hook_sources,
                public_user_config=public_user_config,
            ),
            "mcp_sources": _public_component_sources(
                sources=plugin.mcp_sources,
                public_user_config=public_user_config,
            ),
            "monitor_sources": _public_component_sources(
                sources=plugin.monitor_sources,
                public_user_config=public_user_config,
            ),
            "monitor_definitions": _public_monitor_definitions(
                sources=plugin.monitor_sources,
                public_user_config=public_user_config,
            ),
            "settings_sources": _public_settings_sources(
                sources=plugin.settings_sources,
                public_user_config=public_user_config,
            ),
        }
    )


def _public_install_source(
    source: PluginInstallSource | None,
) -> PluginInstallSource | None:
    if source is None:
        return None
    return source.model_copy(update={"value": _public_source_value(source.value)})


def _public_source_value(value: str) -> str:
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc or "@" not in parts.netloc:
        return value
    netloc = f"{_MASKED_CONFIG_VALUE}@{parts.netloc.rsplit('@', 1)[1]}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _public_user_config(
    *,
    plugin: PluginRecord,
    user_config: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    public: dict[str, JsonValue] = {}
    for key, value in user_config.items():
        field = plugin.manifest.user_config.get(key)
        if field is not None and field.sensitive:
            public[key] = _MASKED_CONFIG_VALUE
            continue
        public[key] = value
    return public


def _public_component_sources(
    *,
    sources: tuple[PluginComponentSource, ...],
    public_user_config: dict[str, JsonValue],
) -> tuple[PluginComponentSource, ...]:
    return tuple(
        source.model_copy(update={"user_config": public_user_config})
        for source in sources
    )


def _public_monitor_definitions(
    *,
    sources: tuple[PluginComponentSource, ...],
    public_user_config: dict[str, JsonValue],
) -> tuple[PluginMonitorDefinition, ...]:
    definitions: list[PluginMonitorDefinition] = []
    for source in sources:
        source_definitions, _ = load_plugin_monitor_definitions(
            source.model_copy(update={"user_config": public_user_config}),
            allow_env=False,
        )
        definitions.extend(source_definitions)
    return tuple(definitions)


def _public_settings_sources(
    *,
    sources: tuple[PluginSettingsSource, ...],
    public_user_config: dict[str, JsonValue],
) -> tuple[PluginSettingsSource, ...]:
    public_sources: list[PluginSettingsSource] = []
    for source in sources:
        updated_source, _ = reload_plugin_settings_source(
            source.model_copy(update={"user_config": public_user_config})
        )
        if updated_source is not None:
            public_sources.append(updated_source)
    return tuple(public_sources)


def _public_diagnostics(registry: PluginRegistry) -> tuple[PluginDiagnostic, ...]:
    plugins_by_name = {plugin.name: plugin for plugin in registry.plugins}
    return tuple(
        _public_diagnostic(
            diagnostic=diagnostic,
            plugin=plugins_by_name.get(diagnostic.plugin_name),
        )
        for diagnostic in registry.diagnostics
    )


def _public_diagnostic(
    *,
    diagnostic: PluginDiagnostic,
    plugin: PluginRecord | None,
) -> PluginDiagnostic:
    if plugin is None:
        return diagnostic
    message = diagnostic.message
    for secret_value in _sensitive_user_config_values(plugin):
        message = message.replace(secret_value, _MASKED_CONFIG_VALUE)
    if message == diagnostic.message:
        return diagnostic
    return diagnostic.model_copy(update={"message": message})


def _sensitive_user_config_values(plugin: PluginRecord) -> tuple[str, ...]:
    sensitive_keys = {
        key for key, field in plugin.manifest.user_config.items() if field.sensitive
    }
    values: list[str] = list(_private_monitor_definition_values(plugin))
    if not sensitive_keys:
        return _longest_first_unique(values)
    for source in _all_component_sources(plugin):
        for key in sensitive_keys:
            value = source.user_config.get(key)
            if isinstance(value, (str, int, float, bool)):
                text = str(value)
                if text and text != _MASKED_CONFIG_VALUE:
                    values.append(text)
    return _longest_first_unique(values)


def _longest_first_unique(values: list[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            dict.fromkeys(values),
            key=len,
            reverse=True,
        )
    )


def _all_component_sources(plugin: PluginRecord) -> tuple[PluginComponentSource, ...]:
    return (
        *plugin.skill_sources,
        *plugin.role_sources,
        *plugin.command_sources,
        *plugin.hook_sources,
        *plugin.mcp_sources,
        *plugin.monitor_sources,
    )


def _private_monitor_definition_values(plugin: PluginRecord) -> tuple[str, ...]:
    public_user_config = _public_user_config(
        plugin=plugin,
        user_config=plugin.user_config,
    )
    public_definitions = _public_monitor_definitions(
        sources=plugin.monitor_sources,
        public_user_config=public_user_config,
    )
    public_values = {
        value
        for definition in public_definitions
        for value in _monitor_values(definition)
    }
    private_values: list[str] = []
    for definition in plugin.monitor_definitions:
        for value in _monitor_values(definition):
            if value and value not in public_values:
                private_values.append(value)
    return tuple(private_values)


def _monitor_values(definition: PluginMonitorDefinition) -> tuple[str, ...]:
    return definition.command, *definition.args
