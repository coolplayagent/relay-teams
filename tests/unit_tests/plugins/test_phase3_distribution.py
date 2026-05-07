# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from relay_teams.plugins import installers as plugin_installers
from relay_teams.plugins import config_manager as plugin_config_manager
from relay_teams.plugins.config_manager import PluginConfigManager
from relay_teams.plugins.integrity import compute_plugin_tree_sha256
from relay_teams.plugins.marketplace_service import PluginMarketplaceService
from relay_teams.plugins.plugin_models import (
    PluginInstallSource,
    PluginInstallSourceKind,
    PluginScope,
)
from relay_teams.plugins.state_paths import (
    get_plugin_project_state_file,
    get_plugin_user_state_file,
)
from relay_teams.plugins.user_config_secrets import PluginUserConfigSecretStore
from relay_teams.secrets import AppSecretStore


class _FileOnlySecretStore(AppSecretStore):
    def has_usable_keyring_backend(self) -> bool:
        return False


class _FailingSetSecretStore(_FileOnlySecretStore):
    fail_sets: bool = False

    def set_secret(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
        field_name: str,
        value: str | None,
    ) -> None:
        if self.fail_sets:
            raise RuntimeError("secret write failed")
        super().set_secret(
            config_dir,
            namespace=namespace,
            owner_id=owner_id,
            field_name=field_name,
            value=value,
        )


