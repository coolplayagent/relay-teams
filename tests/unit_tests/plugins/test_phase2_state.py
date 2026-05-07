# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import JsonValue

from relay_teams.plugins.config_manager import (
    PluginConfigManager,
    _validate_user_config_field_type,
)
from relay_teams.plugins.audit import plugin_command_audit_diagnostics
from relay_teams.secrets import AppSecretStore
from relay_teams.plugins.plugin_models import (
    PluginComponentSource,
    PluginDiagnostic,
    PluginDiagnosticSeverity,
    PluginInstallSource,
    PluginInstallSourceKind,
    PluginManifest,
    PluginMonitorDefinition,
    PluginRecord,
    PluginRegistry,
    PluginScope,
    PluginUserConfigField,
)
from relay_teams.plugins.state_paths import (
    get_installed_plugin_version_dir,
    get_plugin_cache_root,
    get_plugin_data_root,
    get_plugin_installed_root,
    get_plugin_managed_state_file,
    get_plugin_project_local_state_file,
    get_plugin_project_state_file,
    get_plugin_state_file,
    get_plugin_user_state_file,
)
from relay_teams.plugins.user_config_secrets import PluginUserConfigSecretStore
from relay_teams.plugins.views import build_public_plugin_registry


class _FileOnlySecretStore(AppSecretStore):
    def has_usable_keyring_backend(self) -> bool:
        return False


def test_plugin_state_paths_use_app_config_dir_name(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "custom-config"
    project_root = tmp_path / "repo"

    assert get_plugin_user_state_file(app_config_dir=app_config_dir) == (
        app_config_dir.resolve() / "plugins" / "plugins.json"
    )
    assert get_plugin_project_state_file(
        app_config_dir=app_config_dir,
        project_root=project_root,
    ) == (project_root.resolve() / "custom-config" / "plugins.json")
    assert get_plugin_project_local_state_file(
        app_config_dir=app_config_dir,
        project_root=project_root,
    ) == (project_root.resolve() / "custom-config" / "plugins.local.json")
    assert get_plugin_state_file(
        scope=PluginScope.USER,
        app_config_dir=app_config_dir,
        project_root=project_root,
    ) == (app_config_dir.resolve() / "plugins" / "plugins.json")
    assert get_plugin_state_file(
        scope=PluginScope.PROJECT,
        app_config_dir=app_config_dir,
        project_root=project_root,
    ) == (project_root.resolve() / "custom-config" / "plugins.json")
    assert get_plugin_state_file(
        scope=PluginScope.PROJECT_LOCAL,
        app_config_dir=app_config_dir,
        project_root=project_root,
    ) == (project_root.resolve() / "custom-config" / "plugins.local.json")
    assert (
        get_plugin_state_file(
            scope=PluginScope.LOCAL,
            app_config_dir=app_config_dir,
            project_root=project_root,
        )
        is None
    )


def test_plugin_storage_paths_validate_safe_segments(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"

    assert get_plugin_data_root(app_config_dir=app_config_dir) == (
        app_config_dir.resolve() / "plugins" / "data"
    )
    assert get_plugin_installed_root(app_config_dir=app_config_dir) == (
        app_config_dir.resolve() / "plugins" / "installed"
    )
    assert get_plugin_cache_root(app_config_dir=app_config_dir) == (
        app_config_dir.resolve() / "plugins" / "cache"
    )

    with pytest.raises(ValueError, match="plugin_name must not be empty"):
        get_installed_plugin_version_dir(
            plugin_name=" ",
            version="1.0.0",
            app_config_dir=app_config_dir,
        )
    with pytest.raises(ValueError, match="version must be a safe path segment"):
        get_installed_plugin_version_dir(
            plugin_name="quality",
            version="../bad",
            app_config_dir=app_config_dir,
        )


def test_managed_plugin_state_path_uses_admin_env_var(
    monkeypatch,
    tmp_path: Path,
) -> None:
    managed_state_file = tmp_path / "admin" / "plugins.json"

    assert get_plugin_managed_state_file() is None

    monkeypatch.setenv("RELAY_TEAMS_MANAGED_PLUGINS_FILE", str(managed_state_file))

    assert get_plugin_managed_state_file() == managed_state_file.resolve()
    assert (
        get_plugin_state_file(scope=PluginScope.MANAGED) == managed_state_file.resolve()
    )


def test_plugin_command_audit_skips_invalid_sources_and_summarizes_commands(
    tmp_path: Path,
) -> None:
    root_dir = tmp_path / "quality"
    data_dir = tmp_path / "app" / "plugins" / "data" / "quality"
    hook_source = PluginComponentSource(
        plugin_name="quality",
        scope=PluginScope.USER,
        root_dir=root_dir,
        data_dir=data_dir,
        path=root_dir / "hooks.json",
        inline_config={
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {"type": "http", "url": "https://example.test"},
                            {"type": "command", "command": " "},
                            {"type": "command", "command": "echo ok"},
                        ]
                    }
                ]
            }
        },
    )
    mcp_source = PluginComponentSource(
        plugin_name="quality",
        scope=PluginScope.USER,
        root_dir=root_dir,
        data_dir=data_dir,
        path=root_dir / "mcp.json",
        inline_config={
            "mcpServers": {
                "": {"command": "ignored"},
                "bad": ["ignored"],
                "docs": {"command": "relay", "args": ["serve", 1]},
            }
        },
    )
    invalid_source = PluginComponentSource(
        plugin_name="quality",
        scope=PluginScope.USER,
        root_dir=root_dir,
        data_dir=data_dir,
        path=root_dir / "invalid.json",
    )
    plugin = PluginRecord(
        name="quality",
        version="1.0.0",
        scope=PluginScope.USER,
        root_dir=root_dir,
        data_dir=data_dir,
        manifest=PluginManifest(name="quality"),
        hook_sources=(hook_source, invalid_source),
        mcp_sources=(mcp_source, invalid_source),
        monitor_definitions=(PluginMonitorDefinition(name="watch", command="relay"),),
    )

    diagnostics = plugin_command_audit_diagnostics(plugin)

    messages = [diagnostic.message for diagnostic in diagnostics]
    assert "Plugin MCP command: quality:docs -> relay serve 1" in messages
    assert "Plugin monitor command: watch -> relay" in messages


