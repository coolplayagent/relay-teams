# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from relay_teams.plugins import config_manager as plugin_config_manager
from relay_teams.plugins import installers as plugin_installers
from relay_teams.plugins import claude_marketplace_provider
from relay_teams.env import ProxyEnvConfig
from relay_teams.plugins.claude_plugin_adapter import adapt_plugin_tree
from relay_teams.plugins.config_manager import PluginConfigManager
from relay_teams.plugins.integrity import compute_plugin_tree_sha256
from relay_teams.plugins.marketplace_service import PluginMarketplaceService
from relay_teams.plugins.marketplace_models import (
    PluginMarketplaceEntry,
    PluginMarketplaceIndex,
    PluginMarketplaceProviderKind,
    PluginMarketplaceSource,
    PluginMarketplaceVersion,
)
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


def test_install_git_plugin_source_reports_runtime_clone_errors(
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


def test_install_git_plugin_source_checks_out_sha_without_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = PluginInstallSource(
        kind=PluginInstallSourceKind.GIT,
        value="https://example.test/plugin.git",
        sha="a" * 40,
    )
    calls: list[list[str]] = []

    def fake_git(args: list[str]) -> None:
        calls.append(args)
        if args[1] == "clone":
            Path(args[-1]).mkdir(parents=True)

    def skip_sha_verification(*, clone_dir: Path, expected_sha: str) -> None:
        assert clone_dir.exists()
        assert expected_sha == source.sha

    monkeypatch.setattr(plugin_installers, "_run_git", fake_git)
    monkeypatch.setattr(plugin_installers, "_verify_git_sha", skip_sha_verification)

    plugin_installers.install_git_plugin_source(
        source=source,
        app_config_dir=tmp_path / "app",
        target_dir=tmp_path / "target",
    )

    assert calls[0][:4] == ["git", "clone", "--no-checkout", source.value]
    assert any(call[-1] == source.sha for call in calls if "checkout" in call)
    assert (tmp_path / "target").exists()


def test_clone_git_ref_prefers_sha_over_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_git(args: list[str]) -> None:
        calls.append(args)

    monkeypatch.setattr(plugin_installers, "_run_git", fake_git)

    plugin_installers._clone_git_ref(
        source=PluginInstallSource(
            kind=PluginInstallSourceKind.GIT,
            value="https://example.test/plugin.git",
            ref="main",
            sha="abc123",
        ),
        clone_dir=tmp_path / "clone",
    )

    assert calls[1][-1] == "abc123"


def test_install_git_plugin_source_accepts_short_sha(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = PluginInstallSource(
        kind=PluginInstallSourceKind.GIT,
        value="https://example.test/plugin.git",
        sha="abc1234",
    )

    def fake_git(args: list[str]) -> None:
        if args[1] == "clone":
            clone_dir = Path(args[-1])
            clone_dir.mkdir(parents=True)
            (clone_dir / "plugin.json").write_text("{}", encoding="utf-8")

    def fake_run(
        args: list[str],
        *,
        check: bool,
        capture_output: bool,
        env: dict[str, str],
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        assert args[-1] == "HEAD"
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="abc1234fffffffffffffffffffffffffffffffff\n",
            stderr="",
        )

    monkeypatch.setattr(plugin_installers, "_run_git", fake_git)
    monkeypatch.setattr(plugin_installers.subprocess, "run", fake_run)

    plugin_installers.install_git_plugin_source(
        source=source,
        app_config_dir=tmp_path / "app",
        target_dir=tmp_path / "target",
    )

    assert (tmp_path / "target" / "plugin.json").exists()


def test_verify_git_sha_accepts_uppercase_expected_sha(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(
        args: list[str],
        *,
        check: bool,
        capture_output: bool,
        env: dict[str, str],
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        assert args[-1] == "HEAD"
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="abc1234fffffffffffffffffffffffffffffffff\n",
            stderr="",
        )

    monkeypatch.setattr(plugin_installers.subprocess, "run", fake_run)

    plugin_installers._verify_git_sha(
        clone_dir=tmp_path / "clone",
        expected_sha="ABC1234",
    )


def test_install_git_subdir_plugin_source_copies_selected_subdir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = PluginInstallSource(
        kind=PluginInstallSourceKind.GIT_SUBDIR,
        value="https://example.test/marketplace.git",
        subdir="plugins/quality",
    )

    def fake_git(args: list[str]) -> None:
        clone_dir = Path(args[-1])
        plugin_dir = clone_dir / "plugins" / "quality"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text("{}", encoding="utf-8")
        (clone_dir / "plugins" / "ignored").mkdir()

    monkeypatch.setattr(plugin_installers, "_run_git", fake_git)

    plugin_installers.install_git_subdir_plugin_source(
        source=source,
        app_config_dir=tmp_path / "app",
        target_dir=tmp_path / "target",
    )

    assert (tmp_path / "target" / "plugin.json").exists()
    assert not (tmp_path / "target" / "ignored").exists()


def test_install_git_subdir_plugin_source_replaces_stale_cache_and_uses_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []
    source = PluginInstallSource(
        kind=PluginInstallSourceKind.GIT_SUBDIR,
        value="https://example.test/marketplace.git",
        ref="feature",
        subdir="plugins/quality",
    )
    target_dir = tmp_path / "target"
    cache_root = tmp_path / "app" / "plugins" / "cache"
    stale_cache = cache_root / plugin_installers._cache_dir_name(
        f"{source.value}:{source.subdir}:{target_dir.expanduser().resolve()}"
    )
    stale_cache.mkdir(parents=True)
    (stale_cache / "stale.txt").write_text("old", encoding="utf-8")

    def fake_git(args: list[str]) -> None:
        calls.append(args)
        if "clone" in args:
            clone_dir = Path(args[-1])
            plugin_dir = clone_dir / "plugins" / "quality"
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "plugin.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(plugin_installers, "_run_git", fake_git)

    plugin_installers.install_git_subdir_plugin_source(
        source=source,
        app_config_dir=tmp_path / "app",
        target_dir=target_dir,
    )

    assert calls[0][:3] == ["git", "clone", "--no-checkout"]
    assert calls[1][-1] == "feature"
    assert (target_dir / "plugin.json").exists()
    assert not (stale_cache / "stale.txt").exists()


def test_install_git_plugin_source_reports_clone_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = PluginInstallSource(
        kind=PluginInstallSourceKind.GIT,
        value="https://example.test/plugin.git",
    )

    def failed_git(args: list[str]) -> None:
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=args,
            stderr="clone failed",
        )

    monkeypatch.setattr(plugin_installers, "_run_git", failed_git)
    with pytest.raises(ValueError, match="clone failed"):
        plugin_installers.install_git_plugin_source(
            source=source,
            app_config_dir=tmp_path / "app",
            target_dir=tmp_path / "target",
        )

    def missing_git(args: list[str]) -> None:
        raise OSError("git missing")

    monkeypatch.setattr(plugin_installers, "_run_git", missing_git)
    with pytest.raises(ValueError, match="git missing"):
        plugin_installers.install_git_plugin_source(
            source=source,
            app_config_dir=tmp_path / "app",
            target_dir=tmp_path / "target",
        )

    def timed_out_git(args: list[str]) -> None:
        raise subprocess.TimeoutExpired(cmd=args, timeout=1)

    monkeypatch.setattr(plugin_installers, "_run_git", timed_out_git)
    with pytest.raises(ValueError, match="Timed out"):
        plugin_installers.install_git_plugin_source(
            source=source,
            app_config_dir=tmp_path / "app",
            target_dir=tmp_path / "target",
        )


def test_install_plugin_source_dispatches_git_subdir_and_unsupported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[Path] = []

    def fake_install_git_subdir_plugin_source(
        *,
        source: PluginInstallSource,
        app_config_dir: Path,
        target_dir: Path,
    ) -> None:
        calls.append(target_dir)

    monkeypatch.setattr(
        plugin_installers,
        "install_git_subdir_plugin_source",
        fake_install_git_subdir_plugin_source,
    )

    plugin_installers.install_plugin_source(
        source=PluginInstallSource(
            kind=PluginInstallSourceKind.GIT_SUBDIR,
            value="https://example.test/repo.git",
            subdir="plugins/quality",
        ),
        app_config_dir=tmp_path / "app",
        target_dir=tmp_path / "target",
    )

    assert calls == [tmp_path / "target"]
    with pytest.raises(ValueError, match="Unsupported plugin source kind"):
        plugin_installers.install_plugin_source(
            source=PluginInstallSource(
                kind=PluginInstallSourceKind.UNSUPPORTED,
                value="npm",
            ),
            app_config_dir=tmp_path / "app",
            target_dir=tmp_path / "target",
        )


def test_git_sha_and_subdir_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()

    def failed_run(
        args: list[str],
        *,
        check: bool,
        capture_output: bool,
        env: dict[str, str],
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=args,
            stderr="rev-parse failed",
        )

    monkeypatch.setattr(plugin_installers.subprocess, "run", failed_run)
    with pytest.raises(ValueError, match="rev-parse failed"):
        plugin_installers._verify_git_sha(clone_dir=clone_dir, expected_sha="abc")

    def mismatched_run(
        args: list[str],
        *,
        check: bool,
        capture_output: bool,
        env: dict[str, str],
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="def456\n",
            stderr="",
        )

    monkeypatch.setattr(plugin_installers.subprocess, "run", mismatched_run)
    with pytest.raises(ValueError, match="commit mismatch"):
        plugin_installers._verify_git_sha(clone_dir=clone_dir, expected_sha="abc")
    with pytest.raises(ValueError, match="subdirectory is unsafe"):
        plugin_installers._resolve_git_subdir(clone_dir=clone_dir, subdir="../x")
    with pytest.raises(ValueError, match="subdirectory does not exist"):
        plugin_installers._resolve_git_subdir(clone_dir=clone_dir, subdir="missing")


def test_plugin_git_subprocess_env_includes_saved_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXISTING_ENV", "1")
    monkeypatch.setattr(
        plugin_installers,
        "load_proxy_env_config",
        lambda: ProxyEnvConfig(https_proxy="http://proxy.example:8080"),
    )

    env = plugin_installers._git_subprocess_env()

    assert env["EXISTING_ENV"] == "1"
    assert env["HTTPS_PROXY"] == "http://proxy.example:8080"
    assert env["https_proxy"] == "http://proxy.example:8080"


def test_claude_marketplace_git_subprocess_env_includes_saved_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXISTING_ENV", "1")
    monkeypatch.setattr(
        claude_marketplace_provider,
        "load_proxy_env_config",
        lambda: ProxyEnvConfig(https_proxy="http://proxy.example:8080"),
    )

    env = claude_marketplace_provider._git_subprocess_env()

    assert env["EXISTING_ENV"] == "1"
    assert env["HTTPS_PROXY"] == "http://proxy.example:8080"
    assert env["https_proxy"] == "http://proxy.example:8080"


def test_claude_marketplace_refresh_reclones_cached_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_git(args: list[str]) -> None:
        calls.append(args)
        checkout_dir = Path(args[-1])
        if "clone" in args:
            marketplace_dir = checkout_dir / ".claude-plugin"
            marketplace_dir.mkdir(parents=True)
            (marketplace_dir / "marketplace.json").write_text(
                '{"plugins": []}',
                encoding="utf-8",
            )

    monkeypatch.setattr(claude_marketplace_provider, "_run_git", fake_git)
    service = PluginMarketplaceService()
    source = PluginMarketplaceSource(
        provider=PluginMarketplaceProviderKind.CLAUDE,
        name="test-claude",
        value="example/marketplace",
    )

    service.load_provider_index(source=source, app_config_dir=tmp_path / "app")
    service.load_provider_index(source=source, app_config_dir=tmp_path / "app")
    service.load_provider_index(
        source=source.model_copy(update={"refresh": True}),
        app_config_dir=tmp_path / "app",
    )

    clone_calls = [call for call in calls if "clone" in call]
    assert len(clone_calls) == 2


def test_plugin_git_args_enable_windows_long_paths() -> None:
    assert plugin_installers._git_args(["git", "clone", "repo"]) == [
        "git",
        "-c",
        "core.longpaths=true",
        "clone",
        "repo",
    ]
    assert claude_marketplace_provider._git_args(["git", "clone", "repo"]) == [
        "git",
        "-c",
        "core.longpaths=true",
        "clone",
        "repo",
    ]


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


def test_marketplace_install_persists_resolved_relative_marketplace_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    plugin_root = tmp_path / "quality"
    marketplace_path = tmp_path / "marketplace.json"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    _write_marketplace(
        marketplace_path,
        name="quality",
        version="1.0.0",
        source=plugin_root,
    )
    monkeypatch.chdir(tmp_path)

    installed = PluginConfigManager(
        app_config_dir=app_config_dir
    ).install_marketplace_plugin(
        name="quality",
        marketplace=Path("marketplace.json"),
        scope=PluginScope.USER,
    )

    assert installed.source.marketplace == str(marketplace_path.resolve())


def test_claude_marketplace_loads_relative_plugin_source(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    marketplace_root = tmp_path / "claude-marketplace"
    plugin_root = marketplace_root / "plugins" / "quality"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    _write_claude_marketplace(
        marketplace_root,
        name="quality",
        source="./plugins/quality",
        version="1.0.0",
    )

    index = PluginMarketplaceService().load_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAUDE,
            name="test-claude",
            value=str(marketplace_root),
        ),
        app_config_dir=app_config_dir,
    )

    assert index.plugins[0].name == "quality"
    assert index.plugins[0].versions[0].source.kind == PluginInstallSourceKind.LOCAL
    assert index.plugins[0].versions[0].source.value == str(plugin_root.resolve())


def test_claude_marketplace_treats_slash_relative_source_as_local(
    tmp_path: Path,
) -> None:
    marketplace_root = tmp_path / "claude-marketplace"
    plugin_root = marketplace_root / "plugins" / "quality"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    _write_claude_marketplace(
        marketplace_root,
        name="quality",
        source="plugins/quality",
        version="1.0.0",
    )

    index = PluginMarketplaceService().load_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAUDE,
            name="test-claude",
            value=str(marketplace_root),
        ),
        app_config_dir=tmp_path / "app",
    )

    assert index.plugins[0].versions[0].source.kind == PluginInstallSourceKind.LOCAL
    assert index.plugins[0].versions[0].source.value == str(plugin_root.resolve())