def test_update_plugin_installs_new_version_and_prune_removes_old_copy(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    installed = manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    _write_plugin_manifest(plugin_root, name="quality", version="2.0.0")

    updated = manager.update_plugin(name="quality", scope=PluginScope.USER)

    assert updated.version == "2.0.0"
    assert updated.root_dir != installed.root_dir
    assert updated.root_dir.exists()
    assert installed.root_dir.exists()

    removed = manager.prune_installed_plugins()

    assert installed.root_dir in removed
    assert not installed.root_dir.exists()
    assert updated.root_dir.exists()


def test_prune_installed_plugins_aborts_on_invalid_state_file(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    installed = manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    get_plugin_user_state_file(app_config_dir=app_config_dir).write_text(
        "{",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        manager.prune_installed_plugins()

    assert installed.root_dir.exists()


def test_update_plugin_rejects_version_for_local_plugins(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)

    with pytest.raises(
        ValueError,
        match="Versioned plugin updates are only supported for marketplace plugins",
    ):
        manager.update_plugin(name="quality", scope=PluginScope.USER, version="2.0.0")


def test_copy_local_plugin_source_rejects_missing_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Plugin source directory does not exist"):
        plugin_installers.copy_local_plugin_source(
            source_dir=tmp_path / "missing",
            target_dir=tmp_path / "target",
        )


def test_copy_local_plugin_source_rejects_existing_target(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()

    with pytest.raises(ValueError, match="Installed plugin target already exists"):
        plugin_installers.copy_local_plugin_source(
            source_dir=source_dir,
            target_dir=target_dir,
        )


def test_copy_local_plugin_source_rejects_symlinked_content(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    outside_file = tmp_path / "outside.txt"
    source_dir.mkdir()
    outside_file.write_text("secret", encoding="utf-8")
    try:
        (source_dir / "linked.txt").symlink_to(outside_file)
    except OSError as exc:
        pytest.skip(f"symlink creation is not available: {exc}")

    with pytest.raises(ValueError, match="unsupported symlink"):
        plugin_installers.copy_local_plugin_source(
            source_dir=source_dir,
            target_dir=target_dir,
        )

    assert not target_dir.exists()


def test_install_git_plugin_source_reports_clone_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = PluginInstallSource(
        kind=PluginInstallSourceKind.GIT,
        value="https://example.test/plugin.git",
    )

    def fail_git(args: list[str]) -> None:
        raise subprocess.CalledProcessError(1, args, stderr="denied")

    monkeypatch.setattr(plugin_installers, "_run_git", fail_git)

    with pytest.raises(ValueError, match="Failed to clone plugin git source: denied"):
        plugin_installers.install_git_plugin_source(
            source=source,
            app_config_dir=tmp_path / "app",
            target_dir=tmp_path / "target",
        )


def test_install_git_plugin_source_fetches_unavailable_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = PluginInstallSource(
        kind=PluginInstallSourceKind.GIT,
        value="https://example.test/plugin.git",
        ref="feature",
    )
    calls: list[list[str]] = []

    def fake_git(args: list[str]) -> None:
        calls.append(args)
        if args[1] == "clone":
            Path(args[-1]).mkdir(parents=True)
            return
        if "checkout" in args and args[-1] == "feature":
            raise subprocess.CalledProcessError(1, args, stderr="missing ref")

    monkeypatch.setattr(plugin_installers, "_run_git", fake_git)

    plugin_installers.install_git_plugin_source(
        source=source,
        app_config_dir=tmp_path / "app",
        target_dir=tmp_path / "target",
    )

    assert any("fetch" in call for call in calls)
    assert (tmp_path / "target").exists()


def test_update_plugin_persists_new_sensitive_defaults_in_secret_store(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        user_config_secret_store=PluginUserConfigSecretStore(
            secret_store=_FileOnlySecretStore()
        ),
    )
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        version="2.0.0",
        user_config={
            "token": {
                "type": "string",
                "default": "default-secret",
                "sensitive": True,
            }
        },
    )

    updated = manager.update_plugin(name="quality", scope=PluginScope.USER)

    state_path = app_config_dir / "plugins" / "plugins.json"
    state_text = state_path.read_text(encoding="utf-8")
    assert "default-secret" not in state_text
    assert updated.user_config == {}
    registry = manager.load_registry()
    assert registry.plugins[0].user_config["token"] == "<configured>"


def test_update_plugin_deletes_removed_sensitive_fields_from_secret_store(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    project_root = tmp_path / "project"
    project_root.mkdir()
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        version="1.0.0",
        user_config={
            "token": {
                "type": "string",
                "required": True,
                "sensitive": True,
            }
        },
    )
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        project_root=project_root,
        user_config_secret_store=PluginUserConfigSecretStore(
            secret_store=_FileOnlySecretStore()
        ),
    )
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    manager.set_plugin_user_config(
        name="quality",
        scope=PluginScope.USER,
        user_config={"token": "old-secret"},
    )
    _write_plugin_manifest(plugin_root, name="quality", version="2.0.0")
    manager.update_plugin(name="quality", scope=PluginScope.USER)
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        version="3.0.0",
        user_config={
            "token": {
                "type": "string",
                "required": True,
                "sensitive": True,
            }
        },
    )

    manager.update_plugin(name="quality", scope=PluginScope.USER)
    registry = manager.load_registry()
    secrets_file = app_config_dir / "secrets.json"

    assert registry.plugins[0].enabled is False
    assert not secrets_file.exists() or "old-secret" not in secrets_file.read_text(
        encoding="utf-8"
    )
    assert any(
        "Missing required plugin user_config field(s): token" in diagnostic.message
        for diagnostic in registry.diagnostics
    )


def test_update_plugin_preserves_existing_secret_when_rewrite_fails(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        version="1.0.0",
        user_config={
            "token": {
                "type": "string",
                "required": True,
                "sensitive": True,
            }
        },
    )
    secret_store = _FailingSetSecretStore()
    user_config_secret_store = PluginUserConfigSecretStore(secret_store=secret_store)
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        user_config_secret_store=user_config_secret_store,
    )
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    manager.set_plugin_user_config(
        name="quality",
        scope=PluginScope.USER,
        user_config={"token": "old-secret"},
    )
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        version="2.0.0",
        user_config={
            "token": {
                "type": "string",
                "required": True,
                "sensitive": True,
            }
        },
    )
    secret_store.fail_sets = True

    with pytest.raises(RuntimeError, match="secret write failed"):
        manager.update_plugin(name="quality", scope=PluginScope.USER)

    assert (
        user_config_secret_store.get_field(
            app_config_dir,
            plugin_name="quality",
            scope=PluginScope.USER,
            field_name="token",
        )
        == "old-secret"
    )


def test_update_plugin_preserves_sensitive_value_when_field_becomes_non_sensitive(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    project_root = tmp_path / "project"
    project_root.mkdir()
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        version="1.0.0",
        user_config={
            "token": {
                "type": "string",
                "required": True,
                "sensitive": True,
            }
        },
    )
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        project_root=project_root,
        user_config_secret_store=PluginUserConfigSecretStore(
            secret_store=_FileOnlySecretStore()
        ),
    )
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    manager.set_plugin_user_config(
        name="quality",
        scope=PluginScope.USER,
        user_config={"token": "kept-secret"},
    )
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        version="2.0.0",
        user_config={
            "token": {
                "type": "string",
                "required": True,
                "sensitive": False,
            }
        },
    )

    updated = manager.update_plugin(name="quality", scope=PluginScope.USER)
    registry = manager.load_registry()
    state_text = (app_config_dir / "plugins" / "plugins.json").read_text(
        encoding="utf-8"
    )

    assert updated.user_config == {"token": "kept-secret"}
    assert registry.plugins[0].user_config["token"] == "kept-secret"
    assert "kept-secret" in state_text