def test_plugin_command_audit_preserves_empty_inline_mcp_config(
    tmp_path: Path,
) -> None:
    root_dir = tmp_path / "quality"
    data_dir = tmp_path / "app" / "plugins" / "data" / "quality"
    manifest_path = root_dir / "plugin.json"
    root_dir.mkdir()
    manifest_path.write_text(
        '{"mcpServers":{"leaked":{"command":"should-not-audit"}}}',
        encoding="utf-8",
    )
    mcp_source = PluginComponentSource(
        plugin_name="quality",
        scope=PluginScope.USER,
        root_dir=root_dir,
        data_dir=data_dir,
        path=manifest_path,
        inline_config={},
    )
    plugin = PluginRecord(
        name="quality",
        version="1.0.0",
        scope=PluginScope.USER,
        root_dir=root_dir,
        data_dir=data_dir,
        manifest=PluginManifest(name="quality"),
        mcp_sources=(mcp_source,),
    )

    diagnostics = plugin_command_audit_diagnostics(plugin)

    assert diagnostics == ()


def test_install_plugin_writes_user_state_and_runtime_loads_it(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    (plugin_root / "skills" / "review").mkdir(parents=True)

    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        user_config_secret_store=PluginUserConfigSecretStore(
            secret_store=_FileOnlySecretStore()
        ),
    )
    installed = manager.install_plugin(source=plugin_root, scope=PluginScope.USER)

    assert installed.name == "quality"
    state_path = app_config_dir / "plugins" / "plugins.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["plugins"][0]["name"] == "quality"
    expected_installed_root = get_installed_plugin_version_dir(
        plugin_name="quality",
        version="1.0.0",
        app_config_dir=app_config_dir,
    )
    assert payload["plugins"][0]["root_dir"] == str(expected_installed_root)
    assert payload["plugins"][0]["source"]["value"] == str(plugin_root.resolve())
    assert (expected_installed_root / "app" / "plugin.json").exists()

    registry = manager.load_registry()

    assert all(
        diagnostic.severity != PluginDiagnosticSeverity.ERROR
        for diagnostic in registry.diagnostics
    )
    assert len(registry.plugins) == 1
    assert registry.plugins[0].scope == PluginScope.USER
    assert registry.plugins[0].skill_sources[0].plugin_name == "quality"


def test_disable_enable_and_uninstall_update_state(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        user_config_secret_store=PluginUserConfigSecretStore(
            secret_store=_FileOnlySecretStore()
        ),
    )
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)

    disabled = manager.set_plugin_enabled(
        name="quality",
        scope=PluginScope.USER,
        enabled=False,
    )
    assert disabled.enabled is False
    assert manager.load_registry().plugins[0].enabled is False

    enabled = manager.set_plugin_enabled(
        name="quality",
        scope=PluginScope.USER,
        enabled=True,
    )
    assert enabled.enabled is True

    removed = manager.uninstall_plugin(name="quality", scope=PluginScope.USER)

    assert removed.name == "quality"
    assert manager.list_state_records() == ()


