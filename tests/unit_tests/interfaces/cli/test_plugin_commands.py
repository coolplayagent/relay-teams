# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from relay_teams.interfaces.cli import app as cli_app
from relay_teams.plugins.marketplace_models import PluginMarketplaceProviderKind
from relay_teams.plugins.marketplace_policy import PluginMarketplaceInstallPolicy
from relay_teams.plugins.plugin_models import PluginScope

runner = CliRunner()


def test_plugin_install_and_list_json(monkeypatch, tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, config_dir_name="app", name="quality")
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(app_config_dir))

    install_result = runner.invoke(cli_app.app, ["plugin", "install", str(plugin_root)])
    assert install_result.exit_code == 0, install_result.output

    list_result = runner.invoke(
        cli_app.app,
        ["plugin", "list", "--format", "json"],
    )
    assert list_result.exit_code == 0, list_result.output
    payload = json.loads(list_result.output)

    assert payload["plugins"][0]["name"] == "quality"
    assert payload["plugins"][0]["scope"] == "user"
    assert payload["plugins"][0]["enabled"] is True
    assert payload["diagnostics"] == []


def test_plugin_validate_reports_invalid_manifest(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "bad"
    _write_plugin_manifest(plugin_root, config_dir_name="app", name="../bad")
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(app_config_dir))

    result = runner.invoke(
        cli_app.app,
        ["plugin", "validate", str(plugin_root), "--format", "json"],
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)

    assert payload["valid"] is False
    assert "Plugin name must be identifier-safe" in payload["diagnostics"][0]["message"]


def test_plugin_install_project_scope_uses_project_config_dir_name(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "relay-config"
    project_root = tmp_path / "repo"
    plugin_root = tmp_path / "quality"
    project_root.mkdir()
    _write_plugin_manifest(plugin_root, config_dir_name="relay-config", name="quality")
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(app_config_dir))
    monkeypatch.setattr(
        "relay_teams.plugins.plugin_cli.get_project_root_or_none",
        lambda start_dir=None: project_root.resolve(),
    )

    result = runner.invoke(
        cli_app.app,
        ["plugin", "install", str(plugin_root), "--scope", "project"],
    )

    assert result.exit_code == 0, result.output
    state_path = project_root / "relay-config" / "plugins.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["plugins"][0]["name"] == "quality"
    assert payload["plugins"][0]["scope"] == "project"


def test_plugin_marketplace_list_install_and_update(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    first_root = tmp_path / "quality-v1"
    second_root = tmp_path / "quality-v2"
    marketplace_path = tmp_path / "marketplace.json"
    _write_plugin_manifest(first_root, config_dir_name="app", name="quality")
    _write_plugin_manifest(
        second_root,
        config_dir_name="app",
        name="quality",
        version="2.0.0",
    )
    _write_marketplace(
        marketplace_path=marketplace_path,
        first_root=first_root,
        second_root=second_root,
    )
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(app_config_dir))

    list_result = runner.invoke(
        cli_app.app,
        [
            "plugin",
            "list",
            "--available",
            "--marketplace",
            str(marketplace_path),
            "--format",
            "json",
        ],
    )
    assert list_result.exit_code == 0, list_result.output
    available = json.loads(list_result.output)
    assert available[0]["name"] == "quality"
    assert available[0]["latest"] == "2.0.0"

    install_result = runner.invoke(
        cli_app.app,
        [
            "plugin",
            "install",
            "quality",
            "--marketplace",
            str(marketplace_path),
            "--version",
            "1.0.0",
        ],
    )
    assert install_result.exit_code == 0, install_result.output

    update_result = runner.invoke(
        cli_app.app,
        ["plugin", "update", "quality", "--version", "2.0.0"],
    )
    assert update_result.exit_code == 0, update_result.output
    payload = json.loads((app_config_dir / "plugins" / "plugins.json").read_text())
    assert payload["plugins"][0]["version"] == "2.0.0"