def test_install_rejects_duplicate_plugin_names_across_scopes(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    project_root = tmp_path / "project"
    project_root.mkdir()
    first_root = tmp_path / "quality-user"
    second_root = tmp_path / "quality-project"
    _write_plugin_manifest(first_root, name="quality", version="1.0.0")
    _write_plugin_manifest(second_root, name="quality", version="1.0.0")
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        project_root=project_root,
    )
    manager.install_plugin(source=first_root, scope=PluginScope.USER)

    with pytest.raises(ValueError, match="Plugin already installed in user: quality"):
        manager.install_plugin(source=second_root, scope=PluginScope.PROJECT)


def test_install_aborts_when_cross_scope_state_file_is_invalid(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    project_root = tmp_path / "project"
    project_root.mkdir()
    first_root = tmp_path / "quality-project"
    second_root = tmp_path / "quality-user"
    _write_plugin_manifest(first_root, name="quality", version="1.0.0")
    _write_plugin_manifest(second_root, name="quality", version="1.0.0")
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        project_root=project_root,
    )
    manager.install_plugin(source=first_root, scope=PluginScope.PROJECT)
    project_state_file = get_plugin_project_state_file(
        app_config_dir=app_config_dir,
        project_root=project_root,
    )
    assert project_state_file is not None
    project_state_file.write_text("{", encoding="utf-8")

    with pytest.raises(ValueError):
        manager.install_plugin(source=second_root, scope=PluginScope.USER)

    user_state_file = get_plugin_user_state_file(app_config_dir=app_config_dir)
    assert not user_state_file.exists()


def test_install_resolves_dependencies_against_local_plugin_dirs(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    local_base = tmp_path / "base"
    dependent_root = tmp_path / "dependent"
    _write_plugin_manifest(local_base, name="base", version="1.0.0")
    _write_plugin_manifest(
        dependent_root,
        name="dependent",
        version="1.0.0",
        dependencies=[{"name": "base", "version": "1.0.0"}],
    )
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        plugin_dirs=(local_base,),
    )

    installed = manager.install_plugin(
        source=dependent_root,
        scope=PluginScope.USER,
    )
    registry = manager.load_registry()
    by_name = {plugin.name: plugin for plugin in registry.plugins}

    assert installed.name == "dependent"
    assert by_name["dependent"].enabled is True
    assert by_name["base"].scope == PluginScope.LOCAL


