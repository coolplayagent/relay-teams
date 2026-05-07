# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import cast

import pytest

from relay_teams.hooks import HookLoader
from relay_teams.hooks.executors.command_executor import CommandHookExecutor
from relay_teams.hooks.executors.http_executor import HttpHookExecutor
from relay_teams.hooks.hook_event_models import SessionStartInput
from relay_teams.hooks.hook_models import HookSourceScope
from relay_teams.hooks.hook_models import (
    HookDecisionType,
    HookEventName,
    HookHandlerConfig,
    HookHandlerType,
)
from relay_teams.hooks.hook_runtime_state import HookRuntimeState
from relay_teams.hooks.hook_service import HookService
from relay_teams.mcp.mcp_config_manager import McpConfigManager
from relay_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
from relay_teams.plugins.config_manager import PluginConfigManager
from relay_teams.plugins.manifest_loader import _component_sources
from relay_teams.plugins.mcp_sources import load_plugin_mcp_specs
from relay_teams.plugins.path_resolution import (
    namespace_plugin_ref,
    resolve_plugin_component_path,
)
from relay_teams.plugins.plugin_models import (
    PluginComponentKind,
    PluginComponentSource,
    PluginDiagnosticSeverity,
    PluginScope,
)
from relay_teams.roles import RoleLoader
from relay_teams.skills import SkillRegistry
from relay_teams.commands import CommandRegistry