def test_claude_marketplace_uses_plugin_root_metadata(tmp_path: Path) -> None:
    marketplace_root = tmp_path / "claude-marketplace"
    plugin_root = marketplace_root / "packages" / "quality"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    marketplace_dir = marketplace_root / ".claude-plugin"
    marketplace_dir.mkdir(parents=True)
    (marketplace_dir / "marketplace.json").write_text(
        json.dumps(
            {
                "metadata": {"pluginRoot": "packages"},
                "plugins": [
                    {
                        "name": "quality",
                        "version": "1.0.0",
                        "source": "quality",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    index = PluginMarketplaceService().load_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAUDE,
            name="test-claude",
            value=str(marketplace_root),
        ),
        app_config_dir=tmp_path / "app",
    )

    assert index.plugins[0].versions[0].source.value == str(plugin_root.resolve())


def test_claude_marketplace_install_resolves_relative_plugin_source(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    marketplace_root = tmp_path / "claude-marketplace"
    plugin_root = marketplace_root / "plugins" / "quality"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    _write_claude_marketplace(
        marketplace_root,
        name="quality",
        source="./plugins/quality",
        version="1.0.0",
    )

    installed = PluginConfigManager(
        app_config_dir=app_config_dir
    ).install_marketplace_plugin(
        name="quality",
        marketplace=Path("test-claude"),
        marketplace_provider=PluginMarketplaceProviderKind.CLAUDE,
        marketplace_source=str(marketplace_root),
        marketplace_ref="main",
        scope=PluginScope.USER,
    )

    assert installed.name == "quality"
    assert installed.source.kind == PluginInstallSourceKind.MARKETPLACE
    assert installed.source.marketplace == "test-claude"
    assert installed.source.marketplace_provider == "claude"
    assert installed.source.marketplace_source == str(marketplace_root)
    assert installed.source.marketplace_ref == "main"
    assert installed.root_dir.exists()


def test_claude_marketplace_install_persists_resolved_local_source_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    marketplace_root = tmp_path / "claude-marketplace"
    plugin_root = marketplace_root / "plugins" / "quality"
    _write_plugin_manifest(plugin_root, name="quality", version="1.0.0")
    _write_claude_marketplace(
        marketplace_root,
        name="quality",
        source="./plugins/quality",
        version="1.0.0",
    )
    monkeypatch.chdir(tmp_path)

    installed = PluginConfigManager(
        app_config_dir=app_config_dir
    ).install_marketplace_plugin(
        name="quality",
        marketplace=Path("test-claude"),
        marketplace_provider=PluginMarketplaceProviderKind.CLAUDE,
        marketplace_source="claude-marketplace",
        scope=PluginScope.USER,
    )

    assert installed.source.marketplace_source == str(marketplace_root.resolve())


def test_claude_marketplace_source_preserves_empty_default() -> None:
    source = PluginConfigManager._marketplace_source(
        provider=PluginMarketplaceProviderKind.CLAUDE,
        marketplace="claude-plugins-official",
        marketplace_source="",
    )

    assert source.value == ""


def test_claude_marketplace_source_reference_resolves_existing_local_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    marketplace_root = tmp_path / "claude-marketplace"
    marketplace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    assert PluginConfigManager._marketplace_source_reference(
        provider=PluginMarketplaceProviderKind.CLAUDE,
        marketplace_source="claude-marketplace",
    ) == str(marketplace_root.resolve())
    assert (
        PluginConfigManager._marketplace_source_reference(
            provider=PluginMarketplaceProviderKind.CLAUDE,
            marketplace_source="anthropics/claude-plugins-official",
        )
        == "anthropics/claude-plugins-official"
    )
    assert (
        PluginConfigManager._marketplace_source_reference(
            provider=PluginMarketplaceProviderKind.LOCAL_JSON,
            marketplace_source="marketplace.json",
        )
        == "marketplace.json"
    )


def test_config_manager_materializes_validation_sources_for_cached_adapters(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = PluginConfigManager(app_config_dir=tmp_path / "app")
    installed_targets: list[Path] = []

    def fake_install_plugin_source(
        *,
        source: PluginInstallSource,
        app_config_dir: Path,
        target_dir: Path,
    ) -> None:
        installed_targets.append(target_dir)
        target_dir.mkdir(parents=True)
        (target_dir / "plugin.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        plugin_config_manager,
        "install_plugin_source",
        fake_install_plugin_source,
    )
    local_source = PluginInstallSource(
        kind=PluginInstallSourceKind.LOCAL,
        value="local-quality",
        adapter="claude",
    )
    local_cache = (
        tmp_path
        / "app"
        / "plugins"
        / "cache"
        / "validation"
        / manager._cache_dir_name(local_source.value)
    )
    local_cache.mkdir(parents=True)
    (local_cache / "old.txt").write_text("old", encoding="utf-8")

    local_target = manager._materialize_validation_source(local_source)
    git_subdir_target = manager._materialize_validation_source(
        PluginInstallSource(
            kind=PluginInstallSourceKind.GIT_SUBDIR,
            value="https://example.test/repo.git",
            subdir="plugins/quality",
        )
    )

    assert local_target == installed_targets[0]
    assert not (local_target / "old.txt").exists()
    assert git_subdir_target == installed_targets[1]
    with pytest.raises(ValueError, match="Unsupported plugin source kind"):
        manager._materialize_validation_source(
            PluginInstallSource(kind=PluginInstallSourceKind.UNSUPPORTED, value="npm")
        )


def test_marketplace_provider_from_string_defaults_and_rejects_unknown() -> None:
    assert (
        plugin_config_manager._marketplace_provider_from_string("")
        == PluginMarketplaceProviderKind.LOCAL_JSON
    )
    assert (
        plugin_config_manager._marketplace_provider_from_string("claude")
        == PluginMarketplaceProviderKind.CLAUDE
    )
    with pytest.raises(ValueError, match="Unsupported plugin marketplace provider"):
        plugin_config_manager._marketplace_provider_from_string("unknown")


def test_claude_marketplace_update_refreshes_provider_index(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_sources: list[PluginMarketplaceSource] = []

    def fake_load_provider_index(
        self: PluginMarketplaceService,
        *,
        source: PluginMarketplaceSource,
        app_config_dir: Path,
    ) -> PluginMarketplaceIndex:
        captured_sources.append(source)
        return PluginMarketplaceIndex(
            plugins=(
                PluginMarketplaceEntry(
                    name="quality",
                    latest="1.0.0",
                    versions=(
                        PluginMarketplaceVersion(
                            version="1.0.0",
                            source=PluginInstallSource(
                                kind=PluginInstallSourceKind.LOCAL,
                                value=str(tmp_path / "quality"),
                            ),
                        ),
                    ),
                ),
            )
        )

    monkeypatch.setattr(
        PluginMarketplaceService,
        "load_provider_index",
        fake_load_provider_index,
    )
    manager = PluginConfigManager(app_config_dir=tmp_path / "app")

    manager._resolve_update_install_source(
        source=PluginInstallSource(
            kind=PluginInstallSourceKind.MARKETPLACE,
            value="quality",
            marketplace="claude-plugins-official",
            marketplace_provider="claude",
        ),
        version=None,
    )

    assert captured_sources[0].refresh is True


def test_claude_marketplace_provider_rejects_invalid_payloads(tmp_path: Path) -> None:
    provider = claude_marketplace_provider.ClaudeMarketplaceProvider()
    marketplace_root = tmp_path / "marketplace"
    marketplace_dir = marketplace_root / ".claude-plugin"
    marketplace_dir.mkdir(parents=True)
    marketplace_file = marketplace_dir / "marketplace.json"

    marketplace_file.write_text('{"plugins": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="plugins must be a list"):
        provider.load_index(
            source=PluginMarketplaceSource(
                provider=PluginMarketplaceProviderKind.CLAUDE,
                value=str(marketplace_root),
            ),
            app_config_dir=tmp_path / "app",
        )

    marketplace_file.write_text('{"plugins": [1]}', encoding="utf-8")
    with pytest.raises(ValueError, match="plugin entries must be objects"):
        provider.load_index(
            source=PluginMarketplaceSource(
                provider=PluginMarketplaceProviderKind.CLAUDE,
                value=str(marketplace_root),
            ),
            app_config_dir=tmp_path / "app",
        )

    marketplace_file.write_text(
        json.dumps({"plugins": [{"name": "quality"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="plugin source is required"):
        provider.load_index(
            source=PluginMarketplaceSource(
                provider=PluginMarketplaceProviderKind.CLAUDE,
                value=str(marketplace_root),
            ),
            app_config_dir=tmp_path / "app",
        )


def test_claude_marketplace_provider_normalizes_source_variants(
    tmp_path: Path,
) -> None:
    provider = claude_marketplace_provider.ClaudeMarketplaceProvider()

    git_source = provider._string_source(
        value="https://example.test/plugin.git",
        marketplace_root=tmp_path,
        plugin_root="",
    )
    github_source, _ = provider._object_source(
        {"source": "github", "repo": "owner/repo", "ref": "main"}
    )
    git_subdir_source, _ = provider._object_source(
        {
            "source": "git-subdir",
            "url": "owner/repo",
            "path": "plugins/quality",
            "commit": "abc123",
        }
    )
    unknown_source, reason = provider._object_source({"source": "archive"})

    assert git_source.kind == PluginInstallSourceKind.GIT
    assert github_source.value == "https://github.com/owner/repo.git"
    assert github_source.ref == "main"
    assert git_subdir_source.kind == PluginInstallSourceKind.GIT_SUBDIR
    assert git_subdir_source.subdir == "plugins/quality"
    assert git_subdir_source.sha == "abc123"
    assert unknown_source.kind == PluginInstallSourceKind.UNSUPPORTED
    assert reason == "Unsupported Claude marketplace plugin source: archive"


def test_claude_marketplace_provider_normalizes_plugin_root_trailing_slash(
    tmp_path: Path,
) -> None:
    provider = claude_marketplace_provider.ClaudeMarketplaceProvider()
    source = provider._string_source(
        value="quality",
        marketplace_root=tmp_path,
        plugin_root="./plugins/",
    )

    assert source.kind == PluginInstallSourceKind.LOCAL
    assert source.value == str(tmp_path.resolve() / "plugins" / "quality")


def test_claude_marketplace_provider_rejects_symlink_escape(
    tmp_path: Path,
) -> None:
    provider = claude_marketplace_provider.ClaudeMarketplaceProvider()
    marketplace_root = tmp_path / "marketplace"
    outside = tmp_path / "outside"
    outside.mkdir()
    plugin_parent = marketplace_root / "plugins"
    plugin_parent.mkdir(parents=True)
    try:
        (plugin_parent / "quality").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is not available: {exc}")

    with pytest.raises(ValueError, match="escapes marketplace root"):
        provider._string_source(
            value="./plugins/quality",
            marketplace_root=marketplace_root,
            plugin_root="",
        )


def test_claude_marketplace_provider_derives_versions_and_dependencies() -> None:
    provider = claude_marketplace_provider.ClaudeMarketplaceProvider()
    source_with_sha = PluginInstallSource(
        kind=PluginInstallSourceKind.GIT,
        value="https://example.test/plugin.git",
        sha="abc123",
    )
    source_with_ref = source_with_sha.model_copy(update={"sha": "", "ref": "main"})

    dependencies = provider._dependencies_from_raw_plugin(
        {
            "dependencies": [
                "base",
                {"name": "tools", "version": "1.0.0"},
            ]
        }
    )

    assert (
        provider._version_from_raw_plugin(raw_plugin={}, source=source_with_sha)
        == "abc123"
    )
    assert (
        provider._version_from_raw_plugin(raw_plugin={}, source=source_with_ref)
        == "main"
    )
    assert (
        provider._version_from_raw_plugin(
            raw_plugin={}, source=source_with_ref.model_copy(update={"ref": ""})
        )
        == "latest"
    )
    assert [dependency.name for dependency in dependencies] == ["base", "tools"]
    assert dependencies[1].version == "1.0.0"
    with pytest.raises(ValueError, match="dependencies must be a list"):
        provider._dependencies_from_raw_plugin({"dependencies": "base"})
    with pytest.raises(ValueError, match="dependency name is required"):
        provider._dependencies_from_raw_plugin({"dependencies": [{}]})
    with pytest.raises(ValueError, match="dependency entries must be objects"):
        provider._dependencies_from_raw_plugin({"dependencies": [1]})


def test_claude_marketplace_provider_materializes_local_marketplace_paths(
    tmp_path: Path,
) -> None:
    provider = claude_marketplace_provider.ClaudeMarketplaceProvider()
    marketplace_root = tmp_path / "marketplace"
    marketplace_dir = marketplace_root / ".claude-plugin"
    marketplace_dir.mkdir(parents=True)
    marketplace_file = marketplace_dir / "marketplace.json"
    marketplace_file.write_text('{"plugins": []}', encoding="utf-8")
    plain_file = tmp_path / "plain.json"
    plain_file.write_text("{}", encoding="utf-8")

    assert (
        provider._materialize_marketplace(
            source=PluginMarketplaceSource(
                provider=PluginMarketplaceProviderKind.CLAUDE,
                value=str(marketplace_file),
            ),
            app_config_dir=tmp_path / "app",
        )
        == marketplace_root.resolve()
    )
    assert (
        provider._materialize_marketplace(
            source=PluginMarketplaceSource(
                provider=PluginMarketplaceProviderKind.CLAUDE,
                value=str(plain_file),
            ),
            app_config_dir=tmp_path / "app",
        )
        == tmp_path.resolve()
    )
    assert (
        provider._materialize_marketplace(
            source=PluginMarketplaceSource(
                provider=PluginMarketplaceProviderKind.CLAUDE,
                value=str(marketplace_root),
            ),
            app_config_dir=tmp_path / "app",
        )
        == marketplace_root.resolve()
    )


def test_claude_marketplace_provider_clones_refs_and_reports_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_git(args: list[str]) -> None:
        calls.append(args)
        if "clone" in args:
            checkout_dir = Path(args[-1])
            (checkout_dir / ".claude-plugin").mkdir(parents=True)
            (checkout_dir / ".claude-plugin" / "marketplace.json").write_text(
                '{"plugins": []}',
                encoding="utf-8",
            )

    monkeypatch.setattr(claude_marketplace_provider, "_run_git", fake_git)

    provider = claude_marketplace_provider.ClaudeMarketplaceProvider()
    provider._materialize_marketplace(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAUDE,
            name="test",
            value="owner/repo",
            ref="feature",
        ),
        app_config_dir=tmp_path / "app",
    )

    assert calls[0][:3] == ["git", "clone", "--no-checkout"]
    assert calls[1][-1] == "feature"

    def failed_git(args: list[str]) -> None:
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=args,
            stderr="clone failed",
        )

    monkeypatch.setattr(claude_marketplace_provider, "_run_git", failed_git)
    with pytest.raises(ValueError, match="clone failed"):
        provider._materialize_marketplace(
            source=PluginMarketplaceSource(
                provider=PluginMarketplaceProviderKind.CLAUDE,
                value="owner/failed",
            ),
            app_config_dir=tmp_path / "app",
        )


def test_claude_marketplace_provider_fetches_unavailable_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_git(args: list[str]) -> None:
        calls.append(args)
        if "clone" in args:
            checkout_dir = Path(args[-1])
            (checkout_dir / ".claude-plugin").mkdir(parents=True)
            (checkout_dir / ".claude-plugin" / "marketplace.json").write_text(
                '{"plugins": []}',
                encoding="utf-8",
            )
            return
        if "checkout" in args and args[-1] == "feature":
            raise subprocess.CalledProcessError(1, args, stderr="missing ref")

    monkeypatch.setattr(claude_marketplace_provider, "_run_git", fake_git)

    provider = claude_marketplace_provider.ClaudeMarketplaceProvider()
    provider._materialize_marketplace(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAUDE,
            name="test",
            value="owner/repo",
            ref="feature",
        ),
        app_config_dir=tmp_path / "app",
    )

    assert any("fetch" in call and call[-1] == "feature" for call in calls)
    assert any("checkout" in call and call[-1] == "FETCH_HEAD" for call in calls)


def test_claude_marketplace_provider_reports_read_and_path_errors(
    tmp_path: Path,
) -> None:
    provider = claude_marketplace_provider.ClaudeMarketplaceProvider()
    marketplace_root = tmp_path / "marketplace"
    marketplace_dir = marketplace_root / ".claude-plugin"
    marketplace_dir.mkdir(parents=True)
    marketplace_file = marketplace_dir / "marketplace.json"

    with pytest.raises(ValueError, match="relative path is unsafe"):
        claude_marketplace_provider._safe_relative_path("../outside")
    with pytest.raises(ValueError, match="relative path is unsafe"):
        claude_marketplace_provider._safe_relative_path(".//plugins/quality")
    with pytest.raises(ValueError, match="relative path is unsafe"):
        claude_marketplace_provider._safe_relative_path("")
    sentinel = object()
    assert claude_marketplace_provider._json_value({"items": [sentinel]}) == {
        "items": [str(sentinel)]
    }
    assert claude_marketplace_provider._git_args(["echo", "ok"]) == ["echo", "ok"]
    assert (
        claude_marketplace_provider._github_shorthand_to_url("C:/plugins")
        == "C:/plugins"
    )
    assert (
        claude_marketplace_provider._github_shorthand_to_url("owner/repo.git")
        == "https://github.com/owner/repo.git"
    )

    with pytest.raises(ValueError, match="file not found"):
        provider._read_marketplace_json(marketplace_root)
    marketplace_file.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid Claude marketplace JSON"):
        provider._read_marketplace_json(marketplace_root)
    marketplace_file.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object"):
        provider._read_marketplace_json(marketplace_root)


def test_claude_marketplace_supports_url_source_with_path(tmp_path: Path) -> None:
    marketplace_root = tmp_path / "claude-marketplace"
    _write_claude_marketplace(
        marketplace_root,
        name="atomic-agents",
        source={
            "source": "url",
            "url": "https://github.com/BrainBlend-AI/atomic-agents.git",
            "path": "claude-plugin/atomic-agents",
            "sha": "f849087b26bbb6fb5e63acb60f2b566ce874aaa7",
        },
        version="1.0.0",
    )

    index = PluginMarketplaceService().load_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAUDE,
            name="test-claude",
            value=str(marketplace_root),
        ),
        app_config_dir=tmp_path / "app",
    )
    version = index.plugins[0].versions[0]

    assert version.source.kind == PluginInstallSourceKind.GIT_SUBDIR
    assert version.source.subdir == "claude-plugin/atomic-agents"
    assert version.source.sha == "f849087b26bbb6fb5e63acb60f2b566ce874aaa7"
    assert not version.warnings
    assert not version.unsupported_reason


def test_claude_marketplace_marks_npm_source_unsupported(tmp_path: Path) -> None:
    marketplace_root = tmp_path / "claude-marketplace"
    _write_claude_marketplace(
        marketplace_root,
        name="npm-plugin",
        source={
            "source": "npm",
            "package": "@example/plugin",
        },
        version="1.0.0",
    )

    index = PluginMarketplaceService().load_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAUDE,
            name="test-claude",
            value=str(marketplace_root),
        ),
        app_config_dir=tmp_path / "app",
    )
    version = index.plugins[0].versions[0]

    assert version.source.kind == PluginInstallSourceKind.UNSUPPORTED
    assert version.source.value == "@example/plugin"
    assert version.unsupported_reason == (
        "Claude marketplace npm plugin sources are not supported"
    )


def test_claude_marketplace_warns_for_unpinned_git_source(tmp_path: Path) -> None:
    marketplace_root = tmp_path / "claude-marketplace"
    _write_claude_marketplace(
        marketplace_root,
        name="floating",
        source={
            "source": "url",
            "url": "https://github.com/example/plugin.git",
            "ref": "main",
        },
        version="1.0.0",
    )

    index = PluginMarketplaceService().load_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAUDE,
            name="test-claude",
            value=str(marketplace_root),
        ),
        app_config_dir=tmp_path / "app",
    )

    assert index.plugins[0].versions[0].warnings == (
        "Plugin source is not pinned to a commit sha.",
    )


def test_claude_plugin_adapter_normalizes_agent_front_matter(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    manifest_dir = plugin_root / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        (
            "name: atomic-agents\n"
            "description: Atomic Agents plugin\n"
            "category: development\n"
        ),
        encoding="utf-8",
    )
    agents_dir = plugin_root / "agents"
    agents_dir.mkdir(parents=True)
    agent_path = agents_dir / "atomic-explorer.md"
    agent_path.write_text(
        (
            "---\n"
            "name: atomic-explorer\n"
            "description: Explore Atomic Agents apps\n"
            "tools: Glob, Grep, Read\n"
            "---\n"
            "Prompt body\n"
        ),
        encoding="utf-8",
    )
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir(parents=True)
    hook_path = hooks_dir / "hooks.json"
    hook_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 guardrails.py",
                                    "timeout": 5000,
                                }
                            ],
                        }
                    ],
                    "PostToolUse": [
                        {
                            "matcher": "Write|Edit",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 validator.py",
                                    "timeout_seconds": 10000,
                                }
                            ],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    adapt_plugin_tree(plugin_root=plugin_root, adapter="claude")

    content = agent_path.read_text(encoding="utf-8")
    manifest_content = (manifest_dir / "plugin.json").read_text(encoding="utf-8")
    hook_content = json.loads(hook_path.read_text(encoding="utf-8"))
    assert "role_id: atomic-explorer" in content
    assert "version: 1.0.0" in content
    assert "mode: subagent" in content
    assert "tools: []" in content
    assert "category" not in manifest_content
    assert '"name": "atomic-agents"' in manifest_content
    assert hook_content["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] == 5.0
    assert (
        hook_content["hooks"]["PostToolUse"][0]["hooks"][0]["timeout_seconds"] == 10.0
    )


def test_claude_plugin_adapter_reads_manifest_declared_hook_path(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    manifest_dir = plugin_root / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "custom-hooks",
                "version": "1.0.0",
                "hooks": "./custom/hooks.json",
            }
        ),
        encoding="utf-8",
    )
    hook_path = plugin_root / "custom" / "hooks.json"
    hook_path.parent.mkdir()
    hook_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 validate.py",
                                    "timeout": 900,
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    adapt_plugin_tree(plugin_root=plugin_root, adapter="claude")

    hook_content = json.loads(hook_path.read_text(encoding="utf-8"))
    assert hook_content["hooks"]["Stop"][0]["hooks"][0]["timeout"] == 900