def test_reinstall_reuses_existing_unreferenced_installed_copy(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    first = manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    manager.uninstall_plugin(name="quality", scope=PluginScope.USER)

    second = manager.install_plugin(source=plugin_root, scope=PluginScope.USER)

    assert second.root_dir == first.root_dir
    assert second.root_dir.exists()


def test_reinstall_rejects_same_version_with_different_contents(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    manager.uninstall_plugin(name="quality", scope=PluginScope.USER)
    (plugin_root / "README.md").write_text("changed", encoding="utf-8")

    with pytest.raises(ValueError, match="different contents"):
        manager.install_plugin(source=plugin_root, scope=PluginScope.USER)


def test_install_revalidates_installed_copy_capabilities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    install_calls = 0
    original_install_plugin_source = plugin_config_manager.install_plugin_source

    def mutate_after_validation_install(
        *,
        source: PluginInstallSource,
        app_config_dir: Path,
        target_dir: Path,
    ) -> None:
        nonlocal install_calls
        install_calls += 1
        original_install_plugin_source(
            source=source,
            app_config_dir=app_config_dir,
            target_dir=target_dir,
        )
        manifest_path = target_dir / "app" / "plugin.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["roles"] = "./roles"
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        roles_dir = target_dir / "roles"
        roles_dir.mkdir()
        _write_role(
            roles_dir / "reviewer.md",
            role_id="reviewer",
            tools=("missing_tool",),
        )

    monkeypatch.setattr(
        plugin_config_manager,
        "install_plugin_source",
        mutate_after_validation_install,
    )

    with pytest.raises(ValueError, match="Unknown tools"):
        PluginConfigManager(app_config_dir=app_config_dir).install_plugin(
            source=plugin_root,
            scope=PluginScope.USER,
        )

    assert install_calls == 1


def test_uninstall_prune_removes_unreferenced_installed_copy(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    installed = manager.install_plugin(source=plugin_root, scope=PluginScope.USER)

    manager.uninstall_plugin(name="quality", scope=PluginScope.USER, prune=True)

    assert not installed.root_dir.exists()


def test_uninstall_prune_aborts_before_state_change_on_invalid_state_file(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    project_root = tmp_path / "project"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        project_root=project_root,
    )
    installed = manager.install_plugin(source=plugin_root, scope=PluginScope.USER)
    project_state_file = get_plugin_project_state_file(
        app_config_dir=app_config_dir,
        project_root=project_root,
    )
    assert project_state_file is not None
    project_state_file.parent.mkdir(parents=True)
    project_state_file.write_text("{", encoding="utf-8")

    with pytest.raises(ValueError):
        manager.uninstall_plugin(name="quality", scope=PluginScope.USER, prune=True)

    records = manager.list_state_records()
    assert tuple(record.name for record in records) == ("quality",)
    assert records[0].root_dir == installed.root_dir
    assert installed.root_dir.exists()


def test_git_install_clones_source_into_installed_copy(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        return
    app_config_dir = tmp_path / "app"
    git_root = tmp_path / "quality-git"
    _write_plugin_manifest(git_root, name="quality", version="1.0.0")
    subprocess.run(["git", "init"], cwd=git_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.invalid"],
        cwd=git_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Tests"],
        cwd=git_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=git_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=git_root,
        check=True,
        capture_output=True,
    )

    installed = PluginConfigManager(app_config_dir=app_config_dir).install_git_plugin(
        source=str(git_root),
        scope=PluginScope.USER,
    )

    assert installed.name == "quality"
    assert installed.source.kind.value == "git"
    assert installed.root_dir.exists()
    assert (installed.root_dir / "app" / "plugin.json").exists()


def test_git_install_checks_out_requested_commit_ref(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        return
    app_config_dir = tmp_path / "app"
    git_root = tmp_path / "quality-git"
    _write_plugin_manifest(git_root, name="quality", version="1.0.0")
    subprocess.run(["git", "init"], cwd=git_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.invalid"],
        cwd=git_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Tests"],
        cwd=git_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=git_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "v1"],
        cwd=git_root,
        check=True,
        capture_output=True,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _write_plugin_manifest(git_root, name="quality", version="2.0.0")
    subprocess.run(["git", "add", "."], cwd=git_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "v2"],
        cwd=git_root,
        check=True,
        capture_output=True,
    )

    installed = PluginConfigManager(app_config_dir=app_config_dir).install_git_plugin(
        source=str(git_root),
        ref=commit,
        scope=PluginScope.USER,
    )

    assert installed.version == "1.0.0"
    assert installed.source.ref == commit
    assert (
        json.loads(
            (installed.root_dir / "app" / "plugin.json").read_text(encoding="utf-8")
        )["version"]
        == "1.0.0"
    )


def test_marketplace_install_resolves_local_source(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    marketplace_path = tmp_path / "marketplace.json"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    _write_marketplace(
        marketplace_path,
        name="quality",
        version="1.0.0",
        source=plugin_root,
        sha256=compute_plugin_tree_sha256(plugin_root),
    )

    installed = PluginConfigManager(
        app_config_dir=app_config_dir
    ).install_marketplace_plugin(
        name="quality",
        marketplace=marketplace_path,
        scope=PluginScope.USER,
    )

    assert installed.name == "quality"
    assert installed.source.kind.value == "marketplace"
    assert installed.source.marketplace == str(marketplace_path.resolve())
    assert installed.root_dir.exists()


def test_marketplace_install_without_latest_uses_highest_version(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    first_root = tmp_path / "quality-v1"
    second_root = tmp_path / "quality-v2"
    marketplace_path = tmp_path / "marketplace.json"
    _write_plugin_manifest(first_root, name="quality", version="1.0.0")
    _write_plugin_manifest(second_root, name="quality", version="2.0.0")
    marketplace_path.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "quality",
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

    installed = PluginConfigManager(
        app_config_dir=app_config_dir
    ).install_marketplace_plugin(
        name="quality",
        marketplace=marketplace_path,
        scope=PluginScope.USER,
    )

    assert installed.version == "2.0.0"


def test_marketplace_install_without_latest_prefers_stable_release(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    beta_root = tmp_path / "quality-beta"
    stable_root = tmp_path / "quality-stable"
    marketplace_path = tmp_path / "marketplace.json"
    _write_plugin_manifest(beta_root, name="quality", version="1.0.0-beta")
    _write_plugin_manifest(stable_root, name="quality", version="1.0.0")
    marketplace_path.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "quality",
                        "versions": [
                            {
                                "version": "1.0.0-beta",
                                "source": {
                                    "kind": "local",
                                    "value": str(beta_root.resolve()),
                                },
                            },
                            {
                                "version": "1.0.0",
                                "source": {
                                    "kind": "local",
                                    "value": str(stable_root.resolve()),
                                },
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    installed = PluginConfigManager(
        app_config_dir=app_config_dir
    ).install_marketplace_plugin(
        name="quality",
        marketplace=marketplace_path,
        scope=PluginScope.USER,
    )

    assert installed.version == "1.0.0"


def test_marketplace_install_without_latest_orders_prerelease_tokens(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    numeric_root = tmp_path / "quality-alpha-1"
    text_root = tmp_path / "quality-alpha-beta"
    marketplace_path = tmp_path / "marketplace.json"
    _write_plugin_manifest(numeric_root, name="quality", version="1.0.0-alpha.1")
    _write_plugin_manifest(text_root, name="quality", version="1.0.0-alpha.beta")
    marketplace_path.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "quality",
                        "versions": [
                            {
                                "version": "1.0.0-alpha.1",
                                "source": {
                                    "kind": "local",
                                    "value": str(numeric_root.resolve()),
                                },
                            },
                            {
                                "version": "1.0.0-alpha.beta",
                                "source": {
                                    "kind": "local",
                                    "value": str(text_root.resolve()),
                                },
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    installed = PluginConfigManager(
        app_config_dir=app_config_dir
    ).install_marketplace_plugin(
        name="quality",
        marketplace=marketplace_path,
        scope=PluginScope.USER,
    )

    assert installed.version == "1.0.0-alpha.beta"


def test_marketplace_service_rejects_invalid_index_payloads(tmp_path: Path) -> None:
    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{", encoding="utf-8")
    not_object = tmp_path / "not-object.json"
    not_object.write_text("[]", encoding="utf-8")
    invalid_index = tmp_path / "invalid-index.json"
    invalid_index.write_text(
        '{"plugins": [{"name": "quality", "unknown": true}]}',
        encoding="utf-8",
    )

    service = PluginMarketplaceService()

    with pytest.raises(ValueError, match="Invalid marketplace JSON"):
        service.load_index(invalid_json)
    with pytest.raises(ValueError, match="Marketplace JSON must be an object"):
        service.load_index(not_object)
    with pytest.raises(ValueError, match="Invalid marketplace index"):
        service.load_index(invalid_index)


def test_marketplace_install_rejects_checksum_mismatch(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    marketplace_path = tmp_path / "marketplace.json"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    _write_marketplace(
        marketplace_path,
        name="quality",
        version="1.0.0",
        source=plugin_root,
        sha256="0" * 64,
    )

    try:
        PluginConfigManager(app_config_dir=app_config_dir).install_marketplace_plugin(
            name="quality",
            marketplace=marketplace_path,
            scope=PluginScope.USER,
        )
    except ValueError as exc:
        assert "Plugin source checksum mismatch" in str(exc)
    else:
        raise AssertionError("Expected checksum mismatch to fail")


def test_marketplace_version_dependencies_are_enforced(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    marketplace_path = tmp_path / "marketplace.json"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    _write_marketplace(
        marketplace_path,
        name="quality",
        version="1.0.0",
        source=plugin_root,
        dependencies=[{"name": "base", "version": "1.0.0"}],
    )

    try:
        PluginConfigManager(app_config_dir=app_config_dir).install_marketplace_plugin(
            name="quality",
            marketplace=marketplace_path,
            scope=PluginScope.USER,
        )
    except ValueError as exc:
        assert "Missing plugin dependency: base" in str(exc)
    else:
        raise AssertionError("Expected marketplace dependency to fail")


def test_marketplace_dependencies_persist_for_runtime_checks(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    marketplace_path = tmp_path / "marketplace.json"
    base_root = tmp_path / "base"
    dependent_root = tmp_path / "dependent"
    _write_plugin_manifest(base_root, name="base", version="1.0.0")
    _write_plugin_manifest(dependent_root, name="dependent", version="1.0.0")
    _write_marketplace(
        marketplace_path,
        name="base",
        version="1.0.0",
        source=base_root,
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    manager.install_marketplace_plugin(
        name="base",
        marketplace=marketplace_path,
        scope=PluginScope.USER,
    )
    _write_marketplace(
        marketplace_path,
        name="dependent",
        version="1.0.0",
        source=dependent_root,
        dependencies=[{"name": "base", "version": "1.0.0"}],
    )

    installed = manager.install_marketplace_plugin(
        name="dependent",
        marketplace=marketplace_path,
        scope=PluginScope.USER,
    )
    manager.uninstall_plugin(name="base", scope=PluginScope.USER)
    registry = manager.load_registry()

    assert installed.dependencies[0].name == "base"
    assert registry.plugins[0].name == "dependent"
    assert registry.plugins[0].enabled is False
    assert any(
        "Missing plugin dependency: base" in diagnostic.message
        for diagnostic in registry.diagnostics
    )


def test_install_rejects_missing_dependency(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "dependent"
    _write_plugin_manifest(
        plugin_root,
        name="dependent",
        version="1.0.0",
        dependencies=[{"name": "base", "version": "1.0.0"}],
    )

    try:
        PluginConfigManager(app_config_dir=app_config_dir).install_plugin(
            source=plugin_root,
            scope=PluginScope.USER,
        )
    except ValueError as exc:
        assert "Missing plugin dependency: base" in str(exc)
    else:
        raise AssertionError("Expected missing dependency to fail")


def test_runtime_skips_plugin_with_missing_persisted_dependency(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    base_root = tmp_path / "base"
    dependent_root = tmp_path / "dependent"
    _write_plugin_manifest(base_root, name="base", version="1.0.0")
    _write_plugin_manifest(
        dependent_root,
        name="dependent",
        version="1.0.0",
        dependencies=[{"name": "base", "version": "1.0.0"}],
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    manager.install_plugin(source=base_root, scope=PluginScope.USER)
    manager.install_plugin(source=dependent_root, scope=PluginScope.USER)
    state_path = app_config_dir / "plugins" / "plugins.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["plugins"] = [
        item for item in state["plugins"] if item["name"] == "dependent"
    ]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    registry = manager.load_registry()

    assert len(registry.plugins) == 1
    assert registry.plugins[0].name == "dependent"
    assert registry.plugins[0].enabled is False
    assert len(registry.diagnostics) == 1
    assert "Missing plugin dependency: base" in registry.diagnostics[0].message


def test_runtime_skips_local_plugin_with_missing_dependency(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    dependent_root = tmp_path / "dependent"
    _write_plugin_manifest(
        dependent_root,
        name="dependent",
        version="1.0.0",
        dependencies=[{"name": "base", "version": "1.0.0"}],
    )
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        plugin_dirs=(dependent_root,),
    )

    registry = manager.load_registry()

    assert len(registry.plugins) == 1
    assert registry.plugins[0].name == "dependent"
    assert registry.plugins[0].scope == PluginScope.LOCAL
    assert registry.plugins[0].enabled is False
    assert len(registry.diagnostics) == 1
    assert "Missing plugin dependency: base" in registry.diagnostics[0].message


def test_install_rejects_disabled_dependency(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    base_root = tmp_path / "base"
    dependent_root = tmp_path / "dependent"
    _write_plugin_manifest(base_root, name="base", version="1.0.0")
    _write_plugin_manifest(
        dependent_root,
        name="dependent",
        version="1.0.0",
        dependencies=[{"name": "base", "version": "1.0.0"}],
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    manager.install_plugin(
        source=base_root,
        scope=PluginScope.USER,
        enabled=False,
    )

    try:
        manager.install_plugin(source=dependent_root, scope=PluginScope.USER)
    except ValueError as exc:
        assert "Plugin dependency is disabled: base" in str(exc)
    else:
        raise AssertionError("Expected disabled dependency to fail")


def test_install_rejects_local_dependency_disabled_by_required_config(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    base_root = tmp_path / "base"
    dependent_root = tmp_path / "dependent"
    _write_plugin_manifest(
        base_root,
        name="base",
        version="1.0.0",
        user_config={"token": {"type": "string", "required": True}},
    )
    _write_plugin_manifest(
        dependent_root,
        name="dependent",
        version="1.0.0",
        dependencies=[{"name": "base", "version": "1.0.0"}],
    )
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        plugin_dirs=(base_root,),
    )

    try:
        manager.install_plugin(source=dependent_root, scope=PluginScope.USER)
    except ValueError as exc:
        assert "Plugin dependency is disabled: base" in str(exc)
    else:
        raise AssertionError("Expected local disabled dependency to fail")


def test_install_rejects_installed_dependency_disabled_by_required_config(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    base_root = tmp_path / "base"
    dependent_root = tmp_path / "dependent"
    _write_plugin_manifest(
        base_root,
        name="base",
        version="1.0.0",
        user_config={"token": {"type": "string", "required": True}},
    )
    _write_plugin_manifest(
        dependent_root,
        name="dependent",
        version="1.0.0",
        dependencies=[{"name": "base", "version": "1.0.0"}],
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    manager.install_plugin(source=base_root, scope=PluginScope.USER)
    registry = manager.load_registry()
    assert registry.plugins[0].name == "base"
    assert registry.plugins[0].enabled is False

    try:
        manager.install_plugin(source=dependent_root, scope=PluginScope.USER)
    except ValueError as exc:
        assert "Plugin dependency is disabled: base" in str(exc)
    else:
        raise AssertionError("Expected runtime-disabled dependency to fail")


def test_install_allows_dependency_from_another_scope(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    project_root = tmp_path / "repo"
    base_root = tmp_path / "base"
    dependent_root = tmp_path / "dependent"
    _write_plugin_manifest(base_root, name="base", version="1.0.0")
    _write_plugin_manifest(
        dependent_root,
        name="dependent",
        version="1.0.0",
        dependencies=[{"name": "base", "version": "1.0.0"}],
    )
    manager = PluginConfigManager(
        app_config_dir=app_config_dir,
        project_root=project_root,
    )
    manager.install_plugin(source=base_root, scope=PluginScope.USER)

    installed = manager.install_plugin(
        source=dependent_root,
        scope=PluginScope.PROJECT,
    )

    assert installed.name == "dependent"
    registry = manager.load_registry()
    assert {plugin.name for plugin in registry.plugins} == {"base", "dependent"}
    assert not registry.diagnostics


def test_install_allows_hook_agent_role_from_dependency_plugin(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    base_root = tmp_path / "base"
    dependent_root = tmp_path / "dependent"
    _write_plugin_manifest(base_root, name="base", version="1.0.0")
    roles_dir = base_root / "roles"
    roles_dir.mkdir()
    _write_role(roles_dir / "reviewer.md", role_id="reviewer", tools=("read",))
    _write_plugin_manifest(
        dependent_root,
        name="dependent",
        version="1.0.0",
        dependencies=[{"name": "base", "version": "1.0.0"}],
    )
    hooks_dir = dependent_root / "hooks"
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
                                    "role_id": "base:reviewer",
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
    manager.install_plugin(source=base_root, scope=PluginScope.USER)

    installed = manager.install_plugin(source=dependent_root, scope=PluginScope.USER)

    assert installed.name == "dependent"


def test_install_requires_manifest(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "no-manifest"
    plugin_root.mkdir()

    try:
        PluginConfigManager(app_config_dir=app_config_dir).install_plugin(
            source=plugin_root,
            scope=PluginScope.USER,
        )
    except ValueError as exc:
        assert "Plugin manifest is required" in str(exc)
    else:
        raise AssertionError("Expected installed plugin manifest to be required")


def test_install_rejects_missing_explicit_component_path(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(
        plugin_root,
        name="quality",
        version="1.0.0",
        skills="./missing-skills",
    )

    try:
        PluginConfigManager(app_config_dir=app_config_dir).install_plugin(
            source=plugin_root,
            scope=PluginScope.USER,
        )
    except ValueError as exc:
        assert "Plugin component directory does not exist" in str(exc)
    else:
        raise AssertionError("Expected missing explicit component path to fail")


def test_install_rejects_unknown_plugin_settings(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    (plugin_root / "settings.json").write_text(
        '{"agent": "quality:reviewer", "unknown": true}',
        encoding="utf-8",
    )

    try:
        PluginConfigManager(app_config_dir=app_config_dir).install_plugin(
            source=plugin_root,
            scope=PluginScope.USER,
        )
    except ValueError as exc:
        assert "Unknown plugin settings field(s): unknown" in str(exc)
    else:
        raise AssertionError("Expected unknown plugin setting to fail")


def test_runtime_disables_plugin_with_disabled_dependency(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    base_root = tmp_path / "base"
    dependent_root = tmp_path / "dependent"
    _write_plugin_manifest(base_root, name="base", version="1.0.0")
    _write_plugin_manifest(
        dependent_root,
        name="dependent",
        version="1.0.0",
        dependencies=[{"name": "base", "version": "1.0.0"}],
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    manager.install_plugin(source=base_root, scope=PluginScope.USER)
    manager.install_plugin(source=dependent_root, scope=PluginScope.USER)
    manager.set_plugin_enabled(name="base", scope=PluginScope.USER, enabled=False)

    registry = manager.load_registry()

    assert len(registry.plugins) == 2
    by_name = {plugin.name: plugin for plugin in registry.plugins}
    assert by_name["base"].enabled is False
    assert by_name["dependent"].enabled is False
    assert "Plugin dependency is disabled: base" in registry.diagnostics[0].message


def test_runtime_disables_plugin_with_missing_dependency_manifest(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    base_root = tmp_path / "base"
    dependent_root = tmp_path / "dependent"
    _write_plugin_manifest(base_root, name="base", version="1.0.0")
    _write_plugin_manifest(
        dependent_root,
        name="dependent",
        version="1.0.0",
        dependencies=[{"name": "base", "version": "1.0.0"}],
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    base_record = manager.install_plugin(source=base_root, scope=PluginScope.USER)
    manager.install_plugin(source=dependent_root, scope=PluginScope.USER)
    (base_record.root_dir / "app" / "plugin.json").unlink()

    registry = manager.load_registry()

    by_name = {plugin.name: plugin for plugin in registry.plugins}
    assert by_name["dependent"].enabled is False
    messages = {diagnostic.message for diagnostic in registry.diagnostics}
    assert "Plugin manifest is unavailable" in messages
    assert "Plugin dependency is unavailable: base" in messages


def test_runtime_disables_plugin_with_transitively_unavailable_dependency(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    base_root = tmp_path / "base"
    dependent_root = tmp_path / "dependent"
    _write_plugin_manifest(base_root, name="base", version="1.0.0")
    _write_plugin_manifest(
        dependent_root,
        name="dependent",
        version="1.0.0",
        dependencies=[{"name": "base", "version": "1.0.0"}],
    )
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    base_record = manager.install_plugin(source=base_root, scope=PluginScope.USER)
    manager.install_plugin(source=dependent_root, scope=PluginScope.USER)
    _write_plugin_manifest(
        base_record.root_dir,
        name="base",
        version="1.0.0",
        dependencies=[{"name": "missing", "version": "1.0.0"}],
    )

    registry = manager.load_registry()

    assert len(registry.plugins) == 2
    by_name = {plugin.name: plugin for plugin in registry.plugins}
    assert by_name["base"].enabled is False
    assert by_name["dependent"].enabled is False
    messages = {diagnostic.message for diagnostic in registry.diagnostics}
    assert "Missing plugin dependency: missing" in messages
    assert "Plugin dependency is unavailable: base" in messages


def _write_plugin_manifest(
    plugin_root: Path,
    *,
    name: str,
    version: str,
    dependencies: list[dict[str, str]] | None = None,
    skills: str | None = None,
    user_config: dict[str, object] | None = None,
) -> None:
    manifest_dir = plugin_root / "app"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"name": name, "version": version}
    if dependencies is not None:
        payload["dependencies"] = dependencies
    if skills is not None:
        payload["skills"] = skills
    if user_config is not None:
        payload["userConfig"] = user_config
    (manifest_dir / "plugin.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _write_marketplace(
    marketplace_path: Path,
    *,
    name: str,
    version: str,
    source: Path,
    sha256: str = "",
    dependencies: list[dict[str, str]] | None = None,
) -> None:
    marketplace_path.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": name,
                        "description": "Quality tools",
                        "latest": version,
                        "versions": [
                            {
                                "version": version,
                                "source": {
                                    "kind": "local",
                                    "value": str(source.resolve()),
                                },
                                "sha256": sha256,
                                "dependencies": dependencies or [],
                            }
                        ],
                    }
                ]
            }
        ),
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