def test_user_config_is_validated_persisted_and_exposed_to_component_sources(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        user_config={
            "endpoint": {"type": "string", "default": "https://default.test"},
            "token": {"type": "string", "required": True, "sensitive": True},
        },
    )
    (plugin_root / "mcp.json").write_text(
        '{"docs": {"command": "${user_config.endpoint}"}}',
        encoding="utf-8",
    )
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        user_config_secret_store=PluginUserConfigSecretStore(
            secret_store=_FileOnlySecretStore()
        ),
    )
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)

    registry = manager.load_registry()

    assert registry.plugins[0].enabled is False
    assert "Missing required plugin user_config field(s): token" in (
        registry.diagnostics[0].message
    )

    configured = manager.set_plugin_user_config(
        name="quality",
        scope=PluginScope.USER,
        user_config={
            "endpoint": "https://configured.test",
            "token": "secret",
        },
    )

    assert configured.user_config == {
        "endpoint": "https://configured.test",
    }
    state_path = app_config_dir / "plugins" / "plugins.json"
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert "secret" not in state_path.read_text(encoding="utf-8")
    assert state_payload["plugins"][0]["user_config"] == {
        "endpoint": "https://configured.test",
    }
    registry = manager.load_registry()
    assert all(
        diagnostic.severity != PluginDiagnosticSeverity.ERROR
        for diagnostic in registry.diagnostics
    )
    assert registry.plugins[0].user_config["endpoint"] == "https://configured.test"
    assert registry.plugins[0].user_config["token"] == "<configured>"
    assert registry.plugins[0].mcp_sources[0].user_config["token"] == "secret"

    public_registry = build_public_plugin_registry(registry)
    public_plugin = public_registry.plugins[0]
    assert public_plugin.user_config["token"] == "<configured>"
    assert public_plugin.mcp_sources[0].user_config == {
        "endpoint": "https://configured.test",
        "token": "<configured>",
    }