def test_plugin_marketplace_list_without_latest_uses_highest_version(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    first_root = tmp_path / "quality-v1"
    second_root = tmp_path / "quality-v2"
    marketplace_path = tmp_path / "marketplace.json"
    _write_plugin_manifest(first_root, config_dir_name="app", name="quality")
    _write_plugin_manifest(
        second_root,
        config_dir_name="app",
        name="quality",
        version="2.0.0",
    )
    marketplace_path.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "quality",
                        "description": "Quality tools",
                        "versions": [
                            {
                                "version": "2.0.0",
                                "source": {
                                    "kind": "local",
                                    "value": str(second_root.resolve()),
                                },
                            },
                            {
                                "version": "1.0.0",
                                "source": {
                                    "kind": "local",
                                    "value": str(first_root.resolve()),
                                },
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(app_config_dir))

    list_result = runner.invoke(
        cli_app.app,
        [
            "plugin",
            "list",
            "--available",
            "--marketplace",
            str(marketplace_path),
            "--format",
            "json",
        ],
    )

    assert list_result.exit_code == 0, list_result.output
    available = json.loads(list_result.output)
    assert available[0]["name"] == "quality"
    assert available[0]["latest"] == "2.0.0"


def test_plugin_configure_updates_user_config(monkeypatch, tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(
        plugin_root,
        config_dir_name="app",
        name="quality",
        user_config={"endpoint": {}, "retries": {"type": "integer"}},
    )
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(app_config_dir))

    install_result = runner.invoke(cli_app.app, ["plugin", "install", str(plugin_root)])
    assert install_result.exit_code == 0, install_result.output

    configure_result = runner.invoke(
        cli_app.app,
        [
            "plugin",
            "configure",
            "quality",
            "--set",
            "endpoint=https://docs.test",
            "--set",
            "retries=2",
        ],
    )
    assert configure_result.exit_code == 0, configure_result.output

    list_result = runner.invoke(
        cli_app.app,
        ["plugin", "list", "--format", "json"],
    )
    assert list_result.exit_code == 0, list_result.output
    payload = json.loads(list_result.output)
    assert payload["plugins"][0]["user_config"] == {
        "endpoint": "https://docs.test",
        "retries": 2,
    }


def test_plugin_lifecycle_table_commands_and_prune(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    first_root = tmp_path / "quality-v1"
    second_root = tmp_path / "quality-v2"
    marketplace_path = tmp_path / "marketplace.json"
    _write_plugin_manifest(first_root, config_dir_name="app", name="quality")
    _write_plugin_manifest(
        second_root,
        config_dir_name="app",
        name="quality",
        version="2.0.0",
    )
    _write_marketplace(
        marketplace_path=marketplace_path,
        first_root=first_root,
        second_root=second_root,
    )
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(app_config_dir))

    empty_list = runner.invoke(cli_app.app, ["plugin", "list"])
    assert empty_list.exit_code == 0, empty_list.output
    assert "No plugins installed or configured." in empty_list.output

    install_result = runner.invoke(
        cli_app.app,
        [
            "plugin",
            "install",
            "quality",
            "--marketplace",
            str(marketplace_path),
            "--version",
            "1.0.0",
            "--disabled",
        ],
    )
    assert install_result.exit_code == 0, install_result.output
    assert "(disabled)" in install_result.output

    table_list = runner.invoke(cli_app.app, ["plugin", "list"])
    assert table_list.exit_code == 0, table_list.output
    assert "Plugins (1 total)" in table_list.output
    assert "quality" in table_list.output

    enable_result = runner.invoke(cli_app.app, ["plugin", "enable", "quality"])
    assert enable_result.exit_code == 0, enable_result.output
    assert "Enabled plugin quality" in enable_result.output

    disable_result = runner.invoke(cli_app.app, ["plugin", "disable", "quality"])
    assert disable_result.exit_code == 0, disable_result.output
    assert "Disabled plugin quality" in disable_result.output

    update_result = runner.invoke(
        cli_app.app,
        ["plugin", "update", "quality", "--version", "2.0.0"],
    )
    assert update_result.exit_code == 0, update_result.output

    prune_result = runner.invoke(cli_app.app, ["plugin", "prune"])
    assert prune_result.exit_code == 0, prune_result.output
    assert "Pruned 1 installed plugin version(s)." in prune_result.output

    uninstall_result = runner.invoke(
        cli_app.app,
        ["plugin", "uninstall", "quality", "--prune"],
    )
    assert uninstall_result.exit_code == 0, uninstall_result.output
    assert "and pruned installed copies" in uninstall_result.output

    empty_prune = runner.invoke(cli_app.app, ["plugin", "prune"])
    assert empty_prune.exit_code == 0, empty_prune.output
    assert "No installed plugin versions pruned." in empty_prune.output


def test_plugin_validate_text_modes(monkeypatch, tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    valid_root = tmp_path / "quality"
    invalid_root = tmp_path / "bad"
    _write_plugin_manifest(valid_root, config_dir_name="app", name="quality")
    _write_plugin_manifest(invalid_root, config_dir_name="app", name="../bad")
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(app_config_dir))

    valid_result = runner.invoke(cli_app.app, ["plugin", "validate", str(valid_root)])
    assert valid_result.exit_code == 0, valid_result.output
    assert "Plugin is valid: quality (1.0.0)" in valid_result.output

    invalid_result = runner.invoke(
        cli_app.app,
        ["plugin", "validate", str(invalid_root)],
    )
    assert invalid_result.exit_code == 1, invalid_result.output
    assert "Plugin is invalid." in invalid_result.output
    assert "Plugin name must be identifier-safe" in invalid_result.output


def test_plugin_marketplace_table_and_missing_marketplace(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    first_root = tmp_path / "quality-v1"
    second_root = tmp_path / "quality-v2"
    marketplace_path = tmp_path / "marketplace.json"
    _write_plugin_manifest(first_root, config_dir_name="app", name="quality")
    _write_plugin_manifest(
        second_root,
        config_dir_name="app",
        name="quality",
        version="2.0.0",
    )
    _write_marketplace(
        marketplace_path=marketplace_path,
        first_root=first_root,
        second_root=second_root,
    )
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(app_config_dir))

    missing = runner.invoke(cli_app.app, ["plugin", "list", "--available"])
    assert missing.exit_code == 2, missing.output

    table_result = runner.invoke(
        cli_app.app,
        ["plugin", "list", "--available", "--marketplace", str(marketplace_path)],
    )
    assert table_result.exit_code == 0, table_result.output
    assert "Available plugins (1 total)" in table_result.output
    assert "Quality tools" in table_result.output

    empty_marketplace = tmp_path / "empty-marketplace.json"
    empty_marketplace.write_text('{"plugins": []}', encoding="utf-8")
    empty_result = runner.invoke(
        cli_app.app,
        ["plugin", "list", "--available", "--marketplace", str(empty_marketplace)],
    )
    assert empty_result.exit_code == 0, empty_result.output
    assert "No marketplace plugins available." in empty_result.output


def test_plugin_marketplace_search_json_and_table(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    first_root = tmp_path / "quality-v1"
    second_root = tmp_path / "quality-v2"
    marketplace_path = tmp_path / "marketplace.json"
    _write_plugin_manifest(first_root, config_dir_name="app", name="quality")
    _write_plugin_manifest(
        second_root,
        config_dir_name="app",
        name="quality",
        version="2.0.0",
    )
    _write_marketplace(
        marketplace_path=marketplace_path,
        first_root=first_root,
        second_root=second_root,
    )
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(app_config_dir))

    json_result = runner.invoke(
        cli_app.app,
        [
            "plugin",
            "search",
            "quality",
            "--marketplace",
            str(marketplace_path),
            "--marketplace-provider",
            "local_json",
            "--format",
            "json",
        ],
    )
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload[0]["name"] == "quality"
    assert payload[0]["latest"] == "2.0.0"

    table_result = runner.invoke(
        cli_app.app,
        [
            "plugin",
            "search",
            "missing",
            "--marketplace",
            str(marketplace_path),
            "--marketplace-provider",
            "local_json",
        ],
    )
    assert table_result.exit_code == 0, table_result.output
    assert "No marketplace plugins available." in table_result.output


def test_plugin_install_clawhub_override_flags(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured_policy: dict[str, PluginMarketplaceInstallPolicy] = {}

    def fake_install_marketplace_plugin(
        self,
        *,
        name: str,
        marketplace: Path,
        scope: PluginScope,
        version: str | None = None,
        enabled: bool = True,
        marketplace_provider: object = "local_json",
        marketplace_source: str = "",
        marketplace_ref: str = "",
        install_policy: PluginMarketplaceInstallPolicy | None = None,
    ) -> SimpleNamespace:
        _ = self
        _ = name
        _ = marketplace
        _ = version
        _ = marketplace_provider
        _ = marketplace_source
        _ = marketplace_ref
        assert install_policy is not None
        captured_policy["value"] = install_policy
        return SimpleNamespace(name="quality", scope=scope, enabled=enabled)

    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(tmp_path / "app"))
    monkeypatch.setattr(
        "relay_teams.plugins.config_manager.PluginConfigManager.install_marketplace_plugin",
        fake_install_marketplace_plugin,
    )

    result = runner.invoke(
        cli_app.app,
        [
            "plugin",
            "install",
            "clawhub:quality",
            "--allow-community-plugins",
            "--allow-executes-code",
            "--allow-missing-digest",
            "--allow-unclean-scan",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured_policy["value"] == PluginMarketplaceInstallPolicy(
        allow_community_plugins=True,
        allow_executes_code=True,
        require_digest=False,
        allow_unclean_scan=True,
    )


def test_plugin_install_clawhub_shorthand_strips_prefix_with_explicit_marketplace(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_install_marketplace_plugin(
        self,
        *,
        name: str,
        marketplace: Path,
        scope: PluginScope,
        version: str | None = None,
        enabled: bool = True,
        marketplace_provider: object = "local_json",
        marketplace_source: str = "",
        marketplace_ref: str = "",
        install_policy: PluginMarketplaceInstallPolicy | None = None,
    ) -> SimpleNamespace:
        _ = self
        _ = version
        _ = marketplace_ref
        _ = install_policy
        captured["name"] = name
        captured["marketplace"] = marketplace
        captured["scope"] = scope
        captured["marketplace_provider"] = marketplace_provider
        captured["marketplace_source"] = marketplace_source
        return SimpleNamespace(name="quality", scope=scope, enabled=enabled)

    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(tmp_path / "app"))
    monkeypatch.setattr(
        "relay_teams.plugins.config_manager.PluginConfigManager.install_marketplace_plugin",
        fake_install_marketplace_plugin,
    )

    result = runner.invoke(
        cli_app.app,
        [
            "plugin",
            "install",
            "clawhub:quality",
            "--marketplace",
            "clawhub",
            "--marketplace-provider",
            "clawhub",
            "--marketplace-source",
            "https://clawhub.test",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {
        "name": "quality",
        "marketplace": Path("clawhub"),
        "scope": PluginScope.USER,
        "marketplace_provider": PluginMarketplaceProviderKind.CLAWHUB,
        "marketplace_source": "https://clawhub.test",
    }


def test_plugin_configure_reports_invalid_values(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(
        plugin_root,
        config_dir_name="app",
        name="quality",
        user_config={"endpoint": {"type": "string"}},
    )
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(app_config_dir))

    install_result = runner.invoke(cli_app.app, ["plugin", "install", str(plugin_root)])
    assert install_result.exit_code == 0, install_result.output

    malformed = runner.invoke(
        cli_app.app,
        ["plugin", "configure", "quality", "--set", "endpoint"],
    )
    assert malformed.exit_code == 2, malformed.output
    assert "Plugin config values must use key=value" in malformed.output

    unknown = runner.invoke(
        cli_app.app,
        ["plugin", "configure", "missing", "--set", "endpoint=https://docs.test"],
    )
    assert unknown.exit_code == 2, unknown.output
    assert "Plugin is not installed in user: missing" in unknown.output


def _write_plugin_manifest(
    plugin_root: Path,
    *,
    config_dir_name: str,
    name: str,
    version: str = "1.0.0",
    user_config: dict[str, object] | None = None,
) -> None:
    manifest_dir = plugin_root / config_dir_name
    manifest_dir.mkdir(parents=True)
    payload: dict[str, object] = {"name": name, "version": version}
    if user_config is not None:
        payload["userConfig"] = user_config
    (manifest_dir / "plugin.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _write_marketplace(
    *,
    marketplace_path: Path,
    first_root: Path,
    second_root: Path,
) -> None:
    marketplace_path.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "quality",
                        "description": "Quality tools",
                        "latest": "2.0.0",
                        "versions": [
                            {
                                "version": "1.0.0",
                                "source": {
                                    "kind": "local",
                                    "value": str(first_root.resolve()),
                                },
                            },
                            {
                                "version": "2.0.0",
                                "source": {
                                    "kind": "local",
                                    "value": str(second_root.resolve()),
                                },
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