def test_claude_plugin_adapter_preserves_oversized_second_timeout(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    manifest_dir = plugin_root / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "oversized-timeout",
                "version": "1.0.0",
            }
        ),
        encoding="utf-8",
    )
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir()
    hook_path = hooks_dir / "hooks.json"
    hook_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 validate.py",
                                    "timeout": 1000,
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    adapt_plugin_tree(plugin_root=plugin_root, adapter="claude")

    hook_content = json.loads(hook_path.read_text(encoding="utf-8"))
    assert hook_content["hooks"]["Stop"][0]["hooks"][0]["timeout"] == 1000


def test_claude_plugin_adapter_normalizes_inline_manifest_hooks(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    manifest_dir = plugin_root / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "plugin.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "inline-hooks",
                "version": "1.0.0",
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 validate.py",
                                    "timeout": 5000,
                                }
                            ]
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    adapt_plugin_tree(plugin_root=plugin_root, adapter="claude")

    manifest_content = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_content["hooks"]["Stop"][0]["hooks"][0]["timeout"] == 5.0


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


def test_marketplace_install_without_version_skips_unsupported_latest(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    supported_root = tmp_path / "quality-v1"
    marketplace_path = tmp_path / "marketplace.json"
    _write_plugin_manifest(supported_root, name="quality", version="1.0.0")
    marketplace_path.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "quality",
                        "latest": "2.0.0",
                        "versions": [
                            {
                                "version": "2.0.0",
                                "source": {
                                    "kind": "unsupported",
                                    "value": "npm-package",
                                },
                                "unsupported_reason": "Unsupported source",
                            },
                            {
                                "version": "1.0.0",
                                "source": {
                                    "kind": "local",
                                    "value": str(supported_root.resolve()),
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
    assert installed.source.requested_version == "1.0.0"


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


def _write_claude_marketplace(
    marketplace_root: Path,
    *,
    name: str,
    source: str | dict[str, object],
    version: str,
) -> None:
    marketplace_dir = marketplace_root / ".claude-plugin"
    marketplace_dir.mkdir(parents=True, exist_ok=True)
    (marketplace_dir / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "test-claude",
                "owner": {"name": "Tests"},
                "plugins": [
                    {
                        "name": name,
                        "description": "Quality tools",
                        "version": version,
                        "source": source,
                    }
                ],
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