def test_public_plugin_registry_redacts_sensitive_diagnostics(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        user_config={"token": {"type": "string", "sensitive": True}},
    )
    monitors_dir = plugin_root / "monitors"
    monitors_dir.mkdir()
    (monitors_dir / "monitors.json").write_text(
        json.dumps(
            {
                "monitors": [
                    {
                        "name": "watch",
                        "command": "${user_config.token}",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        user_config_secret_store=PluginUserConfigSecretStore(
            secret_store=_FileOnlySecretStore()
        ),
    )
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    manager.set_plugin_user_config(
        name="quality",
        scope=PluginScope.USER,
        user_config={"token": "secret-token"},
    )

    registry = manager.load_registry()
    assert any(
        "secret-token" in diagnostic.message for diagnostic in registry.diagnostics
    )

    public_registry = build_public_plugin_registry(registry)

    assert all(
        "secret-token" not in diagnostic.message
        for diagnostic in public_registry.diagnostics
    )
    assert any(
        "<configured>" in diagnostic.message
        for diagnostic in public_registry.diagnostics
    )


def test_public_plugin_registry_redacts_credentialed_source_urls(
    tmp_path: Path,
) -> None:
    plugin = PluginRecord(
        name="quality",
        version="1.0.0",
        scope=PluginScope.USER,
        root_dir=tmp_path / "quality",
        data_dir=tmp_path / "app" / "plugins" / "data" / "quality",
        source=PluginInstallSource(
            kind=PluginInstallSourceKind.GIT,
            value="https://token:secret@example.test/org/quality.git",
            ref="main",
        ),
        manifest=PluginManifest(name="quality"),
    )
    registry = PluginRegistry(plugins=(plugin,))

    public_registry = build_public_plugin_registry(registry)

    public_source = public_registry.plugins[0].source
    assert public_source is not None
    assert public_source.value == "https://<configured>@example.test/org/quality.git"
    assert public_source.ref == "main"


def test_public_plugin_registry_redacts_longest_sensitive_values_first(
    tmp_path: Path,
) -> None:
    source = PluginComponentSource(
        plugin_name="quality",
        scope=PluginScope.USER,
        root_dir=tmp_path / "quality",
        data_dir=tmp_path / "app" / "plugins" / "data" / "quality",
        path=tmp_path / "quality" / "commands",
        user_config={"short": "abc", "long": "abc123"},
    )
    plugin = PluginRecord(
        name="quality",
        version="1.0.0",
        scope=PluginScope.USER,
        root_dir=tmp_path / "quality",
        data_dir=tmp_path / "app" / "plugins" / "data" / "quality",
        manifest=PluginManifest(
            name="quality",
            user_config={
                "short": PluginUserConfigField(type="string", sensitive=True),
                "long": PluginUserConfigField(type="string", sensitive=True),
            },
        ),
        command_sources=(source,),
    )
    registry = PluginRegistry(
        plugins=(plugin,),
        diagnostics=(
            PluginDiagnostic(
                plugin_name="quality",
                scope=PluginScope.USER,
                severity=PluginDiagnosticSeverity.ERROR,
                message="Invalid command abc123",
            ),
        ),
    )

    public_registry = build_public_plugin_registry(registry)

    assert public_registry.diagnostics[0].message == "Invalid command <configured>"


def test_public_plugin_registry_does_not_expand_monitor_env_vars(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PLUGIN_MONITOR_SECRET", "secret-env")
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    monitors_dir = plugin_root / "monitors"
    monitors_dir.mkdir()
    (monitors_dir / "monitors.json").write_text(
        json.dumps(
            {
                "monitors": [
                    {
                        "name": "watch",
                        "command": "relay",
                        "args": ["${env:PLUGIN_MONITOR_SECRET}"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)

    registry = manager.load_registry()
    assert registry.plugins[0].monitor_definitions[0].args == ("secret-env",)

    public_registry = build_public_plugin_registry(registry)

    assert public_registry.plugins[0].monitor_definitions[0].args == ("",)
    assert any(
        "secret-env" in diagnostic.message for diagnostic in registry.diagnostics
    )
    assert all(
        "secret-env" not in diagnostic.message
        for diagnostic in public_registry.diagnostics
    )


def test_user_config_partial_update_preserves_existing_sensitive_values(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        user_config={
            "endpoint": {"type": "string", "default": "https://default.test"},
            "token": {"type": "string", "required": True, "sensitive": True},
        },
    )
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        user_config_secret_store=PluginUserConfigSecretStore(
            secret_store=_FileOnlySecretStore()
        ),
    )
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    manager.set_plugin_user_config(
        name="quality",
        scope=PluginScope.USER,
        user_config={"endpoint": "https://first.test", "token": "secret"},
    )

    updated = manager.set_plugin_user_config(
        name="quality",
        scope=PluginScope.USER,
        user_config={"endpoint": "https://second.test"},
    )

    assert updated.user_config == {"endpoint": "https://second.test"}
    registry = manager.load_registry()
    assert all(
        diagnostic.severity != PluginDiagnosticSeverity.ERROR
        for diagnostic in registry.diagnostics
    )
    assert registry.plugins[0].enabled is True
    assert registry.plugins[0].user_config["token"] == "<configured>"


def test_user_config_blank_optional_typed_value_clears_existing_value(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        user_config={
            "limit": {"type": "integer"},
            "label": {"type": "string"},
        },
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    manager.set_plugin_user_config(
        name="quality",
        scope=PluginScope.USER,
        user_config={"limit": 3, "label": "ci"},
    )

    updated = manager.set_plugin_user_config(
        name="quality",
        scope=PluginScope.USER,
        user_config={"limit": "", "label": ""},
    )

    assert updated.user_config == {"label": ""}
    registry = manager.load_registry()
    assert registry.plugins[0].user_config == {"label": ""}


def test_sensitive_user_config_preserves_typed_values_on_revalidation(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        user_config={
            "enabled": {"type": "boolean", "required": True, "sensitive": True},
        },
    )
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        user_config_secret_store=PluginUserConfigSecretStore(
            secret_store=_FileOnlySecretStore()
        ),
    )
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    manager.set_plugin_user_config(
        name="quality",
        scope=PluginScope.USER,
        user_config={"enabled": True},
    )
    manager.set_plugin_enabled(name="quality", scope=PluginScope.USER, enabled=False)

    enabled = manager.set_plugin_enabled(
        name="quality",
        scope=PluginScope.USER,
        enabled=True,
    )

    assert enabled.enabled is True


def test_sensitive_user_config_preserves_structured_and_null_values(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        user_config={
            "payload": {"type": "object", "required": True, "sensitive": True},
            "maybe": {"type": "json", "required": True, "sensitive": True},
        },
    )
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        user_config_secret_store=PluginUserConfigSecretStore(
            secret_store=_FileOnlySecretStore()
        ),
    )
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    manager.set_plugin_user_config(
        name="quality",
        scope=PluginScope.USER,
        user_config={"payload": {"paths": ["src"]}, "maybe": None},
    )

    registry = manager.load_registry()

    assert registry.plugins[0].enabled is True
    assert registry.plugins[0].user_config == {
        "payload": "<configured>",
        "maybe": "<configured>",
    }
    assert all(
        diagnostic.severity != PluginDiagnosticSeverity.ERROR
        for diagnostic in registry.diagnostics
    )


def test_sensitive_user_config_preserves_whitespace_values(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        user_config={
            "token": {"type": "string", "required": True, "sensitive": True},
        },
    )
    (plugin_root / "mcp.json").write_text(
        '{"docs": {"command": "${user_config.token}"}}',
        encoding="utf-8",
    )
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        user_config_secret_store=PluginUserConfigSecretStore(
            secret_store=_FileOnlySecretStore()
        ),
    )
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    manager.set_plugin_user_config(
        name="quality",
        scope=PluginScope.USER,
        user_config={"token": "  secret  "},
    )

    registry = manager.load_registry()

    assert registry.plugins[0].mcp_sources[0].user_config["token"] == "  secret  "


def test_user_config_rejects_unknown_fields(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality", user_config={"token": {}})
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)

    try:
        manager.set_plugin_user_config(
            name="quality",
            scope=PluginScope.USER,
            user_config={"unknown": "value"},
        )
    except ValueError as exc:
        assert "Unknown plugin user_config field(s): unknown" in str(exc)
    else:
        raise AssertionError("Expected unknown user_config field to fail")


def test_user_config_rejects_wrong_declared_type(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        user_config={"threshold": {"type": "number"}},
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)

    try:
        manager.set_plugin_user_config(
            name="quality",
            scope=PluginScope.USER,
            user_config={"threshold": "high"},
        )
    except ValueError as exc:
        assert "Plugin user_config field threshold must be number" in str(exc)
    else:
        raise AssertionError("Expected wrong user_config type to fail")


def test_user_config_shorthand_fields_default_to_string(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        user_config={"endpoint": {}},
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)

    updated = manager.set_plugin_user_config(
        name="quality",
        scope=PluginScope.USER,
        user_config={"endpoint": "https://docs.test"},
    )

    assert updated.user_config == {"endpoint": "https://docs.test"}
    assert manager.load_registry().plugins[0].manifest.user_config["endpoint"].type == (
        "string"
    )


@pytest.mark.parametrize(
    ("field_type", "value"),
    [
        ("string", "docs"),
        ("number", 1.5),
        ("integer", 2),
        ("boolean", True),
        ("array", ["a"]),
        ("object", {"a": "b"}),
        ("json", {"a": ["b"]}),
    ],
)
def test_user_config_field_type_validation_accepts_declared_types(
    field_type: str,
    value: JsonValue,
) -> None:
    _validate_user_config_field_type(
        key="setting",
        field_type=field_type,
        value=value,
    )


@pytest.mark.parametrize(
    ("field_type", "value", "message"),
    [
        ("string", 1, "must be string, got integer"),
        ("number", True, "must be number, got boolean"),
        ("integer", 1.2, "must be integer, got number"),
        ("boolean", "yes", "must be boolean, got string"),
        ("array", {"a": "b"}, "must be array, got object"),
        ("object", ["a"], "must be object, got array"),
        ("string", None, "must be string, got null"),
        ("unsupported", "value", "Unsupported plugin user_config type"),
    ],
)
def test_user_config_field_type_validation_reports_json_type_names(
    field_type: str,
    value: JsonValue,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _validate_user_config_field_type(
            key="setting",
            field_type=field_type,
            value=value,
        )


def test_plugin_user_config_secret_store_preserves_json_and_legacy_values(
    tmp_path: Path,
) -> None:
    store = PluginUserConfigSecretStore(secret_store=_FileOnlySecretStore())
    config_dir = tmp_path / "app"

    store.set_field(
        config_dir,
        plugin_name="quality",
        scope=PluginScope.USER,
        field_name="enabled",
        value=True,
    )
    store.set_field(
        config_dir,
        plugin_name="quality",
        scope=PluginScope.USER,
        field_name="token",
        value="",
    )
    store.set_field(
        config_dir,
        plugin_name="quality",
        scope=PluginScope.USER,
        field_name="payload",
        value={"paths": ["src"]},
    )
    store.set_field(
        config_dir,
        plugin_name="quality",
        scope=PluginScope.USER,
        field_name="maybe",
        value=None,
    )

    assert (
        store.get_field(
            config_dir,
            plugin_name="quality",
            scope=PluginScope.USER,
            field_name="enabled",
        )
        is True
    )
    assert (
        store.get_field(
            config_dir,
            plugin_name="quality",
            scope=PluginScope.USER,
            field_name="token",
        )
        == ""
    )
    assert store.get_field(
        config_dir,
        plugin_name="quality",
        scope=PluginScope.USER,
        field_name="payload",
    ) == {"paths": ["src"]}
    assert store.has_field(
        config_dir,
        plugin_name="quality",
        scope=PluginScope.USER,
        field_name="maybe",
    )
    assert (
        store.get_field(
            config_dir,
            plugin_name="quality",
            scope=PluginScope.USER,
            field_name="maybe",
        )
        is None
    )

    secret_file = config_dir / "secrets.json"
    payload = json.loads(secret_file.read_text(encoding="utf-8"))
    for entry in payload["entries"]:
        if entry["field_name"] == "enabled":
            entry["value"] = "legacy"
    secret_file.write_text(json.dumps(payload), encoding="utf-8")

    assert (
        store.get_field(
            config_dir,
            plugin_name="quality",
            scope=PluginScope.USER,
            field_name="enabled",
        )
        == "legacy"
    )
    store.delete_field(
        config_dir,
        plugin_name="quality",
        scope=PluginScope.USER,
        field_name="enabled",
    )
    assert (
        store.get_field(
            config_dir,
            plugin_name="quality",
            scope=PluginScope.USER,
            field_name="enabled",
        )
        is None
    )
    assert not store.has_field(
        config_dir,
        plugin_name="quality",
        scope=PluginScope.USER,
        field_name="enabled",
    )


def test_runtime_loads_read_only_managed_plugin_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "managed-quality"
    _write_plugin_manifest(plugin_root, name="quality")
    managed_state_file = tmp_path / "admin" / "managed-plugins.json"
    managed_state_file.parent.mkdir(parents=True)
    managed_state_file.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "quality",
                        "version": "1.0.0",
                        "scope": "managed",
                        "enabled": True,
                        "root_dir": str(plugin_root.resolve()),
                        "source": {
                            "kind": "local",
                            "value": str(plugin_root.resolve()),
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RELAY_TEAMS_MANAGED_PLUGINS_FILE", str(managed_state_file))

    manager = PluginConfigManager(app_config_dir=app_config_dir)
    registry = manager.load_registry()

    assert all(
        diagnostic.severity != PluginDiagnosticSeverity.ERROR
        for diagnostic in registry.diagnostics
    )
    assert len(registry.plugins) == 1
    assert registry.plugins[0].name == "quality"
    assert registry.plugins[0].scope == PluginScope.MANAGED

    try:
        manager.set_plugin_enabled(
            name="quality",
            scope=PluginScope.MANAGED,
            enabled=False,
        )
    except ValueError as exc:
        assert "Managed plugin state is read-only" in str(exc)
    else:
        raise AssertionError("Expected managed plugin mutation to fail")


def test_runtime_skips_invalid_persisted_plugin_state(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    state_path = app_config_dir / "plugins" / "plugins.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"plugins": "invalid"}', encoding="utf-8")

    registry = PluginConfigManager(app_config_dir=app_config_dir).load_registry()

    assert registry.plugins == ()
    assert len(registry.diagnostics) == 1
    assert "Invalid plugin state file" in registry.diagnostics[0].message


def test_install_plugin_strictly_rejects_invalid_manifest(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "bad"
    _write_plugin_manifest(plugin_root, name="../bad")
    manager = PluginConfigManager(app_config_dir=app_config_dir)

    try:
        manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    except ValueError as exc:
        assert "Plugin name must be identifier-safe" in str(exc)
    else:
        raise AssertionError("Expected invalid plugin install to fail")


def test_install_plugin_rejects_role_unknown_capabilities(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    roles_dir = plugin_root / "roles"
    roles_dir.mkdir()
    _write_role(
        roles_dir / "reviewer.md",
        role_id="reviewer",
        tools=("missing_tool",),
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)

    try:
        manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    except ValueError as exc:
        assert "Unknown tools" in str(exc)
    else:
        raise AssertionError("Expected plugin role capability validation to fail")


def test_install_plugin_rejects_hook_agent_non_subagent_role(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    roles_dir = plugin_root / "roles"
    roles_dir.mkdir()
    _write_role(
        roles_dir / "reviewer.md",
        role_id="reviewer",
        tools=("read",),
        mode="primary",
    )
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "agent",
                                    "role_id": "reviewer",
                                    "prompt": "Review this run.",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)

    try:
        manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    except ValueError as exc:
        assert "must reference a subagent role" in str(exc)
    else:
        raise AssertionError("Expected plugin hook role validation to fail")


def test_install_plugin_accepts_bom_prefixed_hook_config(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo ok",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8-sig",
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)

    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    registry = manager.load_registry()

    assert registry.plugins[0].component_counts.hooks == 1


def test_install_plugin_validates_full_payload_inline_hook_config(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    manifest_dir = plugin_root / "app"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "quality",
                "version": "1.0.0",
                "hooks": {
                    "hooks": {
                        "SessionStart": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "echo ok",
                                    }
                                ]
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)

    installed = manager.install_plugin(source=plugin_root, scope=PluginScope.USER)

    assert installed.name == "quality"


def test_install_plugin_accepts_normalized_legacy_hook_group_fields(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "if_condition": "Bash(git *)",
                            "tool_names": ["Read", "Write"],
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo ok",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)

    installed = manager.install_plugin(source=plugin_root, scope=PluginScope.USER)

    assert installed.name == "quality"
    assert manager.load_registry().plugins[0].component_counts.hooks == 1


def test_install_plugin_rejects_invalid_command_front_matter_name(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    commands_dir = plugin_root / "commands"
    commands_dir.mkdir()
    (commands_dir / "review.md").write_text(
        "---\nname: bad name\n---\nReview work.\n",
        encoding="utf-8",
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)

    try:
        manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    except ValueError as exc:
        assert "Invalid plugin command name" in str(exc)
    else:
        raise AssertionError("Expected plugin command validation to fail")


def test_install_plugin_rejects_empty_command_template(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    commands_dir = plugin_root / "commands"
    commands_dir.mkdir()
    (commands_dir / "review.md").write_text(
        "---\nname: review\n---\n",
        encoding="utf-8",
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)

    try:
        manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    except ValueError as exc:
        assert "Plugin command template must not be empty" in str(exc)
    else:
        raise AssertionError("Expected empty plugin command validation to fail")


def test_install_plugin_rejects_command_hook_missing_command(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)

    try:
        manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    except ValueError as exc:
        assert "command hook requires command" in str(exc)
    else:
        raise AssertionError("Expected hook handler validation to fail")


def test_install_plugin_rejects_session_start_agent_hook(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    roles_dir = plugin_root / "roles"
    roles_dir.mkdir()
    _write_role(
        roles_dir / "reviewer.md",
        role_id="reviewer",
        tools=("read",),
    )
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "agent",
                                    "role_id": "reviewer",
                                    "prompt": "Review this run.",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)

    try:
        manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    except ValueError as exc:
        assert "SessionStart only supports command hook handlers" in str(exc)
    else:
        raise AssertionError("Expected hook event compatibility validation to fail")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"hooks": []}, "Plugin hook config must contain hooks"),
        ({"hooks": {"UnknownEvent": []}}, "Unknown plugin hook event"),
        ({"hooks": {"PreToolUse": {}}}, "Plugin hook event groups must be a list"),
        ({"hooks": {"PreToolUse": [None]}}, "Plugin hook group must be an object"),
        (
            {"hooks": {"PreToolUse": [{"hooks": []}]}},
            "hook matcher group must contain at least one handler",
        ),
        (
            {"hooks": {"PreToolUse": [{"hooks": {}}]}},
            "Plugin hook group must contain hooks",
        ),
        (
            {
                "hooks": {
                    "Stop": [
                        {
                            "matcher": "shell",
                            "hooks": [{"type": "command", "command": "echo ok"}],
                        }
                    ]
                }
            },
            "Matcher is not supported for Stop hooks",
        ),
        (
            {"hooks": {"PreToolUse": [{"hooks": [None]}]}},
            "Plugin hook handler must be an object",
        ),
        (
            {"hooks": {"PreToolUse": [{"hooks": [{"type": "bogus"}]}]}},
            "Unknown plugin hook handler type",
        ),
        (
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {"type": "prompt", "prompt": "Check", "async": True}
                            ]
                        }
                    ]
                }
            },
            "Only command hook handlers may run async",
        ),
        (
            {
                "hooks": {
                    "Notification": [{"hooks": [{"type": "prompt", "prompt": "Check"}]}]
                }
            },
            "Notification only supports command or http hook handlers",
        ),
        (
            {"hooks": {"PreToolUse": [{"hooks": [{"type": "command"}]}]}},
            "command hook requires command",
        ),
        (
            {"hooks": {"PreToolUse": [{"hooks": [{"type": "http"}]}]}},
            "http hook requires url",
        ),
        (
            {"hooks": {"PreToolUse": [{"hooks": [{"type": "prompt"}]}]}},
            "prompt hook requires prompt",
        ),
        (
            {
                "hooks": {
                    "PreToolUse": [{"hooks": [{"type": "agent", "prompt": "Run"}]}]
                }
            },
            "Agent hook role_id is required",
        ),
        (
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "unknown": True,
                            "hooks": [{"type": "command", "command": "echo"}],
                        }
                    ]
                }
            },
            "Plugin hook group contains unknown field",
        ),
        (
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo ok",
                                    "extra": True,
                                }
                            ]
                        }
                    ]
                }
            },
            "Plugin hook handler contains unknown field",
        ),
        (
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo ok",
                                    "timeout": True,
                                }
                            ]
                        }
                    ]
                }
            },
            "Plugin hook handler timeout must be a number",
        ),
        (
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo ok",
                                    "timeout_seconds": 0,
                                }
                            ]
                        }
                    ]
                }
            },
            "Plugin hook handler timeout_seconds must be greater than 0",
        ),
        (
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo ok",
                                    "run_async": "yes",
                                }
                            ]
                        }
                    ]
                }
            },
            "Plugin hook handler run_async must be a boolean",
        ),
        (
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo ok",
                                    "on_error": "retry",
                                }
                            ]
                        }
                    ]
                }
            },
            "Invalid plugin hook on_error",
        ),
        (
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo ok",
                                    "shell": "cmd",
                                }
                            ]
                        }
                    ]
                }
            },
            "Invalid plugin hook shell",
        ),
        (
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "http",
                                    "url": "https://example.test",
                                    "headers": ["bad"],
                                }
                            ]
                        }
                    ]
                }
            },
            "Plugin hook handler headers must be an object",
        ),
        (
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "http",
                                    "url": "https://example.test",
                                    "headers": {"X-Test": 1},
                                }
                            ]
                        }
                    ]
                }
            },
            "Plugin hook handler headers must contain only strings",
        ),
        (
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo ok",
                                    "allowed_env_vars": [1],
                                }
                            ]
                        }
                    ]
                }
            },
            "Plugin hook handler allowed_env_vars must contain only strings",
        ),
        (
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": 1,
                                    "command": "echo ok",
                                }
                            ]
                        }
                    ]
                }
            },
            "Unknown plugin hook handler type",
        ),
    ],
)
def test_plugin_hook_validation_reports_invalid_payloads(
    tmp_path: Path,
    payload: dict[str, object],
    message: str,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(json.dumps(payload), encoding="utf-8")
    manager = PluginConfigManager(app_config_dir=app_config_dir)

    with pytest.raises(ValueError, match=message):
        manager.install_plugin(source=plugin_root, scope=PluginScope.USER)


def test_install_plugin_accepts_agent_hook_for_subagent_role(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    roles_dir = plugin_root / "roles"
    roles_dir.mkdir()
    _write_role(
        roles_dir / "reviewer.md",
        role_id="reviewer",
        tools=("read",),
        mode="subagent",
    )
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "agent",
                                    "role_id": "reviewer",
                                    "prompt": "Review this run.",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)

    installed = manager.install_plugin(source=plugin_root, scope=PluginScope.USER)

    assert installed.name == "quality"


def test_install_plugin_rejects_hook_unknown_agent_role(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "agent",
                                    "role_id": "reviewer",
                                    "prompt": "Review this run.",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)

    with pytest.raises(
        ValueError, match="Unknown agent hook role_id: quality:reviewer"
    ):
        manager.install_plugin(source=plugin_root, scope=PluginScope.USER)


def test_install_plugin_accepts_command_aliases_and_allowed_modes(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    commands_dir = plugin_root / "commands"
    commands_dir.mkdir()
    (commands_dir / "review.md").write_text(
        (
            "---\n"
            "name: review\n"
            "aliases:\n"
            "  - /qr\n"
            "  - qr\n"
            "allowed_modes: chat, plan\n"
            "allowed-modes:\n"
            "  - chat\n"
            "---\n"
            "Review work.\n"
        ),
        encoding="utf-8",
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)

    installed = manager.install_plugin(source=plugin_root, scope=PluginScope.USER)

    assert installed.name == "quality"


def test_install_plugin_rejects_invalid_command_aliases(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    commands_dir = plugin_root / "commands"
    commands_dir.mkdir()
    (commands_dir / "review.md").write_text(
        "---\naliases:\n  - bad alias\n---\nReview work.\n",
        encoding="utf-8",
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)

    with pytest.raises(ValueError, match="Invalid plugin command alias"):
        manager.install_plugin(source=plugin_root, scope=PluginScope.USER)


def test_install_plugin_rejects_invalid_command_allowed_modes(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    commands_dir = plugin_root / "commands"
    commands_dir.mkdir()
    (commands_dir / "review.md").write_text(
        "---\nallowed_modes:\n  - 1\n---\nReview work.\n",
        encoding="utf-8",
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)

    with pytest.raises(ValueError, match="Plugin command allowed_modes"):
        manager.install_plugin(source=plugin_root, scope=PluginScope.USER)


def _write_plugin_manifest(
    plugin_root: Path,
    *,
    name: str,
    user_config: dict[str, object] | None = None,
) -> None:
    manifest_dir = plugin_root / "app"
    manifest_dir.mkdir(parents=True)
    payload: dict[str, object] = {"name": name, "version": "1.0.0"}
    if user_config is not None:
        payload["userConfig"] = user_config
    (manifest_dir / "plugin.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _write_role(
    path: Path,
    *,
    role_id: str,
    tools: tuple[str, ...],
    mode: str = "subagent",
) -> None:
    tools_yaml = "\n".join(f"  - {tool}" for tool in tools)
    path.write_text(
        (
            "---\n"
            f"role_id: {role_id}\n"
            "name: Reviewer\n"
            "description: Reviews work.\n"
            "version: 1.0.0\n"
            f"mode: {mode}\n"
            "tools:\n"
            f"{tools_yaml}\n"
            "---\n"
            "Review work.\n"
        ),
        encoding="utf-8",
    )