def test_resolve_plugin_component_path_rejects_traversal(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()

    with pytest.raises(ValueError, match="traverse|escapes"):
        resolve_plugin_component_path(
            plugin_root=plugin_root,
            raw_path="../outside",
        )


def test_resolve_plugin_component_path_rejects_empty_absolute_and_unprefixed(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()

    with pytest.raises(ValueError, match="must not be empty"):
        resolve_plugin_component_path(plugin_root=plugin_root, raw_path=" ")
    with pytest.raises(ValueError, match="must be relative"):
        resolve_plugin_component_path(
            plugin_root=plugin_root,
            raw_path=str((tmp_path / "outside").resolve()),
        )
    with pytest.raises(ValueError, match="must start with ./"):
        resolve_plugin_component_path(plugin_root=plugin_root, raw_path="roles")


def test_resolve_plugin_component_path_rejects_resolved_escape(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()

    with pytest.raises(ValueError, match="escapes"):
        resolve_plugin_component_path(
            plugin_root=plugin_root,
            raw_path="./nested/../../outside",
        )


def test_namespace_plugin_ref_rejects_blank_parts_and_preserves_existing_namespace() -> (
    None
):
    assert namespace_plugin_ref(plugin_name="quality", local_name="quality:review") == (
        "quality:review"
    )
    with pytest.raises(ValueError, match="plugin_name"):
        namespace_plugin_ref(plugin_name=" ", local_name="review")
    with pytest.raises(ValueError, match="local_name"):
        namespace_plugin_ref(plugin_name="quality", local_name=" ")


def test_plugin_registry_loads_default_component_sources(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    (plugin_root / "skills" / "review").mkdir(parents=True)
    (plugin_root / "roles").mkdir()
    (plugin_root / "commands").mkdir()
    (plugin_root / "hooks").mkdir()
    (plugin_root / "hooks" / "hooks.json").write_text(
        '{"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo ok"}]}]}}',
        encoding="utf-8",
    )
    (plugin_root / "mcp.json").write_text(
        '{"mcpServers": {"docs": {"command": "${RELAY_TEAMS_PLUGIN_ROOT}/bin/docs"}}}',
        encoding="utf-8",
    )

    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    assert all(
        diagnostic.severity != PluginDiagnosticSeverity.ERROR
        for diagnostic in registry.diagnostics
    )
    assert len(registry.plugins) == 1
    plugin = registry.plugins[0]
    assert plugin.name == "quality"
    assert len(plugin.skill_sources) == 1
    assert len(plugin.role_sources) == 1
    assert len(plugin.command_sources) == 1
    assert len(plugin.hook_sources) == 1
    assert len(plugin.mcp_sources) == 1
    assert plugin.component_counts.skills == 1
    assert plugin.component_counts.roles == 1
    assert plugin.component_counts.commands == 1
    assert plugin.component_counts.hooks == 1
    assert plugin.component_counts.mcp_servers == 1
    assert any(
        diagnostic.severity == PluginDiagnosticSeverity.INFO
        and "Plugin command hook" in diagnostic.message
        for diagnostic in registry.diagnostics
    )
    assert any(
        diagnostic.severity == PluginDiagnosticSeverity.INFO
        and "Plugin MCP command" in diagnostic.message
        for diagnostic in registry.diagnostics
    )


def test_local_plugin_dir_missing_required_user_config_is_disabled(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        user_config={"token": {"type": "string", "required": True}},
    )

    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    assert len(registry.plugins) == 1
    assert registry.plugins[0].enabled is False
    assert any(
        diagnostic.severity == PluginDiagnosticSeverity.ERROR
        and "Missing required plugin user_config field(s): token" in diagnostic.message
        for diagnostic in registry.diagnostics
    )


def test_plugin_registry_loads_default_monitor_source(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    monitors_dir = plugin_root / "monitors"
    monitors_dir.mkdir(parents=True)
    (monitors_dir / "monitors.json").write_text(
        '{"monitors": [{"name": "watch", "command": "echo ${plugin_root}", "args": ["${plugin_data}"]}]}',
        encoding="utf-8",
    )

    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    assert all(
        diagnostic.severity != PluginDiagnosticSeverity.ERROR
        for diagnostic in registry.diagnostics
    )
    assert len(registry.plugins) == 1
    assert len(registry.plugins[0].monitor_sources) == 1
    assert registry.monitor_sources()[0].path == monitors_dir / "monitors.json"
    assert len(registry.plugins[0].monitor_definitions) == 1
    assert registry.plugins[0].component_counts.monitors == 1
    monitor = registry.plugins[0].monitor_definitions[0]
    assert monitor.name == "watch"
    assert monitor.command == f"echo {plugin_root}"
    assert monitor.args == (str(tmp_path / "app" / "plugins" / "data" / "quality"),)
    assert any(
        diagnostic.severity == PluginDiagnosticSeverity.INFO
        and "Plugin monitor command" in diagnostic.message
        for diagnostic in registry.diagnostics
    )


def test_invalid_plugin_monitor_config_is_reported_and_skipped(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    monitors_dir = plugin_root / "monitors"
    monitors_dir.mkdir(parents=True)
    (monitors_dir / "monitors.json").write_text("[1, 2, 3]", encoding="utf-8")

    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    assert registry.monitor_sources() == ()
    assert len(registry.diagnostics) == 1
    assert registry.diagnostics[0].component == PluginComponentKind.MONITORS
    assert "Invalid plugin monitor config" in registry.diagnostics[0].message


def test_plugin_registry_uses_app_config_dir_name_for_relay_manifest(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "custom-config"
    plugin_root = tmp_path / "quality"
    manifest_dir = plugin_root / app_config_dir.name
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        '{"name":"quality","version":"2.0.0"}',
        encoding="utf-8",
    )

    registry = PluginConfigManager(
        app_config_dir=app_config_dir,
        plugin_dirs=(plugin_root,),
    ).load_registry()

    assert all(
        diagnostic.severity != PluginDiagnosticSeverity.ERROR
        for diagnostic in registry.diagnostics
    )
    assert len(registry.plugins) == 1
    assert registry.plugins[0].manifest_path == manifest_dir / "plugin.json"
    assert registry.plugins[0].version == "2.0.0"


def test_plugin_registry_reports_missing_plugin_directory(tmp_path: Path) -> None:
    missing_plugin_root = tmp_path / "missing"

    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(missing_plugin_root,),
    ).load_registry()

    assert registry.plugins == ()
    assert len(registry.diagnostics) == 1
    assert registry.diagnostics[0].path == missing_plugin_root.resolve()
    assert "does not exist" in registry.diagnostics[0].message


def test_plugin_registry_skips_duplicate_plugin_names(tmp_path: Path) -> None:
    first_plugin_root = tmp_path / "first"
    second_plugin_root = tmp_path / "second"
    _write_plugin_manifest(first_plugin_root, name="quality")
    _write_plugin_manifest(second_plugin_root, name="quality")

    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(first_plugin_root, second_plugin_root),
    ).load_registry()

    assert len(registry.plugins) == 1
    assert registry.plugins[0].root_dir == first_plugin_root
    assert len(registry.diagnostics) == 1
    assert registry.diagnostics[0].plugin_name == "quality"
    assert "Duplicate plugin name" in registry.diagnostics[0].message


def test_plugin_registry_rejects_unsafe_manifest_name(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    _write_plugin_manifest(plugin_root, name="../shared")

    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    assert registry.plugins == ()
    assert len(registry.diagnostics) == 1
    assert "identifier-safe" in registry.diagnostics[0].message


def test_plugin_registry_rejects_manifest_name_with_whitespace(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    _write_plugin_manifest(plugin_root, name="my plugin")

    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    assert registry.plugins == ()
    assert len(registry.diagnostics) == 1
    assert "identifier-safe" in registry.diagnostics[0].message


def test_plugin_registry_uses_default_manifest_and_reports_invalid_component_path(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "quality"
    plugin_root.mkdir()
    manifest_dir = plugin_root / "app"
    manifest_dir.mkdir()
    (manifest_dir / "plugin.json").write_text(
        '{"name":"quality","roles":"../outside"}',
        encoding="utf-8",
    )

    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    assert len(registry.plugins) == 1
    assert registry.plugins[0].name == "quality"
    assert registry.plugins[0].role_sources == ()
    assert len(registry.diagnostics) == 1
    assert "traverse" in registry.diagnostics[0].message


def test_plugin_registry_accepts_claude_manifest_aliases(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    manifest_dir = plugin_root / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        "{"
        '"$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",'
        '"name": "quality",'
        '"agents": "./roles",'
        '"mcpServers": "./.mcp.json",'
        '"userConfig": {"api_endpoint": {"type": "string"}}'
        "}",
        encoding="utf-8",
    )
    (plugin_root / "roles").mkdir()
    (plugin_root / ".mcp.json").write_text(
        '{"mcpServers": {"docs": {"command": "docs-server"}}}',
        encoding="utf-8",
    )

    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    assert all(
        diagnostic.severity != PluginDiagnosticSeverity.ERROR
        for diagnostic in registry.diagnostics
    )
    plugin = registry.plugins[0]
    assert plugin.manifest_path == manifest_dir / "plugin.json"
    assert plugin.role_sources[0].path == plugin_root / "roles"
    assert plugin.mcp_sources[0].path == plugin_root / ".mcp.json"
    assert "api_endpoint" in plugin.manifest.user_config


def test_plugin_registry_rejects_unknown_manifest_fields(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    manifest_dir = plugin_root / "app"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        '{"name":"quality","unknownField":true}',
        encoding="utf-8",
    )

    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    assert registry.plugins == ()
    assert len(registry.diagnostics) == 1
    assert "Extra inputs are not permitted" in registry.diagnostics[0].message


def test_plugin_registry_loads_inline_hook_mcp_monitor_and_settings_configs(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "quality"
    manifest_dir = plugin_root / "app"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        "{"
        '"name":"quality",'
        '"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"echo ${plugin_root}"}]}]},'
        '"mcpServers":{"docs":{"command":"${RELAY_TEAMS_PLUGIN_ROOT}/bin/docs"}},'
        '"monitors":{"monitors":[{"name":"watch","command":"echo ${plugin_data}"}]},'
        '"settings":{"agent":"quality:reviewer"}'
        "}",
        encoding="utf-8",
    )

    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    assert len(registry.plugins) == 1
    assert all(
        diagnostic.severity != PluginDiagnosticSeverity.ERROR
        for diagnostic in registry.diagnostics
    )
    plugin = registry.plugins[0]
    assert len(plugin.hook_sources) == 1
    assert len(plugin.mcp_sources) == 1
    assert len(plugin.monitor_sources) == 1
    assert len(plugin.settings_sources) == 1
    assert plugin.monitor_definitions[0].name == "watch"
    assert plugin.settings_sources[0].settings.agent == "quality:reviewer"

    snapshot = HookLoader(
        app_config_dir=tmp_path / "app",
        project_root=None,
        plugin_hook_sources=registry.hook_sources(),
    ).load_snapshot()
    group = snapshot.hooks[HookEventName.SESSION_START][0].group
    hook_command = str(group.hooks[0].command)
    assert hook_command.startswith("echo ")
    assert Path(hook_command.removeprefix("echo ")).resolve() == plugin_root.resolve()

    specs = load_plugin_mcp_specs(registry.mcp_sources())
    assert len(specs) == 1
    assert specs[0].name == "quality:docs"
    mcp_command = str(specs[0].server_config["command"])
    assert (
        Path(mcp_command.removesuffix("/bin/docs")).resolve() == plugin_root.resolve()
    )


def test_plugin_registry_rejects_inline_path_component_configs(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "quality"
    plugin_root.mkdir()
    diagnostics = []

    sources = _component_sources(
        manifest_value={},
        default_relative_path="./roles",
        component=PluginComponentKind.ROLES,
        plugin_name="quality",
        scope=PluginScope.LOCAL,
        root_dir=plugin_root,
        data_dir=tmp_path / "app" / "plugins" / "quality",
        manifest_path=plugin_root / "app" / "plugin.json",
        user_config={},
        diagnostics=diagnostics,
        require_directory=True,
        strict_explicit_paths=True,
    )

    assert sources == ()
    assert len(diagnostics) == 1
    assert diagnostics[0].message == (
        "Inline plugin component configs are not supported; "
        "provide a component path instead"
    )
    assert diagnostics[0].severity == PluginDiagnosticSeverity.ERROR
    assert diagnostics[0].component == PluginComponentKind.ROLES


def test_plugin_skill_is_namespaced(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    skill_dir = plugin_root / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code.\n---\n\nReview carefully.\n",
        encoding="utf-8",
    )
    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    skill_registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / "app" / "skills",
        builtin_skills_dir=None,
        plugin_sources=registry.skill_sources(),
    )

    assert "quality:review" in skill_registry.list_names()


def test_plugin_role_and_mcp_specs_are_namespaced(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    roles_dir = plugin_root / "roles"
    roles_dir.mkdir(parents=True)
    (roles_dir / "reviewer.md").write_text(
        "---\n"
        "role_id: reviewer\n"
        "name: Reviewer\n"
        "description: Reviews code\n"
        "version: '1'\n"
        "tools: []\n"
        "skills: [review]\n"
        "mcp_servers: [docs]\n"
        "mode: subagent\n"
        "---\n\nReview code.\n",
        encoding="utf-8",
    )
    (plugin_root / "mcp.json").write_text(
        '{"mcpServers": {"docs": {"command": "${RELAY_TEAMS_PLUGIN_ROOT}/bin/docs"}}}',
        encoding="utf-8",
    )
    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    role_registry = RoleLoader().load_builtin_app_and_plugins(
        builtin_roles_dir=tmp_path / "missing-builtin",
        app_roles_dir=tmp_path / "missing-app",
        plugin_sources=registry.role_sources(),
        allow_empty=True,
    )
    role = role_registry.get("quality:reviewer")
    mcp_specs = load_plugin_mcp_specs(registry.mcp_sources())

    assert role.skills == ("quality:review",)
    assert role.mcp_servers == ("quality:docs",)
    assert len(mcp_specs) == 1
    assert mcp_specs[0].name == "quality:docs"
    assert mcp_specs[0].source == McpConfigScope.PLUGIN
    assert str(plugin_root) in str(mcp_specs[0].server_config["command"])


def test_plugin_mcp_specs_support_bare_payload_disabled_and_data_vars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUALITY_MODE", "strict")
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    (plugin_root / "mcp.json").write_text(
        '{"docs": {'
        '"command": "${plugin_data}/bin/docs", '
        '"args": ["${env:QUALITY_MODE}", "${user_config.endpoint}"], '
        '"disabled": true'
        "}}",
        encoding="utf-8",
    )
    manager = PluginConfigManager(app_config_dir=tmp_path / "app")
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    manager.set_plugin_user_config(
        name="quality",
        scope=PluginScope.USER,
        user_config={"endpoint": "https://docs.test"},
    )
    registry = manager.load_registry()

    specs = load_plugin_mcp_specs(registry.mcp_sources())

    assert len(specs) == 1
    assert specs[0].name == "quality:docs"
    assert specs[0].enabled is False
    assert str(tmp_path / "app" / "plugins" / "data" / "quality") in str(
        specs[0].server_config["command"]
    )
    assert specs[0].server_config["args"] == ["strict", "https://docs.test"]


def test_empty_inline_mcp_config_does_not_fall_back_to_manifest(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "quality"
    manifest_dir = plugin_root / "app"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        '{"name": "quality", "version": "1.0.0", "mcpServers": {}}',
        encoding="utf-8",
    )
    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    sources = registry.mcp_sources()
    specs = load_plugin_mcp_specs(sources)

    assert len(sources) == 1
    assert sources[0].inline_config == {}
    assert specs == ()


def test_plugin_mcp_specs_skip_invalid_runtime_payloads(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    mcp_path = plugin_root / "mcp.json"
    plugin_root.mkdir()
    mcp_path.write_text("[1, 2, 3]", encoding="utf-8")

    specs = load_plugin_mcp_specs(
        (
            _plugin_component_source(
                plugin_name="quality",
                plugin_root=plugin_root,
                path=mcp_path,
            ),
        )
    )

    assert specs == ()


def test_invalid_plugin_mcp_config_is_reported_and_skipped(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    (plugin_root / "mcp.json").write_text("{invalid json", encoding="utf-8")

    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    assert registry.mcp_sources() == ()
    assert len(registry.diagnostics) == 1
    diagnostic = registry.diagnostics[0]
    assert diagnostic.component is not None
    assert diagnostic.component.value == "mcp_servers"
    assert "Invalid plugin MCP config" in diagnostic.message


def test_plugin_stdio_mcp_specs_do_not_persist_app_proxy_env(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    (app_config_dir / ".env").write_text(
        "HTTPS_PROXY=http://proxy.local:8080\n",
        encoding="utf-8",
    )
    spec = McpServerSpec(
        name="quality:docs",
        config={"mcpServers": {"quality:docs": {"command": "docs"}}},
        server_config={"command": "docs"},
        source=McpConfigScope.PLUGIN,
    )

    registry = McpConfigManager(app_config_dir=app_config_dir).load_registry(
        extra_specs=(spec,)
    )

    loaded = registry.get_spec("quality:docs")
    assert "env" not in loaded.server_config


def test_plugin_role_hooks_use_local_namespace(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    roles_dir = plugin_root / "roles"
    roles_dir.mkdir(parents=True)
    (roles_dir / "reviewer.md").write_text(
        "---\n"
        "role_id: reviewer\n"
        "name: Reviewer\n"
        "description: Reviews code\n"
        "version: '1'\n"
        "tools: []\n"
        "mode: subagent\n"
        "hooks:\n"
        "  Stop:\n"
        "    - role_ids: [reviewer]\n"
        "      hooks:\n"
        "        - type: agent\n"
        "          role_id: reviewer\n"
        "          prompt: Review\n"
        "---\n\nReview code.\n",
        encoding="utf-8",
    )
    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    role_registry = RoleLoader().load_builtin_app_and_plugins(
        builtin_roles_dir=tmp_path / "missing-builtin",
        app_roles_dir=tmp_path / "missing-app",
        plugin_sources=registry.role_sources(),
        allow_empty=True,
    )

    role = role_registry.get("quality:reviewer")
    group = role.hooks.hooks[HookEventName.STOP][0]
    assert group.role_ids == ("quality:reviewer",)
    assert group.hooks[0].type == HookHandlerType.AGENT
    assert group.hooks[0].role_id == "quality:reviewer"


def test_plugin_role_loader_skips_missing_and_invalid_plugin_sources(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "quality"
    invalid_roles_dir = plugin_root / "roles"
    invalid_roles_dir.mkdir(parents=True)
    (invalid_roles_dir / "bad.md").write_text(
        "---\nrole_id: bad\n---\n\nMissing fields.\n",
        encoding="utf-8",
    )

    registry = RoleLoader().load_builtin_app_and_plugins(
        builtin_roles_dir=tmp_path / "missing-builtin",
        app_roles_dir=tmp_path / "missing-app",
        plugin_sources=(
            _plugin_component_source(
                plugin_name="missing",
                plugin_root=tmp_path / "missing",
                path=tmp_path / "missing" / "roles",
            ),
            _plugin_component_source(
                plugin_name="quality",
                plugin_root=plugin_root,
                path=invalid_roles_dir,
            ),
        ),
        allow_empty=True,
    )

    assert registry.list_roles() == ()


def test_plugin_hook_source_appears_in_runtime_snapshot(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "hooks.json").write_text(
        '{"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo ok"}]}]}}',
        encoding="utf-8",
    )
    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    snapshot = HookLoader(
        app_config_dir=tmp_path / "app",
        project_root=None,
        plugin_hook_sources=registry.hook_sources(),
    ).load_snapshot()

    assert any(source.scope == HookSourceScope.PLUGIN for source in snapshot.sources)


def test_plugin_hook_config_substitutes_plugin_vars(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "hooks.json").write_text(
        '{"hooks": {"SessionStart": [{"hooks": [{"type": "command", '
        '"command": "echo ${user_config.message} ${plugin_root}"}]}]}}',
        encoding="utf-8",
    )
    manager = PluginConfigManager(app_config_dir=tmp_path / "app")
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    manager.set_plugin_user_config(
        name="quality",
        scope=PluginScope.USER,
        user_config={"message": "hello"},
    )
    registry = manager.load_registry()

    snapshot = HookLoader(
        app_config_dir=tmp_path / "app",
        project_root=None,
        plugin_hook_sources=registry.hook_sources(),
    ).load_snapshot()

    group = snapshot.hooks[HookEventName.SESSION_START][0].group
    assert group.hooks[0].type == HookHandlerType.COMMAND
    assert group.hooks[0].command == f"echo hello {registry.plugins[0].root_dir}"


def test_plugin_hook_reload_keeps_existing_run_snapshot(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    first_plugin = tmp_path / "first"
    second_plugin = tmp_path / "second"
    _write_plugin_manifest(first_plugin, name="first")
    _write_plugin_manifest(second_plugin, name="second")
    _write_plugin_hook(first_plugin, command="echo first")
    _write_plugin_hook(second_plugin, command="echo second")
    first_registry = PluginConfigManager(
        app_config_dir=app_config_dir,
        plugin_dirs=(first_plugin,),
    ).load_registry()
    second_registry = PluginConfigManager(
        app_config_dir=app_config_dir,
        plugin_dirs=(second_plugin,),
    ).load_registry()
    runtime_state = HookRuntimeState()
    hook_service = HookService(
        loader=HookLoader(
            app_config_dir=app_config_dir,
            plugin_hook_sources=first_registry.hook_sources(),
        ),
        runtime_state=runtime_state,
        command_executor=cast(CommandHookExecutor, object()),
        http_executor=cast(HttpHookExecutor, object()),
    )

    original_snapshot = hook_service.snapshot_run("run-1")
    hook_service.replace_loader(
        HookLoader(
            app_config_dir=app_config_dir,
            plugin_hook_sources=second_registry.hook_sources(),
        )
    )

    current_group = (
        hook_service.get_effective_config().hooks[HookEventName.SESSION_START][0].group
    )
    stored_snapshot = runtime_state.get_snapshot("run-1")
    assert stored_snapshot is not None
    stored_group = stored_snapshot.hooks[HookEventName.SESSION_START][0].group
    assert (
        original_snapshot.hooks[HookEventName.SESSION_START][0].group.hooks[0].command
        == "echo first"
    )
    assert stored_group.hooks[0].command == "echo first"
    assert current_group.hooks[0].command == "echo second"


def test_plugin_settings_are_loaded_with_substitutions(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    (plugin_root / "settings.json").write_text(
        '{"agent": "${user_config.message}"}',
        encoding="utf-8",
    )
    manager = PluginConfigManager(app_config_dir=tmp_path / "app")
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    manager.set_plugin_user_config(
        name="quality",
        scope=PluginScope.USER,
        user_config={"message": "quality:reviewer"},
    )
    registry = manager.load_registry()

    assert all(
        diagnostic.severity != PluginDiagnosticSeverity.ERROR
        for diagnostic in registry.diagnostics
    )
    assert len(registry.settings_sources()) == 1
    assert registry.settings_sources()[0].settings.agent == "quality:reviewer"


def test_plugin_settings_do_not_substitute_environment_variables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUALITY_AGENT", "quality:reviewer")
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    (plugin_root / "settings.json").write_text(
        '{"agent": "${env:QUALITY_AGENT}"}',
        encoding="utf-8",
    )

    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    assert len(registry.settings_sources()) == 1
    assert registry.settings_sources()[0].settings.agent == ""


def test_plugin_settings_ignore_unknown_fields_at_runtime(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    (plugin_root / "settings.json").write_text(
        '{"agent": "quality:reviewer", "unknown": true}',
        encoding="utf-8",
    )

    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    assert len(registry.plugins) == 1
    assert registry.settings_sources()[0].settings.agent == "quality:reviewer"
    assert len(registry.diagnostics) == 1
    assert registry.diagnostics[0].severity.value == "warning"
    assert "Unknown plugin settings field(s): unknown" in (
        registry.diagnostics[0].message
    )


def test_plugin_command_is_namespaced(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    commands_dir = plugin_root / "commands"
    commands_dir.mkdir(parents=True)
    (commands_dir / "review.md").write_text(
        "---\ndescription: Review code\n---\n\nReview $ARGUMENTS\n",
        encoding="utf-8",
    )
    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    command_registry = CommandRegistry(
        app_config_dir=tmp_path / "app",
        plugin_sources=registry.command_sources(),
    )

    command = command_registry.get_command("quality:review", workspace_root=None)
    assert command is not None
    assert command.name == "quality:review"


def test_plugin_command_frontmatter_name_is_namespaced(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    commands_dir = plugin_root / "commands"
    commands_dir.mkdir(parents=True)
    (commands_dir / "review.md").write_text(
        "---\nname: review\ndescription: Review code\n---\n\nReview $ARGUMENTS\n",
        encoding="utf-8",
    )
    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    command_registry = CommandRegistry(
        app_config_dir=tmp_path / "app",
        plugin_sources=registry.command_sources(),
    )

    assert command_registry.get_command("review", workspace_root=None) is None
    command = command_registry.get_command("quality:review", workspace_root=None)
    assert command is not None
    assert command.name == "quality:review"


def test_plugin_command_aliases_are_namespaced(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    commands_dir = plugin_root / "commands"
    commands_dir.mkdir(parents=True)
    (commands_dir / "review.md").write_text(
        "---\ndescription: Review code\naliases: [review, /inspect]\n---\n\nReview $ARGUMENTS\n",
        encoding="utf-8",
    )
    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()

    command_registry = CommandRegistry(
        app_config_dir=tmp_path / "app",
        plugin_sources=registry.command_sources(),
    )

    command = command_registry.get_command("quality:review", workspace_root=None)
    assert command is not None
    assert command.aliases == ("quality:review", "quality:inspect")
    assert command_registry.get_command("inspect", workspace_root=None) is None
    assert (
        command_registry.get_command("quality:inspect", workspace_root=None) == command
    )


def test_plugin_hook_agent_role_uses_local_namespace(tmp_path: Path) -> None:
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality")
    roles_dir = plugin_root / "roles"
    roles_dir.mkdir(parents=True)
    (roles_dir / "reviewer.md").write_text(
        "---\n"
        "role_id: reviewer\n"
        "name: Reviewer\n"
        "description: Reviews code\n"
        "version: '1'\n"
        "tools: []\n"
        "mode: subagent\n"
        "---\n\nReview code.\n",
        encoding="utf-8",
    )
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        '{"hooks": {"Stop": [{"role_ids": ["reviewer"], '
        '"hooks": [{"type": "agent", "role_id": "reviewer", "prompt": "Review"}]}]}}',
        encoding="utf-8",
    )
    registry = PluginConfigManager(
        app_config_dir=tmp_path / "app",
        plugin_dirs=(plugin_root,),
    ).load_registry()
    role_registry = RoleLoader().load_builtin_app_and_plugins(
        builtin_roles_dir=tmp_path / "missing-builtin",
        app_roles_dir=tmp_path / "missing-app",
        plugin_sources=registry.role_sources(),
        allow_empty=True,
    )

    snapshot = HookLoader(
        app_config_dir=tmp_path / "app",
        project_root=None,
        get_role_registry=lambda: role_registry,
        plugin_hook_sources=registry.hook_sources(),
    ).load_snapshot()

    group = snapshot.hooks[HookEventName.STOP][0].group
    assert group.role_ids == ("quality:reviewer",)
    assert group.hooks[0].type == HookHandlerType.AGENT
    assert group.hooks[0].role_id == "quality:reviewer"


@pytest.mark.asyncio
async def test_command_hook_executor_receives_plugin_environment(
    tmp_path: Path,
) -> None:
    script_path = tmp_path / "env_hook.py"
    script_path.write_text(
        "from __future__ import annotations\n"
        "import json\n"
        "import os\n"
        "print(json.dumps({"
        "'decision': 'allow', "
        "'reason': os.environ['RELAY_TEAMS_PLUGIN_ROOT']"
        "}))\n",
        encoding="utf-8",
    )
    plugin_root = tmp_path / "quality"
    plugin_root.mkdir()

    decision = await CommandHookExecutor().execute(
        handler=HookHandlerConfig(
            type=HookHandlerType.COMMAND,
            command=f'"{sys.executable}" "{script_path}"',
        ),
        event_input=SessionStartInput(
            event_name=HookEventName.SESSION_START,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
        ),
        extra_env={"RELAY_TEAMS_PLUGIN_ROOT": str(plugin_root)},
    )

    assert decision.decision == HookDecisionType.ALLOW
    assert decision.reason == str(plugin_root)


def _write_plugin_manifest(
    plugin_root: Path,
    *,
    name: str,
    user_config: dict[str, object] | None = None,
) -> None:
    manifest_dir = plugin_root / "app"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "userConfig": user_config
                if user_config is not None
                else {"endpoint": {}, "message": {}},
            }
        ),
        encoding="utf-8",
    )


def _write_plugin_hook(plugin_root: Path, *, command: str) -> None:
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "hooks.json").write_text(
        "{"
        '"hooks": {'
        '"SessionStart": [{"hooks": [{"type": "command", '
        f'"command": "{command}"'
        "}]}]"
        "}"
        "}",
        encoding="utf-8",
    )


def _plugin_component_source(
    *,
    plugin_name: str,
    plugin_root: Path,
    path: Path,
) -> PluginComponentSource:
    return PluginComponentSource(
        plugin_name=plugin_name,
        root_dir=plugin_root,
        data_dir=plugin_root / "data",
        path=path,
    )
