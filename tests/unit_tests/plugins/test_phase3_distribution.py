# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
from typing import cast
import zipfile

import pytest

from relay_teams.plugins import config_manager as plugin_config_manager
from relay_teams.plugins import clawhub_marketplace_provider
from relay_teams.plugins import installers as plugin_installers
from relay_teams.plugins import claude_marketplace_provider
from relay_teams.env import ProxyEnvConfig
from relay_teams.plugins.claude_plugin_adapter import adapt_plugin_tree
from relay_teams.plugins.config_manager import PluginConfigManager
from relay_teams.plugins.integrity import compute_plugin_tree_sha256
from relay_teams.plugins.openclaw_plugin_adapter import (
    _directory_component_path,
    _sanitize_component_path_value,
    _sanitize_relay_manifest_value,
    adapt_openclaw_plugin_tree,
)
from relay_teams.plugins.marketplace_service import PluginMarketplaceService
from relay_teams.plugins.marketplace_models import (
    PluginMarketplaceCompatibility,
    PluginMarketplaceEntry,
    PluginMarketplaceIndex,
    PluginMarketplaceProviderKind,
    PluginMarketplaceSource,
    PluginMarketplaceVersion,
)
from relay_teams.plugins.marketplace_policy import (
    PluginMarketplaceInstallPolicy,
    _apply_install_policy_to_version,
    apply_install_policy_to_entry,
    load_plugin_marketplace_install_policy,
    save_plugin_marketplace_install_policy,
)
from relay_teams.plugins.plugin_models import (
    PluginDependency,
    PluginInstallSource,
    PluginInstallSourceKind,
    PluginScope,
    PluginStateRecord,
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


class _FakeResolvedPath:
    def __init__(self, value: str) -> None:
        self._value = value

    def expanduser(self) -> _FakeResolvedPath:
        return self

    def resolve(self) -> _FakeResolvedPath:
        return self

    def __str__(self) -> str:
        return self._value


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


def test_config_manager_removes_validation_cache_tree(tmp_path: Path) -> None:
    cache_root = tmp_path / "app" / "plugins" / "cache" / "validation"
    target = (
        cache_root
        / "plugin"
        / "skills"
        / "minimax-docx"
        / "scripts"
        / "dotnet"
        / "MiniMaxAIDocx.Core"
        / "obj"
        / "Debug"
        / "net8.0"
    )
    target.mkdir(parents=True)
    file_path = target / "build.cache"
    file_path.write_text("cached", encoding="utf-8")
    file_path.chmod(stat.S_IREAD)

    PluginConfigManager._remove_directory_under(
        parent=cache_root,
        target=cache_root / "plugin",
    )

    assert not (cache_root / "plugin").exists()


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


def test_plugin_git_subprocess_captures_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        args: list[str],
        *,
        check: bool,
        stdout: int,
        stderr: int,
        env: dict[str, str],
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        captured.update(
            {
                "args": args,
                "check": check,
                "stdout": stdout,
                "stderr": stderr,
                "env": env,
                "text": text,
                "timeout": timeout,
            }
        )
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(plugin_installers.subprocess, "run", fake_run)

    plugin_installers._run_git(["git", "status"])

    assert captured["stderr"] == subprocess.PIPE
    assert captured["text"] is True


def test_plugin_archive_proxy_map_uses_all_proxy_for_http_schemes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plugin_installers,
        "load_proxy_env_config",
        lambda: ProxyEnvConfig(all_proxy="http://proxy.example:8080"),
    )

    proxies = plugin_installers._urllib_proxy_map()

    assert proxies["http"] == "http://proxy.example:8080"
    assert proxies["https"] == "http://proxy.example:8080"
    assert proxies["all"] == "http://proxy.example:8080"


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


def test_clawhub_url_proxy_map_uses_all_proxy_for_http_schemes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        clawhub_marketplace_provider,
        "load_proxy_env_config",
        lambda: ProxyEnvConfig(all_proxy="http://proxy.example:8080"),
    )

    proxies = clawhub_marketplace_provider._urllib_proxy_map()

    assert proxies["http"] == "http://proxy.example:8080"
    assert proxies["https"] == "http://proxy.example:8080"
    assert proxies["all"] == "http://proxy.example:8080"


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


def test_marketplace_inspect_resolves_archive_without_installing(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    source_root = tmp_path / "archive-root" / "quality"
    source_root.mkdir(parents=True)
    (source_root / "openclaw.plugin.json").write_text(
        json.dumps({"id": "quality", "version": "1.0.0"}),
        encoding="utf-8",
    )
    skills_dir = source_root / "skills" / "quality"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "# Quality\n\nUse this skill for quality checks.\n",
        encoding="utf-8",
    )
    archive_path = tmp_path / "quality.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(
            source_root / "openclaw.plugin.json",
            "quality/openclaw.plugin.json",
        )
        archive.write(skills_dir / "SKILL.md", "quality/skills/quality/SKILL.md")
    marketplace_path = tmp_path / "marketplace.json"
    marketplace_path.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "quality",
                        "latest": "1.0.0",
                        "versions": [
                            {
                                "version": "1.0.0",
                                "source": {
                                    "kind": "http_archive",
                                    "value": archive_path.resolve().as_uri(),
                                    "adapter": "openclaw",
                                },
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    registry = PluginConfigManager(
        app_config_dir=app_config_dir
    ).inspect_marketplace_plugin(
        name="quality",
        marketplace=marketplace_path,
        scope=PluginScope.USER,
    )

    assert registry.plugins[0].name == "quality"
    assert registry.plugins[0].component_counts.skills == 1
    assert not get_plugin_user_state_file(app_config_dir=app_config_dir).exists()


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
    assert (
        plugin_config_manager._marketplace_provider_from_string("clawhub")
        == PluginMarketplaceProviderKind.CLAWHUB
    )
    with pytest.raises(ValueError, match="Unsupported plugin marketplace provider"):
        plugin_config_manager._marketplace_provider_from_string("unknown")


def test_clawhub_marketplace_provider_loads_entry_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_json(url: str) -> dict[str, object]:
        if url == "https://clawhub.test/api/v1/packages/market-plugin":
            return {
                "name": "market-plugin",
                "displayName": "Market",
                "summary": "Market data",
                "family": "code-plugin",
                "channel": "community",
                "latestVersion": "1.0.1",
            }
        if url == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=100":
            return {
                "items": [
                    {
                        "name": "market-plugin",
                        "displayName": "Market",
                        "summary": "Listed market data",
                        "family": "code-plugin",
                        "latestVersion": "1.0.1",
                    }
                ]
            }
        if url == "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=100":
            return {"items": []}
        if url == "https://clawhub.test/api/v1/packages/market-plugin/versions":
            return {"items": [{"version": "1.0.1"}, {"version": "1.0.0"}]}
        if url.endswith("/versions/1.0.1"):
            return {
                "version": "1.0.1",
                "family": "code-plugin",
                "artifact": {"sha256": "a" * 64},
                "runtimeExtensions": ["./dist/index.js"],
            }
        if url.endswith("/versions/1.0.0"):
            return {
                "version": "1.0.0",
                "family": "code-plugin",
                "artifact": {"npmIntegrity": "sha512-" + "b" * 88},
                "skills": ["market"],
            }
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    entry = PluginMarketplaceService().load_provider_entry(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            name="clawhub",
            value="https://clawhub.test",
        ),
        name="market-plugin",
        app_config_dir=Path("unused"),
    )

    assert entry.latest == "1.0.1"
    assert entry.description == "Market data"
    assert [version.version for version in entry.versions] == ["1.0.1", "1.0.0"]
    assert entry.versions[0].source.kind == PluginInstallSourceKind.HTTP_ARCHIVE
    assert entry.versions[0].source.adapter == "openclaw"
    assert entry.versions[0].source.sha == "a" * 64
    assert entry.versions[0].unsupported_reason == (
        "ClawHub plugin is not directly compatible with Relay Teams "
        "(compatibility=native_only): OpenClaw native runtime plugin; "
        "Relay Teams cannot execute native runtime extensions."
    )
    assert entry.versions[1].unsupported_reason == ""
    assert (
        "ClawHub package declares OpenClaw native runtime extensions; "
        "Relay Teams only loads mapped plugin components."
    ) in entry.versions[0].warnings


def test_clawhub_marketplace_provider_preserves_package_metadata_for_sparse_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_json(url: str) -> dict[str, object]:
        if url == "https://clawhub.test/api/v1/packages/fallback-plugin":
            return {
                "name": "fallback-plugin",
                "summary": "Fallback package",
                "family": "code-plugin",
                "latestVersion": "1.0.0",
                "skills": ["quality"],
            }
        if url == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=100":
            return {
                "items": [
                    {
                        "name": "fallback-plugin",
                        "summary": "Listed fallback package",
                        "family": "code-plugin",
                        "latestVersion": "1.0.0",
                        "skills": ["quality"],
                    }
                ]
            }
        if url == "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=100":
            return {"items": []}
        if url == "https://clawhub.test/api/v1/packages/fallback-plugin/versions":
            return {"items": [{"version": "1.0.0"}]}
        if url.endswith("/versions/1.0.0"):
            raise ValueError("version detail unavailable")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    entry = PluginMarketplaceService().load_provider_entry(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        name="fallback-plugin",
        app_config_dir=Path("unused"),
    )

    assert entry.versions[0].version == "1.0.0"
    assert entry.versions[0].unsupported_reason == ""


def test_clawhub_marketplace_provider_loads_lightweight_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_json(url: str) -> dict[str, object]:
        assert url.startswith("https://clawhub.test/api/v1/packages?")
        return {
            "items": [
                {
                    "name": "@owner/market-plugin",
                    "summary": "Market data",
                    "family": "code-plugin",
                    "latestVersion": "1.0.1",
                    "skills": ["market"],
                    "sha256": "a" * 64,
                }
            ]
        }

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    index = PluginMarketplaceService().load_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        app_config_dir=Path("unused"),
    )

    entry = index.get_plugin("@owner/market-plugin")
    assert entry.name == "@owner/market-plugin"
    assert entry.latest == "1.0.1"
    assert entry.versions[0].source.value == (
        "https://clawhub.test/api/v1/packages/%40owner%2Fmarket-plugin/"
        "versions/1.0.1/artifact/download"
    )


def test_clawhub_marketplace_provider_skips_missing_version_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_json(url: str) -> dict[str, object]:
        if url == "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=100":
            return {"items": []}
        assert (
            url == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=100"
        )
        return {
            "items": [
                {
                    "name": "draft-plugin",
                    "summary": "Draft package",
                    "family": "code-plugin",
                    "latestVersion": None,
                },
                {
                    "name": "market-plugin",
                    "summary": "Market data",
                    "family": "code-plugin",
                    "latestVersion": "1.0.1",
                    "skills": ["market"],
                },
            ]
        }

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    index = PluginMarketplaceService().load_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        app_config_dir=Path("unused"),
    )

    assert [entry.name for entry in index.plugins] == ["market-plugin"]


def test_clawhub_marketplace_provider_searches_packages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_json(url: str) -> dict[str, object]:
        assert url == "https://clawhub.test/api/v1/packages/search?q=market"
        return {
            "items": [
                {
                    "name": "market-plugin",
                    "summary": "Market data",
                    "family": "code-plugin",
                    "latestVersion": "1.0.1",
                    "skills": ["market"],
                }
            ]
        }

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    index = PluginMarketplaceService().search_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        query="market",
        app_config_dir=Path("unused"),
    )

    assert [entry.name for entry in index.plugins] == ["market-plugin"]
    assert index.plugins[0].latest == "1.0.1"
    assert not index.plugins[0].versions[0].unsupported_reason


def test_clawhub_marketplace_provider_search_skips_missing_version_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_json(url: str) -> dict[str, object]:
        assert url == "https://clawhub.test/api/v1/packages/search?q=market"
        return {
            "items": [
                {
                    "name": "draft-plugin",
                    "summary": "Draft package",
                    "family": "code-plugin",
                    "latestVersion": None,
                },
                {
                    "name": "market-plugin",
                    "summary": "Market data",
                    "family": "code-plugin",
                    "latestVersion": "1.0.1",
                    "skills": ["market"],
                },
            ]
        }

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    index = PluginMarketplaceService().search_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        query="market",
        app_config_dir=Path("unused"),
    )

    assert [entry.name for entry in index.plugins] == ["market-plugin"]


def test_clawhub_marketplace_provider_blank_search_loads_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    def fake_get_json(url: str) -> dict[str, object]:
        requested_urls.append(url)
        if url == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=100":
            return {"items": [{"name": "code", "version": "1.0.0"}]}
        if url == "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=100":
            return {
                "items": [
                    {"name": "bundle", "version": "1.0.0", "family": "bundle-plugin"}
                ]
            }
        if url.startswith("https://clawhub.test/api/v1/packages/code"):
            raise ValueError("detail unavailable")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    index = PluginMarketplaceService().search_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        query="   ",
        app_config_dir=Path("unused"),
    )

    assert [entry.name for entry in index.plugins] == ["bundle"]
    assert all("/search" not in url for url in requested_urls)
    assert requested_urls == [
        "https://clawhub.test/api/v1/packages?family=code-plugin&limit=100",
        "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=100",
    ]


def test_marketplace_service_search_filters_local_json(tmp_path: Path) -> None:
    marketplace_path = tmp_path / "marketplace.json"
    marketplace_path.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "quality",
                        "description": "Quality tools",
                        "latest": "1.0.0",
                    },
                    {
                        "name": "market",
                        "description": "Market data",
                        "latest": "1.0.0",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    index = PluginMarketplaceService().search_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.LOCAL_JSON,
            value=str(marketplace_path),
        ),
        query="quality",
        app_config_dir=Path("unused"),
    )

    assert [entry.name for entry in index.plugins] == ["quality"]


def test_clawhub_marketplace_provider_uses_requested_name_for_nameless_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_json(url: str) -> dict[str, object]:
        if url == "https://clawhub.test/api/v1/packages/detail-plugin":
            return {
                "summary": "Detail package",
                "latestVersion": "1.0.0",
                "family": "bundle-plugin",
                "sha256": "a" * 64,
            }
        if url == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=100":
            return {"items": []}
        if url == "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=100":
            return {
                "items": [
                    {
                        "name": "detail-plugin",
                        "summary": "Listed detail package",
                        "latestVersion": "1.0.0",
                        "family": "bundle-plugin",
                    }
                ]
            }
        if url == "https://clawhub.test/api/v1/packages/detail-plugin/versions":
            return {"items": []}
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    entry = PluginMarketplaceService().load_provider_entry(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        name="detail-plugin",
        app_config_dir=Path("unused"),
    )

    assert entry.name == "detail-plugin"
    assert entry.latest == "1.0.0"
    assert entry.versions[0].source.value == (
        "https://clawhub.test/api/v1/packages/detail-plugin/"
        "versions/1.0.0/artifact/download"
    )
    assert entry.versions[0].source.sha == "a" * 64


def test_clawhub_marketplace_provider_uses_valid_detail_when_listing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_json(url: str) -> dict[str, object]:
        if url == "https://clawhub.test/api/v1/packages/detail-plugin":
            return {
                "name": "detail-plugin",
                "summary": "Detail package",
                "latestVersion": "1.0.0",
                "family": "bundle-plugin",
                "sha256": "a" * 64,
            }
        if url == "https://clawhub.test/api/v1/packages/detail-plugin/versions":
            return {"items": []}
        if url.startswith("https://clawhub.test/api/v1/packages?family="):
            raise ValueError("listing unavailable")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    entry = PluginMarketplaceService().load_provider_entry(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        name="detail-plugin",
        app_config_dir=Path("unused"),
    )

    assert entry.name == "detail-plugin"
    assert entry.latest == "1.0.0"
    assert entry.versions[0].unsupported_reason == ""


def test_clawhub_marketplace_provider_merges_listing_metadata_for_sparse_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_json(url: str) -> dict[str, object]:
        if url == "https://clawhub.test/api/v1/packages/detail-plugin":
            return {
                "summary": "Detail package",
                "family": "bundle-plugin",
                "sha256": "a" * 64,
            }
        if url == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=100":
            return {"items": []}
        if url == "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=100":
            return {
                "items": [
                    {
                        "name": "detail-plugin",
                        "latestVersion": "1.0.0",
                        "summary": "Listed package",
                        "family": "bundle-plugin",
                    }
                ]
            }
        if url == "https://clawhub.test/api/v1/packages/detail-plugin/versions":
            return {"items": []}
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    entry = PluginMarketplaceService().load_provider_entry(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        name="detail-plugin",
        app_config_dir=Path("unused"),
    )

    assert entry.name == "detail-plugin"
    assert entry.latest == "1.0.0"
    assert entry.description == "Detail package"
    assert entry.versions[0].source.sha == "a" * 64


def test_clawhub_marketplace_provider_preserves_listing_compatibility_for_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_json(url: str) -> dict[str, object]:
        if url == "https://clawhub.test/api/v1/packages/detail-plugin":
            return {
                "name": "detail-plugin",
                "version": "1.0.0",
                "summary": "Detail package",
            }
        if url == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=100":
            return {
                "items": [
                    {
                        "name": "detail-plugin",
                        "latestVersion": "1.0.0",
                        "summary": "Listed package",
                        "family": "bundle-plugin",
                    }
                ]
            }
        if url == "https://clawhub.test/api/v1/packages/detail-plugin/versions":
            return {"items": [{"version": "1.0.0"}]}
        if url == "https://clawhub.test/api/v1/packages/detail-plugin/versions/1.0.0":
            return {"sha256": "a" * 64}
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    entry = PluginMarketplaceService().load_provider_entry(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        name="detail-plugin",
        app_config_dir=Path("unused"),
    )

    assert entry.compatibility.value == "direct"
    assert entry.versions[0].unsupported_reason == ""
    assert entry.versions[0].source.sha == "a" * 64


def test_clawhub_marketplace_provider_does_not_copy_native_metadata_on_detail_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_json(url: str) -> dict[str, object]:
        if url == "https://clawhub.test/api/v1/packages/native-plugin":
            return {
                "name": "native-plugin",
                "summary": "Native package",
                "latestVersion": "2.0.0",
                "family": "code-plugin",
                "runtimeExtensions": ["./dist/index.js"],
            }
        if url == "https://clawhub.test/api/v1/packages/native-plugin/versions":
            return {"items": [{"version": "1.0.0"}]}
        if url == "https://clawhub.test/api/v1/packages/native-plugin/versions/1.0.0":
            raise ValueError("version detail unavailable")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    entry = PluginMarketplaceService().load_provider_entry(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        name="native-plugin",
        app_config_dir=Path("unused"),
    )

    assert entry.versions[0].version == "1.0.0"
    assert "native_only" not in entry.versions[0].unsupported_reason
    assert not any(
        "native runtime extensions" in warning for warning in entry.versions[0].warnings
    )


def test_clawhub_marketplace_provider_falls_back_to_listing_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_json(url: str) -> dict[str, object]:
        if url.startswith("https://clawhub.test/api/v1/packages?"):
            return {
                "items": [
                    {
                        "name": "market-plugin",
                        "summary": "Market data",
                        "family": "code-plugin",
                        "version": "1.0.1",
                        "channel": "community",
                        "artifactKind": "legacy-zip",
                        "executesCode": True,
                        "scanStatus": "clean",
                        "sha256": "a" * 64,
                    }
                ]
            }
        raise ValueError("versions unavailable")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    entry = PluginMarketplaceService().load_provider_entry(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        name="market-plugin",
        app_config_dir=Path("unused"),
    )

    assert entry.latest == "1.0.1"
    assert entry.description == "Market data"
    assert entry.versions[0].source.kind == PluginInstallSourceKind.HTTP_ARCHIVE
    assert entry.versions[0].source.adapter == "openclaw"
    assert entry.versions[0].source.sha == "a" * 64
    assert "ClawHub package executes code." in entry.versions[0].warnings


def test_clawhub_marketplace_provider_paginates_package_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_json(url: str) -> dict[str, object]:
        if url == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=100":
            return {
                "items": [{"name": "first", "version": "1.0.0", "skills": ["first"]}],
                "nextCursor": "next",
            }
        if (
            url
            == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=100&cursor=next"
        ):
            return {"items": [{"name": "second", "version": "1.0.0"}]}
        if url.startswith("https://clawhub.test/api/v1/packages/second"):
            raise ValueError("detail unavailable")
        if url == "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=100":
            return {
                "items": [
                    {"name": "bundle", "version": "2.0.0", "family": "bundle-plugin"}
                ]
            }
        if (
            url.endswith("/versions")
            or url.startswith("https://clawhub.test/api/v1/packages/first")
            or url.startswith("https://clawhub.test/api/v1/packages/second")
        ):
            return {"items": []}
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    index = PluginMarketplaceService().load_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        app_config_dir=Path("unused"),
    )

    assert [entry.name for entry in index.plugins] == ["first", "bundle"]
    assert index.plugins[1].provider_family == "bundle-plugin"
    assert index.plugins[1].compatibility == "direct"


def test_clawhub_marketplace_provider_decodes_family_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    def fake_get_json(url: str) -> dict[str, object]:
        requested_urls.append(url)
        if (
            url
            == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=4&cursor=code-next"
        ):
            return {"items": [{"name": "code", "version": "1.0.0"}]}
        if (
            url
            == "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=3&cursor=bundle-next"
        ):
            return {"items": [{"name": "bundle", "version": "1.0.0"}]}
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    index = clawhub_marketplace_provider.ClawHubMarketplaceProvider().load_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        limit=4,
        cursor='{"code-plugin":"code-next","bundle-plugin":"bundle-next"}',
        fetch_all=False,
    )

    assert [entry.name for entry in index.plugins] == ["code", "bundle"]
    assert len(requested_urls) == 2


def test_clawhub_marketplace_provider_skips_uncursored_families_on_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    def fake_get_json(url: str) -> dict[str, object]:
        requested_urls.append(url)
        if (
            url
            == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=4&cursor=code-next"
        ):
            return {"items": [{"name": "code", "version": "1.0.0"}]}
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    index = clawhub_marketplace_provider.ClawHubMarketplaceProvider().load_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        limit=4,
        cursor='{"code-plugin":"code-next"}',
        fetch_all=False,
    )

    assert [entry.name for entry in index.plugins] == ["code"]
    assert requested_urls == [
        "https://clawhub.test/api/v1/packages?family=code-plugin&limit=4&cursor=code-next"
    ]


def test_clawhub_marketplace_provider_limits_single_page_globally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    def fake_get_json(url: str) -> dict[str, object]:
        requested_urls.append(url)
        if url == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=1":
            return {
                "items": [{"name": "code", "version": "1.0.0"}],
                "nextCursor": "code-next",
            }
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    index = clawhub_marketplace_provider.ClawHubMarketplaceProvider().load_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        limit=1,
        fetch_all=False,
    )

    assert [entry.name for entry in index.plugins] == ["code"]
    assert index.next_cursor == '{"code-plugin":"code-next","bundle-plugin":""}'
    assert requested_urls == [
        "https://clawhub.test/api/v1/packages?family=code-plugin&limit=1"
    ]


def test_clawhub_marketplace_provider_allows_max_size_single_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    def fake_get_json(url: str) -> dict[str, object]:
        requested_urls.append(url)
        if url == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=100":
            return {"items": [], "nextCursor": "code-next"}
        if url == "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=100":
            return {"items": []}
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    index = clawhub_marketplace_provider.ClawHubMarketplaceProvider().load_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        limit=100,
        fetch_all=False,
    )

    assert index.plugins == ()
    assert index.next_cursor == '{"code-plugin":"code-next"}'
    assert requested_urls == [
        "https://clawhub.test/api/v1/packages?family=code-plugin&limit=100",
        "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=100",
    ]


def test_clawhub_marketplace_provider_loads_one_detailed_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    def fake_get_json(url: str) -> dict[str, object]:
        requested_urls.append(url)
        if url == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=1":
            return {
                "items": [{"name": "first", "latestVersion": "1.0.0"}],
                "nextCursor": "next",
            }
        if url == "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=1":
            return {"items": []}
        if url == "https://clawhub.test/api/v1/packages/first/versions/1.0.0":
            return {
                "version": "1.0.0",
                "family": "code-plugin",
                "scanStatus": "clean",
                "artifact": {"sha256": "a" * 64},
                "skills": ["first"],
            }
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    index = clawhub_marketplace_provider.ClawHubMarketplaceProvider().load_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        limit=1,
        include_versions=True,
        fetch_all=False,
    )

    assert [entry.name for entry in index.plugins] == ["first"]
    assert index.next_cursor == '{"code-plugin":"next","bundle-plugin":""}'
    assert index.plugins[0].versions[0].source.sha == "a" * 64
    assert not index.plugins[0].versions[0].unsupported_reason
    assert "https://clawhub.test/api/v1/packages/first/versions" not in requested_urls


def test_clawhub_marketplace_provider_visits_pending_family_before_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    def fake_get_json(url: str) -> dict[str, object]:
        requested_urls.append(url)
        if url == "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=1":
            return {"items": [{"name": "bundle", "version": "1.0.0"}]}
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    index = clawhub_marketplace_provider.ClawHubMarketplaceProvider().load_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        limit=1,
        cursor='{"code-plugin":"code-next","bundle-plugin":""}',
        fetch_all=False,
    )

    assert [entry.name for entry in index.plugins] == ["bundle"]
    assert index.next_cursor == '{"code-plugin":"code-next"}'
    assert requested_urls == [
        "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=1"
    ]


def test_clawhub_marketplace_service_fetches_all_detailed_pages_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    def fake_get_json(url: str) -> dict[str, object]:
        requested_urls.append(url)
        if url == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=1":
            return {
                "items": [{"name": "first", "latestVersion": "1.0.0"}],
                "nextCursor": "next",
            }
        if (
            url
            == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=1&cursor=next"
        ):
            return {"items": [{"name": "second", "latestVersion": "1.1.0"}]}
        if url == "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=1":
            return {"items": []}
        if url == "https://clawhub.test/api/v1/packages/first/versions/1.0.0":
            return {
                "version": "1.0.0",
                "family": "code-plugin",
                "scanStatus": "clean",
                "artifact": {"sha256": "a" * 64},
                "skills": ["first"],
            }
        if url == "https://clawhub.test/api/v1/packages/second/versions/1.1.0":
            return {
                "version": "1.1.0",
                "family": "code-plugin",
                "scanStatus": "clean",
                "artifact": {"sha256": "b" * 64},
            }
        if url.startswith("https://clawhub.test/api/v1/packages/second"):
            raise ValueError("detail unavailable")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    index = PluginMarketplaceService().load_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        app_config_dir=Path("unused"),
        limit=1,
        include_details=True,
    )

    assert [entry.name for entry in index.plugins] == ["first"]
    assert index.next_cursor == ""
    assert requested_urls == [
        "https://clawhub.test/api/v1/packages?family=code-plugin&limit=1",
        "https://clawhub.test/api/v1/packages?family=code-plugin&limit=1&cursor=next",
        "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=1",
        "https://clawhub.test/api/v1/packages/first/versions/1.0.0",
        "https://clawhub.test/api/v1/packages/second/versions/1.1.0",
        "https://clawhub.test/api/v1/packages/second",
    ]


def test_clawhub_marketplace_service_preserves_pagination_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    def fake_get_json(url: str) -> dict[str, object]:
        requested_urls.append(url)
        if (
            url
            == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=1&cursor=stale"
        ):
            return {"items": [{"name": "second", "version": "1.1.0"}]}
        if url.startswith("https://clawhub.test/api/v1/packages/second"):
            raise ValueError("detail unavailable")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    index = PluginMarketplaceService().load_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        app_config_dir=Path("unused"),
        limit=1,
        cursor='{"code-plugin":"stale"}',
        fetch_all=False,
    )

    assert [entry.name for entry in index.plugins] == []
    assert index.next_cursor == ""
    assert requested_urls == [
        "https://clawhub.test/api/v1/packages?family=code-plugin&limit=1&cursor=stale",
    ]


def test_clawhub_marketplace_lightweight_index_filters_to_installable_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_json(url: str) -> dict[str, object]:
        if url == "https://clawhub.test/api/v1/packages?family=code-plugin&limit=50":
            return {
                "items": [
                    {
                        "name": "needs-details",
                        "latestVersion": "1.0.0",
                        "family": "code-plugin",
                    },
                    {
                        "name": "native-with-old-direct",
                        "latestVersion": "2.0.0",
                        "family": "code-plugin",
                        "runtimeExtensions": ["./dist/index.js"],
                    },
                ]
            }
        if url == "https://clawhub.test/api/v1/packages?family=bundle-plugin&limit=50":
            return {
                "items": [
                    {
                        "name": "bundle",
                        "latestVersion": "1.0.0",
                        "family": "bundle-plugin",
                        "capabilities": {"bundleFormat": "generic"},
                    }
                ]
            }
        if url == "https://clawhub.test/api/v1/packages/bundle/versions/1.0.0":
            return {
                "version": "1.0.0",
                "family": "bundle-plugin",
                "capabilities": {"bundleFormat": "generic"},
            }
        if url == "https://clawhub.test/api/v1/packages/needs-details":
            return {
                "name": "needs-details",
                "family": "code-plugin",
                "skills": ["quality"],
            }
        if url == "https://clawhub.test/api/v1/packages/needs-details/versions":
            return {"items": [{"version": "1.0.0"}]}
        if url == "https://clawhub.test/api/v1/packages/needs-details/versions/1.0.0":
            return {
                "version": "1.0.0",
                "family": "code-plugin",
                "skills": ["quality"],
            }
        if url == "https://clawhub.test/api/v1/packages/native-with-old-direct":
            return {
                "name": "native-with-old-direct",
                "latestVersion": "2.0.0",
                "family": "code-plugin",
                "runtimeExtensions": ["./dist/index.js"],
            }
        if (
            url
            == "https://clawhub.test/api/v1/packages/native-with-old-direct/versions"
        ):
            return {"items": [{"version": "2.0.0"}, {"version": "1.0.0"}]}
        if (
            url
            == "https://clawhub.test/api/v1/packages/native-with-old-direct/versions/2.0.0"
        ):
            return {
                "version": "2.0.0",
                "family": "code-plugin",
                "runtimeExtensions": ["./dist/index.js"],
            }
        if (
            url
            == "https://clawhub.test/api/v1/packages/native-with-old-direct/versions/1.0.0"
        ):
            return {
                "version": "1.0.0",
                "family": "code-plugin",
                "skills": ["quality"],
            }
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(clawhub_marketplace_provider, "_get_json", fake_get_json)

    index = PluginMarketplaceService().load_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        app_config_dir=Path("unused"),
        limit=50,
        include_details=True,
    )

    assert [entry.name for entry in index.plugins] == [
        "needs-details",
        "native-with-old-direct",
        "bundle",
    ]
    assert all(entry.supported_versions() for entry in index.plugins)
    assert index.plugins[0].compatibility == "direct"
    assert index.plugins[1].supported_versions()[0].version == "1.0.0"
    assert index.plugins[1].supported_versions()[0].unsupported_reason == ""
    assert index.plugins[2].compatibility == "direct"


def test_clawhub_marketplace_provider_marks_blocked_releases_unsupported() -> None:
    provider = clawhub_marketplace_provider.ClawHubMarketplaceProvider()

    entry = provider._entry_from_raw_package(
        raw_package={
            "name": "blocked-plugin",
            "family": "code-plugin",
            "version": "1.0.0",
            "moderationState": "quarantined",
        },
        base_url="https://clawhub.test",
    )

    assert entry.supported_versions() == ()
    assert entry.versions[0].unsupported_reason == (
        "ClawHub package release is quarantined"
    )


def test_clawhub_marketplace_provider_marks_placeholder_bundle_format_unsupported() -> (
    None
):
    provider = clawhub_marketplace_provider.ClawHubMarketplaceProvider()

    entry = provider._entry_from_raw_package(
        raw_package={
            "name": "placeholder-bundle",
            "family": "bundle-plugin",
            "version": "1.0.0",
            "capabilityTags": ["format:Bundle format"],
        },
        base_url="https://clawhub.test",
    )

    assert entry.supported_versions() == ()
    assert entry.versions[0].unsupported_reason == (
        "ClawHub bundle plugin does not declare a concrete bundle format"
    )

    unsupported_format = provider._entry_from_raw_package(
        raw_package={
            "name": "codex-bundle",
            "family": "bundle-plugin",
            "version": "1.0.0",
            "capabilities": {"bundleFormat": "codex"},
        },
        base_url="https://clawhub.test",
    )
    invalid_host = provider._entry_from_raw_package(
        raw_package={
            "name": "invalid-host",
            "family": "bundle-plugin",
            "version": "1.0.0",
            "capabilities": {
                "bundleFormat": "generic",
                "capabilityTags": ['host:"main": "./dist/plugin.js"'],
            },
        },
        base_url="https://clawhub.test",
    )

    assert unsupported_format.supported_versions() == ()
    assert unsupported_format.versions[0].unsupported_reason == (
        "Unsupported ClawHub bundle format: codex"
    )
    assert invalid_host.supported_versions() == ()
    assert invalid_host.versions[0].unsupported_reason == (
        "ClawHub bundle plugin host target metadata is invalid"
    )

    known_unmappable = provider._entry_from_raw_package(
        raw_package={
            "name": "kdp-author-engine-bundle",
            "family": "bundle-plugin",
            "version": "1.0.0",
            "capabilities": {"bundleFormat": "generic"},
        },
        base_url="https://clawhub.test",
    )

    assert known_unmappable.supported_versions() == ()
    assert known_unmappable.versions[0].unsupported_reason == (
        "ClawHub package artifact does not contain Relay Teams mappable plugin components"
    )
    version_without_name = provider._version_from_raw_package(
        base_url="https://clawhub.test",
        name="kdp-author-engine-bundle",
        version="1.0.0",
        raw_version={
            "version": "1.0.0",
            "family": "bundle-plugin",
            "capabilities": {"bundleFormat": "generic"},
        },
    )
    assert version_without_name.unsupported_reason == (
        "ClawHub package artifact does not contain Relay Teams mappable plugin components"
    )
    type_only_bundle = provider._version_from_raw_package(
        base_url="https://clawhub.test",
        name="codex-bundle",
        version="1.0.0",
        raw_version={
            "version": "1.0.0",
            "type": "bundle-plugin",
            "capabilities": {"bundleFormat": "codex"},
        },
    )
    assert (
        type_only_bundle.unsupported_reason
        == "Unsupported ClawHub bundle format: codex"
    )
    detail_with_family = clawhub_marketplace_provider._version_detail_with_fallback(
        fallback_package={
            "name": "codex-bundle",
            "family": "bundle-plugin",
            "runtimeExtensions": ["./dist/index.js"],
        },
        raw_version={"version": "1.0.0"},
        raw_detail={"capabilities": {"bundleFormat": "codex"}},
    )
    assert detail_with_family["family"] == "bundle-plugin"
    assert "runtimeExtensions" not in detail_with_family
    sparse_direct_detail = clawhub_marketplace_provider._version_detail_with_fallback(
        fallback_package={
            "name": "quality-helper",
            "family": "code-plugin",
            "skills": ["quality"],
        },
        raw_version={"version": "1.0.0"},
        raw_detail={"sha256": "abc"},
    )
    assert sparse_direct_detail["skills"] == ["quality"]
    failed_lookup_detail_with_family = (
        clawhub_marketplace_provider._version_detail_after_failed_lookup(
            fallback_package={
                "name": "codex-bundle",
                "family": "bundle-plugin",
            },
            raw_version={
                "version": "1.0.0",
                "capabilities": {"bundleFormat": "codex"},
            },
        )
    )
    assert failed_lookup_detail_with_family["family"] == "bundle-plugin"


def test_clawhub_marketplace_provider_helper_edge_cases() -> None:
    latest_from_mapping = clawhub_marketplace_provider._package_version_or_empty(
        {"latestVersion": {"version": "2.0.0"}}
    )
    latest_from_tag = clawhub_marketplace_provider._package_version_or_empty(
        {"tags": {"latest": "3.0.0"}}
    )
    compatibility = clawhub_marketplace_provider._compatibility_for_package(
        {"skills": ["quality"], "runtimeExtensions": ["./dist/index.js"]}
    )
    native_only = clawhub_marketplace_provider._compatibility_for_package(
        {"runtimeExtensions": ["./dist/index.js"]}
    )
    digest = clawhub_marketplace_provider._artifact_digest(
        {"artifact": {"sha256": "a" * 64}}
    )
    cursors = clawhub_marketplace_provider._decode_family_cursors("legacy")
    encoded = clawhub_marketplace_provider._encode_family_cursors(
        {"code-plugin": "next", "other": "ignored"}
    )
    parsed_sri = clawhub_marketplace_provider.parse_sri_digest(
        "sha256-" + base64.b64encode(b"abc").decode("ascii")
    )
    json_value = clawhub_marketplace_provider._json_value(
        {"items": [object()], "enabled": True}
    )
    description = clawhub_marketplace_provider._package_description(
        {"description": "Longer description"}
    )
    family = clawhub_marketplace_provider._package_family({"type": "bundle-plugin"})
    bundled = clawhub_marketplace_provider._compatibility_for_package(
        {"type": "bundle-plugin"}
    )
    manifest_capability = clawhub_marketplace_provider._compatibility_for_package(
        {"manifest": {"capabilities": {"commands": ["check"]}}}
    )
    nested_bundle_format = clawhub_marketplace_provider._bundle_format(
        {"capabilities": {"capabilityTags": ["format: relay teams"]}}
    )
    manifest_bundle_format = clawhub_marketplace_provider._bundle_format(
        {"manifest": {"capabilities": {"bundleFormat": "codex"}}}
    )
    manifest_bundle_format_after_empty_capabilities = (
        clawhub_marketplace_provider._bundle_format(
            {
                "capabilities": {},
                "manifest": {"capabilities": {"bundleFormat": "codex"}},
            }
        )
    )
    manifest_bundle_format_after_empty_tag = (
        clawhub_marketplace_provider._bundle_format(
            {
                "capabilityTags": ["format:"],
                "manifest": {"capabilities": {"bundleFormat": "codex"}},
            }
        )
    )
    invalid_host_tag = clawhub_marketplace_provider._has_invalid_bundle_host_targets(
        {"capabilityTags": ["host: claude:desktop"]}
    )
    invalid_nested_host = clawhub_marketplace_provider._has_invalid_bundle_host_targets(
        {"capabilities": {"hostTargets": ["claude:desktop"]}}
    )
    invalid_manifest_host = (
        clawhub_marketplace_provider._has_invalid_bundle_host_targets(
            {"manifest": {"capabilities": {"capabilityTags": ['host:"main"']}}}
        )
    )
    invalid_manifest_host_after_empty_capabilities = (
        clawhub_marketplace_provider._has_invalid_bundle_host_targets(
            {
                "capabilities": {},
                "manifest": {"capabilities": {"hostTargets": ["claude:desktop"]}},
            }
        )
    )
    nested_mappable_metadata = (
        clawhub_marketplace_provider._has_mappable_component_metadata(
            {"capabilities": {"commands": ["setup"]}}
        )
    )
    warnings = clawhub_marketplace_provider._warnings_for_package(
        {
            "scanStatus": "pending",
            "artifactKind": "legacy-zip",
            "manifest": {"compatibility": {"openclaw": "1"}},
        },
        digest="abc",
    )
    blocked_reason = clawhub_marketplace_provider._unsupported_reason(
        {"blockedFromDownload": True}
    )
    unknown_family_reason = clawhub_marketplace_provider._unsupported_reason(
        {"family": "native-extension"}
    )
    empty_decoded = clawhub_marketplace_provider._decode_family_cursors("[]")

    assert latest_from_mapping == "2.0.0"
    assert latest_from_tag == "3.0.0"
    assert compatibility[0] == "partial"
    assert native_only[0] == "native_only"
    assert digest == "a" * 64
    assert cursors == {"code-plugin": "legacy"}
    assert encoded == '{"code-plugin":"next"}'
    assert parsed_sri == ("sha256", b"abc".hex())
    assert isinstance(json_value, dict)
    assert json_value["enabled"] is True
    assert isinstance(json_value["items"], list)
    assert description == "Longer description"
    assert family == "bundle-plugin"
    assert bundled[0] == "direct"
    assert manifest_capability[0] == "direct"
    assert nested_bundle_format == "relay teams"
    assert manifest_bundle_format == "codex"
    assert manifest_bundle_format_after_empty_capabilities == "codex"
    assert manifest_bundle_format_after_empty_tag == "codex"
    assert invalid_host_tag is True
    assert invalid_nested_host is True
    assert invalid_manifest_host is True
    assert invalid_manifest_host_after_empty_capabilities is True
    assert nested_mappable_metadata is True
    assert warnings == (
        "ClawHub scan status is pending.",
        "ClawHub package uses a legacy ZIP artifact.",
        "ClawHub package declares OpenClaw compatibility metadata; "
        "Relay Teams does not execute OpenClaw native plugin APIs.",
    )
    assert blocked_reason == "ClawHub package release is blocked from download"
    assert unknown_family_reason == (
        "Unsupported ClawHub package family: native-extension"
    )
    assert empty_decoded == {"code-plugin": "[]"}
    assert clawhub_marketplace_provider._warnings_for_package({}, digest="abc") == (
        "ClawHub scan status is missing.",
    )


def test_clawhub_marketplace_provider_validates_bad_payloads() -> None:
    with pytest.raises(ValueError, match="field must be a list"):
        clawhub_marketplace_provider._object_list_field({"items": "bad"}, "items")
    with pytest.raises(ValueError, match="package entries must be objects"):
        clawhub_marketplace_provider._object_list_field({"items": ["bad"]}, "items")
    with pytest.raises(ValueError, match="field is required: name"):
        clawhub_marketplace_provider._required_string({"name": ""}, "name")
    with pytest.raises(ValueError, match="field is required: name"):
        clawhub_marketplace_provider._package_name_or_fallback({})
    with pytest.raises(ValueError, match="version is required"):
        clawhub_marketplace_provider._package_version({})
    assert clawhub_marketplace_provider.parse_sri_digest("not-sri") is None


def test_marketplace_install_rejects_unsupported_provider_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_load_provider_entry(
        self: PluginMarketplaceService,
        *,
        source: PluginMarketplaceSource,
        name: str,
        app_config_dir: Path,
        install_policy: PluginMarketplaceInstallPolicy | None = None,
    ) -> PluginMarketplaceEntry:
        _ = self
        _ = source
        _ = name
        _ = app_config_dir
        _ = install_policy
        return PluginMarketplaceEntry(
            name="blocked-plugin",
            latest="1.0.0",
            versions=(
                PluginMarketplaceVersion(
                    version="1.0.0",
                    source=PluginInstallSource(
                        kind=PluginInstallSourceKind.HTTP_ARCHIVE,
                        value="https://clawhub.test/archive.zip",
                    ),
                    unsupported_reason="ClawHub package release is quarantined",
                ),
            ),
        )

    monkeypatch.setattr(
        PluginMarketplaceService,
        "load_provider_entry",
        fake_load_provider_entry,
    )

    with pytest.raises(ValueError, match="release is quarantined"):
        PluginConfigManager(app_config_dir=tmp_path / "app").install_marketplace_plugin(
            name="blocked-plugin",
            marketplace=Path("clawhub"),
            marketplace_provider=PluginMarketplaceProviderKind.CLAWHUB,
            scope=PluginScope.USER,
        )


def test_clawhub_install_policy_allows_high_risk_versions_with_warnings() -> None:
    entry = PluginMarketplaceEntry(
        name="risky-plugin",
        latest="1.0.0",
        compatibility=PluginMarketplaceCompatibility.DIRECT,
        versions=(
            PluginMarketplaceVersion(
                version="1.0.0",
                source=PluginInstallSource(
                    kind=PluginInstallSourceKind.HTTP_ARCHIVE,
                    value="https://clawhub.test/archive.zip",
                    sha="",
                ),
                warnings=(
                    "ClawHub package channel is community; review before install.",
                    "ClawHub package executes code.",
                    "ClawHub scan status is pending.",
                    "ClawHub package artifact has no digest metadata.",
                ),
            ),
        ),
    )

    policy_entry = apply_install_policy_to_entry(
        entry=entry,
        provider=PluginMarketplaceProviderKind.CLAWHUB,
        policy=PluginMarketplaceInstallPolicy(),
    )

    assert policy_entry.supported_versions() == policy_entry.versions
    assert policy_entry.versions[0].warnings == entry.versions[0].warnings
    assert policy_entry.versions[0].unsupported_reason == ""
    PluginMarketplaceInstallPolicy().require_allowed(
        provider=PluginMarketplaceProviderKind.CLAWHUB,
        version=entry.versions[0],
    )

    relaxed = PluginMarketplaceInstallPolicy().with_overrides(
        allow_community_plugins=True,
        allow_executes_code=True,
        allow_missing_digest=True,
        allow_unclean_scan=True,
    )
    relaxed.require_allowed(
        provider=PluginMarketplaceProviderKind.CLAWHUB,
        version=entry.versions[0],
    )


def test_clawhub_install_policy_blocks_non_direct_plugins() -> None:
    entry = PluginMarketplaceEntry(
        name="native-plugin",
        latest="1.0.0",
        compatibility=PluginMarketplaceCompatibility.NATIVE_ONLY,
        compatibility_reason="OpenClaw native runtime plugin",
        versions=(
            PluginMarketplaceVersion(
                version="1.0.0",
                source=PluginInstallSource(
                    kind=PluginInstallSourceKind.HTTP_ARCHIVE,
                    value="https://clawhub.test/archive.zip",
                    sha="",
                ),
            ),
        ),
    )

    policy_entry = apply_install_policy_to_entry(
        entry=entry,
        provider=PluginMarketplaceProviderKind.CLAWHUB,
        policy=PluginMarketplaceInstallPolicy(),
    )

    assert policy_entry.supported_versions() == policy_entry.versions
    assert policy_entry.versions[0].unsupported_reason == ""
    with pytest.raises(
        ValueError,
        match="ClawHub plugin is not directly compatible with Relay Teams",
    ):
        PluginMarketplaceInstallPolicy().require_entry_allowed(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            entry=entry,
        )


def test_clawhub_install_policy_config_loads_from_plugin_config(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    policy = PluginMarketplaceInstallPolicy(
        allow_community_plugins=True,
        allow_executes_code=True,
        require_digest=False,
        allow_unclean_scan=True,
    )

    save_plugin_marketplace_install_policy(
        app_config_dir=app_config_dir,
        policy=policy,
    )

    assert load_plugin_marketplace_install_policy(app_config_dir) == policy


def test_clawhub_install_policy_config_uses_default_when_missing(
    tmp_path: Path,
) -> None:
    assert load_plugin_marketplace_install_policy(tmp_path / "app") == (
        PluginMarketplaceInstallPolicy()
    )


def test_clawhub_install_policy_config_rejects_invalid_payloads(
    tmp_path: Path,
) -> None:
    policy_file = tmp_path / "app" / "plugins" / "marketplace-policy.json"
    policy_file.parent.mkdir(parents=True)

    policy_file.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid plugin marketplace policy JSON"):
        load_plugin_marketplace_install_policy(tmp_path / "app")

    policy_file.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="marketplace policy JSON must be an object"):
        load_plugin_marketplace_install_policy(tmp_path / "app")

    policy_file.write_text(
        json.dumps({"allow_community_plugins": []}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Invalid plugin marketplace policy"):
        load_plugin_marketplace_install_policy(tmp_path / "app")


def test_clawhub_install_policy_leaves_unrelated_versions_unchanged() -> None:
    version = PluginMarketplaceVersion(
        version="1.0.0",
        source=PluginInstallSource(kind=PluginInstallSourceKind.GIT, value="repo"),
    )

    assert (
        _apply_install_policy_to_version(
            version=version,
            provider=PluginMarketplaceProviderKind.CLAUDE,
            policy=PluginMarketplaceInstallPolicy(),
        )
        is version
    )


def test_clawhub_marketplace_service_preserves_paged_request_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_load_index(
        self: clawhub_marketplace_provider.ClawHubMarketplaceProvider,
        *,
        source: PluginMarketplaceSource,
        limit: int = 100,
        cursor: str = "",
        fetch_all: bool = True,
        include_versions: bool = False,
    ) -> PluginMarketplaceIndex:
        _ = (self, source, limit, include_versions)
        captured["cursor"] = cursor
        captured["fetch_all"] = fetch_all
        return PluginMarketplaceIndex(plugins=())

    monkeypatch.setattr(
        clawhub_marketplace_provider.ClawHubMarketplaceProvider,
        "load_index",
        fake_load_index,
    )

    PluginMarketplaceService().load_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        app_config_dir=tmp_path / "app",
        cursor='{"code-plugin":"next"}',
    )

    assert captured == {
        "cursor": '{"code-plugin":"next"}',
        "fetch_all": False,
    }


def test_clawhub_marketplace_service_preserves_partial_page_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_load_index(
        self: clawhub_marketplace_provider.ClawHubMarketplaceProvider,
        *,
        source: PluginMarketplaceSource,
        limit: int = 100,
        cursor: str = "",
        fetch_all: bool = True,
        include_versions: bool = False,
    ) -> PluginMarketplaceIndex:
        _ = (self, source, limit, include_versions)
        captured["cursor"] = cursor
        captured["fetch_all"] = fetch_all
        return PluginMarketplaceIndex(plugins=())

    monkeypatch.setattr(
        clawhub_marketplace_provider.ClawHubMarketplaceProvider,
        "load_index",
        fake_load_index,
    )

    PluginMarketplaceService().load_provider_index(
        source=PluginMarketplaceSource(
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            value="https://clawhub.test",
        ),
        app_config_dir=tmp_path / "app",
        fetch_all=False,
    )

    assert captured == {"cursor": "", "fetch_all": False}


def test_marketplace_install_allows_clawhub_warnings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, PluginInstallSource] = {}

    def fake_load_provider_entry(
        self: PluginMarketplaceService,
        *,
        source: PluginMarketplaceSource,
        name: str,
        app_config_dir: Path,
        install_policy: PluginMarketplaceInstallPolicy | None = None,
    ) -> PluginMarketplaceEntry:
        _ = self
        _ = source
        _ = name
        _ = app_config_dir
        policy = install_policy or PluginMarketplaceInstallPolicy()
        return apply_install_policy_to_entry(
            entry=PluginMarketplaceEntry(
                name="risky-plugin",
                latest="1.0.0",
                compatibility=PluginMarketplaceCompatibility.DIRECT,
                versions=(
                    PluginMarketplaceVersion(
                        version="1.0.0",
                        source=PluginInstallSource(
                            kind=PluginInstallSourceKind.HTTP_ARCHIVE,
                            value="https://clawhub.test/archive.zip",
                            sha="abc",
                        ),
                        warnings=("ClawHub package executes code.",),
                    ),
                ),
            ),
            provider=PluginMarketplaceProviderKind.CLAWHUB,
            policy=policy,
        )

    def fake_install_from_source(
        self: PluginConfigManager,
        *,
        source: PluginInstallSource,
        scope: PluginScope,
        enabled: bool = True,
        resolved_install_source: PluginInstallSource | None = None,
        expected_sha256: str = "",
        extra_dependencies: tuple[PluginDependency, ...] = (),
    ) -> PluginStateRecord:
        _ = (self, enabled, resolved_install_source, expected_sha256)
        _ = extra_dependencies
        captured["source"] = source
        return PluginStateRecord(
            name="risky-plugin",
            scope=scope,
            root_dir=tmp_path / "app" / "plugins" / "installed" / "risky-plugin",
            source=source,
        )

    monkeypatch.setattr(
        PluginMarketplaceService,
        "load_provider_entry",
        fake_load_provider_entry,
    )
    monkeypatch.setattr(
        PluginConfigManager,
        "install_from_source",
        fake_install_from_source,
    )

    record = PluginConfigManager(
        app_config_dir=tmp_path / "app"
    ).install_marketplace_plugin(
        name="risky-plugin",
        marketplace=Path("clawhub"),
        marketplace_provider=PluginMarketplaceProviderKind.CLAWHUB,
        scope=PluginScope.USER,
    )

    assert record.name == "risky-plugin"
    assert captured["source"].kind == PluginInstallSourceKind.MARKETPLACE
    assert captured["source"].value == "risky-plugin"


def test_http_archive_install_adapts_openclaw_manifest(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    source_root = tmp_path / "archive-root" / "quality"
    source_root.mkdir(parents=True)
    (source_root / "openclaw.plugin.json").write_text(
        json.dumps(
            {
                "id": "quality",
                "version": "1.0.0",
                "description": "Quality tools",
            }
        ),
        encoding="utf-8",
    )
    archive_path = tmp_path / "quality.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(
            source_root / "openclaw.plugin.json",
            "quality/openclaw.plugin.json",
        )

    manager = PluginConfigManager(app_config_dir=app_config_dir)
    installed = manager.install_from_source(
        source=PluginInstallSource(
            kind=PluginInstallSourceKind.HTTP_ARCHIVE,
            value=archive_path.resolve().as_uri(),
            adapter="openclaw",
        ),
        scope=PluginScope.USER,
    )

    assert installed.name == "quality"
    assert installed.version == "1.0.0"
    assert (installed.root_dir / "app" / "plugin.json").exists()


def test_openclaw_adapter_creates_manifest_for_static_bundle(tmp_path: Path) -> None:
    plugin_root = tmp_path / "bundle"
    skills_dir = plugin_root / "skills" / "quality"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "# Quality\n\nUse this skill for quality checks.\n",
        encoding="utf-8",
    )

    adapt_openclaw_plugin_tree(
        plugin_root=plugin_root,
        adapter="openclaw",
        manifest_config_dir_name="app",
        source_version="1.2.3",
    )

    manifest = json.loads((plugin_root / "app" / "plugin.json").read_text("utf-8"))
    assert manifest["name"] == "bundle"
    assert manifest["version"] == "1.2.3"
    assert manifest["skills"] == "./skills"


def test_openclaw_adapter_creates_manifest_for_root_skill_static_bundle(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "bundle"
    plugin_root.mkdir()
    (plugin_root / "SKILL.md").write_text(
        "# Bundle\n\nUse this skill from the bundle root.\n",
        encoding="utf-8",
    )
    nested_skill_dir = plugin_root / "nested"
    nested_skill_dir.mkdir()
    (nested_skill_dir / "SKILL.md").write_text(
        "# Nested\n\nUse this nested skill for checks.\n",
        encoding="utf-8",
    )

    adapt_openclaw_plugin_tree(
        plugin_root=plugin_root,
        adapter="openclaw",
        manifest_config_dir_name="app",
        source_version=None,
    )

    manifest = json.loads((plugin_root / "app" / "plugin.json").read_text("utf-8"))
    assert manifest["version"] == "local"
    assert manifest["skills"] == "."


def test_openclaw_adapter_maps_nested_skill_manifest(tmp_path: Path) -> None:
    plugin_root = tmp_path / "bundle"
    skill_dir = plugin_root / "verified-agent-identity"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# Verified Agent Identity\n\nUse this skill for identity checks.\n",
        encoding="utf-8",
    )

    adapt_openclaw_plugin_tree(
        plugin_root=plugin_root,
        adapter="openclaw",
        manifest_config_dir_name="app",
        source_version="4.0.0",
    )

    manifest = json.loads((plugin_root / "app" / "plugin.json").read_text("utf-8"))
    assert manifest["name"] == "bundle"
    assert manifest["version"] == "4.0.0"
    assert manifest["skills"] == "./verified-agent-identity"


def test_openclaw_adapter_maps_multiple_nested_skill_manifests(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "bundle"
    for relative in (
        "alpha",
        "packs/beta",
        "node_modules/ignored",
        ".venv/ignored",
        "a/b/c/d/e",
    ):
        skill_dir = plugin_root / relative
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "# Skill\n\nUse this skill for tests.\n",
            encoding="utf-8",
        )

    adapt_openclaw_plugin_tree(
        plugin_root=plugin_root,
        adapter="openclaw",
        manifest_config_dir_name="app",
        source_version="4.0.0",
    )

    manifest = json.loads((plugin_root / "app" / "plugin.json").read_text("utf-8"))
    assert manifest["skills"] == ["./alpha", "./packs/beta"]


def test_openclaw_adapter_sanitizes_component_path_edge_cases(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "bundle"
    (plugin_root / "commands").mkdir(parents=True)
    (plugin_root / "commands" / "setup.md").write_text("Setup.\n", encoding="utf-8")
    (plugin_root / "commands" / "teardown.md").write_text(
        "Teardown.\n",
        encoding="utf-8",
    )
    (plugin_root / "README.md").write_text("Docs.\n", encoding="utf-8")

    assert _sanitize_component_path_value(
        key="commands",
        value=[
            " commands/setup.md ",
            "commands/teardown.md",
            "README.md",
            "",
            "../outside.md",
            "\\absolute\\command.md",
            7,
        ],
        plugin_root=plugin_root,
    ) == [
        "./commands",
        "./README.md",
        "",
        "../outside.md",
        "\\absolute\\command.md",
        7,
    ]
    assert _sanitize_component_path_value(
        key="settings",
        value={"path": "settings/config.json"},
        plugin_root=plugin_root,
    ) == {"path": "settings/config.json"}
    assert (
        _directory_component_path(
            key="settings",
            value="settings/config.json",
            plugin_root=plugin_root,
        )
        == "settings/config.json"
    )
    assert (
        _directory_component_path(
            key="commands",
            value="./README.md",
            plugin_root=plugin_root,
        )
        == "./README.md"
    )
    assert (
        _directory_component_path(
            key="commands",
            value="./commands/setup.md",
            plugin_root=plugin_root,
        )
        == "./commands"
    )


def test_openclaw_adapter_sanitizes_existing_manifest_user_config(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "bundle"
    plugin_root.mkdir()
    raw_scalar = ["token"]

    assert (
        _sanitize_relay_manifest_value(
            key="description",
            value="Plugin",
            plugin_root=plugin_root,
        )
        == "Plugin"
    )
    assert (
        _sanitize_relay_manifest_value(
            key="userConfig",
            value=raw_scalar,
            plugin_root=plugin_root,
        )
        is raw_scalar
    )
    assert _sanitize_relay_manifest_value(
        key="userConfig",
        value={
            "token": {
                "type": "string",
                "title": "Token",
                "pattern": "ignored",
                "sensitive": True,
            },
            "legacy": "enabled",
        },
        plugin_root=plugin_root,
    ) == {
        "token": {
            "type": "string",
            "title": "Token",
            "sensitive": True,
        },
        "legacy": "enabled",
    }


def test_archive_plugin_root_ignores_macos_metadata(tmp_path: Path) -> None:
    extract_dir = tmp_path / "extract"
    plugin_root = extract_dir / "quality"
    plugin_root.mkdir(parents=True)
    (extract_dir / "__MACOSX").mkdir()
    (extract_dir / ".DS_Store").write_text("", encoding="utf-8")

    assert plugin_installers._archive_plugin_root(extract_dir) == plugin_root


def test_openclaw_adapter_maps_config_and_static_roots(tmp_path: Path) -> None:
    plugin_root = tmp_path / "mapped"
    (plugin_root / "agents").mkdir(parents=True)
    (plugin_root / "commands").mkdir()
    (plugin_root / "hooks").mkdir()
    (plugin_root / "hooks" / "hooks.json").write_text("{}", encoding="utf-8")
    (plugin_root / "mcp.json").write_text("{}", encoding="utf-8")
    (plugin_root / "openclaw.plugin.json").write_text(
        json.dumps(
            {
                "runtimeId": "mapped/runtime",
                "version": "2.0.0",
                "displayName": "Mapped runtime",
                "runtimeExtensions": ["./dist/index.js"],
                "configSchema": {
                    "required": ["token"],
                    "properties": {
                        "token": {
                            "type": "string",
                            "title": "Token",
                            "description": "API token",
                            "default": "secret",
                            "sensitive": True,
                        },
                        "": {"type": "string"},
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    adapt_openclaw_plugin_tree(
        plugin_root=plugin_root,
        adapter="openclaw",
        manifest_config_dir_name="app",
    )

    manifest = json.loads((plugin_root / "app" / "plugin.json").read_text("utf-8"))
    assert manifest["name"] == "mapped-runtime"
    assert manifest["version"] == "2.0.0"
    assert manifest["roles"] == "./agents"
    assert manifest["commands"] == "./commands"
    assert manifest["hooks"] == "./hooks/hooks.json"
    assert manifest["mcp_servers"] == "./mcp.json"
    assert manifest["user_config"]["token"]["required"] is True
    assert manifest["user_config"]["token"]["sensitive"] is True


def test_openclaw_adapter_uses_source_version_when_manifest_omits_version(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "mapped"
    (plugin_root / "skills").mkdir(parents=True)
    (plugin_root / "openclaw.plugin.json").write_text(
        json.dumps({"id": "mapped/runtime"}),
        encoding="utf-8",
    )

    adapt_openclaw_plugin_tree(
        plugin_root=plugin_root,
        adapter="openclaw",
        manifest_config_dir_name="app",
        source_version="3.4.5",
    )

    manifest = json.loads((plugin_root / "app" / "plugin.json").read_text("utf-8"))
    assert manifest["version"] == "3.4.5"


def test_openclaw_adapter_noops_for_unmappable_or_existing_manifest(
    tmp_path: Path,
) -> None:
    no_adapter_root = tmp_path / "no-adapter"
    no_adapter_root.mkdir()
    adapt_openclaw_plugin_tree(
        plugin_root=no_adapter_root,
        adapter="other",
        manifest_config_dir_name="app",
    )
    assert not (no_adapter_root / "app" / "plugin.json").exists()

    existing_root = tmp_path / "existing"
    (existing_root / "app").mkdir(parents=True)
    (existing_root / "app" / "plugin.json").write_text("{}", encoding="utf-8")
    adapt_openclaw_plugin_tree(
        plugin_root=existing_root,
        adapter="openclaw",
        manifest_config_dir_name="app",
    )
    assert json.loads((existing_root / "app" / "plugin.json").read_text("utf-8")) == {}

    extra_manifest_root = tmp_path / "extra-manifest"
    (extra_manifest_root / "app").mkdir(parents=True)
    (extra_manifest_root / "agents").mkdir()
    (extra_manifest_root / "agents" / "researcher.md").write_text(
        "---\nname: Researcher\n---\nResearch.\n",
        encoding="utf-8",
    )
    (extra_manifest_root / "commands").mkdir()
    (extra_manifest_root / "commands" / "setup.md").write_text(
        "---\nname: setup\n---\nSetup.\n",
        encoding="utf-8",
    )
    (extra_manifest_root / "commands" / "teardown.md").write_text(
        "---\nname: teardown\n---\nTeardown.\n",
        encoding="utf-8",
    )
    (extra_manifest_root / "README.md").write_text("Bundle docs.\n", encoding="utf-8")
    (extra_manifest_root / "app" / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://relay-teams.test/plugin.schema.json",
                "name": "extra-manifest",
                "version": "1.0.0",
                "description": "OpenClaw package",
                "agents": "agents/researcher.md",
                "commands": [
                    "commands/setup.md",
                    "commands/teardown.md",
                    "../outside.md",
                    "README.md",
                    7,
                ],
                "hooks": "\\absolute\\hooks.json",
                "mcpServers": "./mcp.json",
                "monitors": "   ",
                "settings": ["settings/config.json", {"path": "settings/config.json"}],
                "userConfig": {
                    "token": {
                        "type": "string",
                        "sensitive": True,
                    },
                    "preferred_currency": {
                        "type": "string",
                        "title": "Preferred currency",
                        "default": "USD",
                        "pattern": "^[A-Z]{3}$",
                    },
                },
                "displayName": "Extra Manifest",
                "icon": "./icon.png",
                "id": "extra-manifest",
                "commandNamespace": "extra",
            }
        ),
        encoding="utf-8",
    )
    adapt_openclaw_plugin_tree(
        plugin_root=extra_manifest_root,
        adapter="openclaw",
        manifest_config_dir_name="app",
    )
    manifest = json.loads(
        (extra_manifest_root / "app" / "plugin.json").read_text("utf-8")
    )
    assert manifest == {
        "$schema": "https://relay-teams.test/plugin.schema.json",
        "name": "extra-manifest",
        "version": "1.0.0",
        "description": "OpenClaw package",
        "agents": "./agents",
        "commands": ["./commands", "../outside.md", "./README.md", 7],
        "hooks": "\\absolute\\hooks.json",
        "mcpServers": "./mcp.json",
        "monitors": "   ",
        "settings": ["./settings/config.json", {"path": "settings/config.json"}],
        "userConfig": {
            "token": {
                "type": "string",
                "sensitive": True,
            },
            "preferred_currency": {
                "type": "string",
                "title": "Preferred currency",
                "default": "USD",
            },
        },
    }

    claude_manifest_root = tmp_path / "claude-manifest"
    (claude_manifest_root / ".claude-plugin").mkdir(parents=True)
    (claude_manifest_root / "agents").mkdir()
    (claude_manifest_root / "commands").mkdir()
    (claude_manifest_root / "commands" / "setup.md").write_text(
        "---\n"
        "name: setup\n"
        "description: Setup command. Usage: /setup <target>\n"
        "aliases:\n"
        "  - /bootstrap\n"
        "  - setup-env\n"
        "---\n"
        "# Setup\n",
        encoding="utf-8",
    )
    (claude_manifest_root / "commands" / "deploy").mkdir()
    (claude_manifest_root / "commands" / "deploy" / "release.md").write_text(
        "---\n"
        "name: release\n"
        "description: Release command. Usage: /deploy:release <target>\n"
        "---\n"
        "# Release\n",
        encoding="utf-8",
    )
    (claude_manifest_root / "skills" / "conflict-patterns").mkdir(parents=True)
    (claude_manifest_root / "skills" / "conflict-patterns" / "SKILL.md").write_text(
        "---\n"
        "name: Conflict Patterns\n"
        "description: Identify conflicts. Usage: /skill-git:check <skill-name>\n"
        "version: 1.0.0\n"
        "---\n"
        "# Conflict Patterns\n",
        encoding="utf-8",
    )
    (claude_manifest_root / "agents" / "performance-marketer.md").write_text(
        "---\n"
        "name: Performance Marketer\n"
        "tools:\n"
        "  - web-search\n"
        "---\n"
        "Run campaigns.\n",
        encoding="utf-8",
    )
    (claude_manifest_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "@markifact/mcp",
                "version": "1.0.0",
                "mcpServers": "mcp.json",
                "commands": ["commands/setup.md"],
                "userConfig": {
                    "preferred_currency": {
                        "type": "string",
                        "default": "USD",
                        "pattern": "^[A-Z]{3}$",
                    }
                },
                "displayName": "Markifact Performance Marketing",
                "icon": "./icon.png",
            }
        ),
        encoding="utf-8",
    )
    adapt_openclaw_plugin_tree(
        plugin_root=claude_manifest_root,
        adapter="openclaw",
        manifest_config_dir_name="app",
    )
    claude_manifest = json.loads(
        (claude_manifest_root / ".claude-plugin" / "plugin.json").read_text("utf-8")
    )
    assert claude_manifest == {
        "name": "markifact-mcp",
        "version": "1.0.0",
        "mcpServers": "./mcp.json",
        "commands": ["./commands"],
        "userConfig": {
            "preferred_currency": {
                "type": "string",
                "default": "USD",
            }
        },
    }
    agent_role = (
        claude_manifest_root / "agents" / "performance-marketer.md"
    ).read_text("utf-8")
    assert "role_id: Performance Marketer" in agent_role
    assert "mode: subagent" in agent_role
    assert "- web-search" in agent_role
    skill_manifest = (
        claude_manifest_root / "skills" / "conflict-patterns" / "SKILL.md"
    ).read_text("utf-8")
    assert (
        "description: 'Identify conflicts. Usage: /skill-git:check <skill-name>'"
        in skill_manifest
    )
    command_manifest = (claude_manifest_root / "commands" / "setup.md").read_text(
        "utf-8"
    )
    assert "description: 'Setup command. Usage: /setup <target>'" in command_manifest
    assert "- /bootstrap" in command_manifest
    assert "- setup-env" in command_manifest
    nested_command_manifest = (
        claude_manifest_root / "commands" / "deploy" / "release.md"
    ).read_text("utf-8")
    assert (
        "description: 'Release command. Usage: /deploy:release <target>'"
        in nested_command_manifest
    )

    unmappable_root = tmp_path / "unmappable"
    unmappable_root.mkdir()
    adapt_openclaw_plugin_tree(
        plugin_root=unmappable_root,
        adapter="openclaw",
        manifest_config_dir_name="app",
    )
    assert not (unmappable_root / "app" / "plugin.json").exists()


def test_http_archive_install_accepts_sri_digest(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    source_root = tmp_path / "archive-root" / "quality"
    source_root.mkdir(parents=True)
    (source_root / "openclaw.plugin.json").write_text(
        json.dumps({"id": "quality", "version": "1.0.0"}),
        encoding="utf-8",
    )
    archive_path = tmp_path / "quality.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(
            source_root / "openclaw.plugin.json",
            "quality/openclaw.plugin.json",
        )
    digest = hashlib.sha512(archive_path.read_bytes()).digest()
    integrity = f"sha512-{base64.b64encode(digest).decode('ascii')}"

    installed = PluginConfigManager(app_config_dir=app_config_dir).install_from_source(
        source=PluginInstallSource(
            kind=PluginInstallSourceKind.HTTP_ARCHIVE,
            value=archive_path.resolve().as_uri(),
            adapter="openclaw",
            sha=integrity,
        ),
        scope=PluginScope.USER,
    )

    assert installed.name == "quality"


def test_http_archive_install_rejects_unsafe_tar_path(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.tar"
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps({"id": "quality", "version": "1.0.0"}),
        encoding="utf-8",
    )
    with tarfile.open(archive_path, "w") as archive:
        archive.add(payload_path, arcname="../openclaw.plugin.json")

    with pytest.raises(ValueError, match="archive path is unsafe"):
        PluginConfigManager(app_config_dir=tmp_path / "app").install_from_source(
            source=PluginInstallSource(
                kind=PluginInstallSourceKind.HTTP_ARCHIVE,
                value=archive_path.resolve().as_uri(),
                adapter="openclaw",
            ),
            scope=PluginScope.USER,
        )


def test_http_archive_install_rejects_unsupported_tar_entry(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.tar"
    unsupported = tarfile.TarInfo("quality/fifo")
    unsupported.type = tarfile.FIFOTYPE

    with tarfile.open(archive_path, "w") as archive:
        archive.addfile(unsupported)

    with pytest.raises(ValueError, match="unsupported entry"):
        plugin_installers._extract_archive(
            archive_path=archive_path,
            target_dir=tmp_path / "target",
        )


def test_http_archive_install_rejects_tar_symlink(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.tar"
    link = tarfile.TarInfo("quality/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "../outside"

    with tarfile.open(archive_path, "w") as archive:
        archive.addfile(link)

    with pytest.raises(ValueError, match="unsupported symlink"):
        plugin_installers._extract_archive(
            archive_path=archive_path,
            target_dir=tmp_path / "target",
        )


def test_http_archive_install_extracts_safe_tar(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    source_root = tmp_path / "archive-root" / "quality"
    source_root.mkdir(parents=True)
    manifest_path = source_root / "openclaw.plugin.json"
    manifest_path.write_text(
        json.dumps({"id": "quality", "version": "1.0.0"}),
        encoding="utf-8",
    )
    archive_path = tmp_path / "quality.tar"
    with tarfile.open(archive_path, "w") as archive:
        archive.add(manifest_path, arcname="quality/openclaw.plugin.json")

    installed = PluginConfigManager(app_config_dir=app_config_dir).install_from_source(
        source=PluginInstallSource(
            kind=PluginInstallSourceKind.HTTP_ARCHIVE,
            value=archive_path.resolve().as_uri(),
            adapter="openclaw",
            sha=hashlib.sha1(
                archive_path.read_bytes(),
                usedforsecurity=False,
            ).hexdigest(),
        ),
        scope=PluginScope.USER,
    )

    assert installed.name == "quality"
    assert (installed.root_dir / "app" / "plugin.json").exists()


def test_extract_zip_archive_preserves_executable_mode(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("Windows chmod does not preserve POSIX executable bits")
    archive_path = tmp_path / "quality.zip"
    script = zipfile.ZipInfo("quality/bin/run.sh")
    script.external_attr = 0o755 << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(script, "#!/bin/sh\n")

    plugin_installers._extract_archive(
        archive_path=archive_path,
        target_dir=tmp_path / "target",
    )

    extracted = tmp_path / "target" / "quality" / "bin" / "run.sh"
    assert stat.S_IMODE(extracted.stat().st_mode) == 0o755


def test_extract_tar_archive_preserves_executable_mode(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("Windows chmod does not preserve POSIX executable bits")
    archive_path = tmp_path / "quality.tar"
    payload = b"#!/bin/sh\n"
    script = tarfile.TarInfo("quality/bin/run.sh")
    script.mode = 0o755
    script.size = len(payload)
    with tarfile.open(archive_path, "w") as archive:
        archive.addfile(script, io.BytesIO(payload))

    plugin_installers._extract_archive(
        archive_path=archive_path,
        target_dir=tmp_path / "target",
    )

    extracted = tmp_path / "target" / "quality" / "bin" / "run.sh"
    assert stat.S_IMODE(extracted.stat().st_mode) == 0o755


def test_extract_archives_create_explicit_directory_entries(tmp_path: Path) -> None:
    zip_path = tmp_path / "quality.zip"
    zip_dir = zipfile.ZipInfo("quality/bin/")
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(zip_dir, "")
        archive.writestr("quality/bin/run.txt", "run\n")

    plugin_installers._extract_archive(
        archive_path=zip_path,
        target_dir=tmp_path / "zip-target",
    )

    tar_path = tmp_path / "quality.tar"
    tar_dir = tarfile.TarInfo("quality/bin")
    tar_dir.type = tarfile.DIRTYPE
    payload = b"run\n"
    tar_file = tarfile.TarInfo("quality/bin/run.txt")
    tar_file.size = len(payload)
    with tarfile.open(tar_path, "w") as archive:
        archive.addfile(tar_dir)
        archive.addfile(tar_file, io.BytesIO(payload))

    plugin_installers._extract_archive(
        archive_path=tar_path,
        target_dir=tmp_path / "tar-target",
    )

    assert (tmp_path / "zip-target" / "quality" / "bin").is_dir()
    assert (tmp_path / "zip-target" / "quality" / "bin" / "run.txt").read_text(
        "utf-8"
    ) == "run\n"
    assert (tmp_path / "tar-target" / "quality" / "bin").is_dir()
    assert (tmp_path / "tar-target" / "quality" / "bin" / "run.txt").read_text(
        "utf-8"
    ) == "run\n"


def test_zip_member_filesystem_name_preserves_windows_sanitization() -> None:
    illegal_name = plugin_installers._zip_member_filesystem_name(
        "quality/bad:name.md",
        platform_name="nt",
    )
    trailing_dot_name = plugin_installers._zip_member_filesystem_name(
        "quality/trailing. /file.txt",
        platform_name="nt",
    )

    assert illegal_name == "quality/bad_name.md"
    assert trailing_dot_name == "quality/trailing/file.txt"


def test_installer_filesystem_path_adds_windows_long_path_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(plugin_installers.os, "name", "nt")
        filesystem_path = plugin_installers._filesystem_path(tmp_path / "quality")

        assert filesystem_path.startswith("\\\\?\\")
        assert (
            plugin_installers._filesystem_path(
                cast(Path, _FakeResolvedPath("\\\\?\\C:\\plugins\\quality"))
            )
            == "\\\\?\\C:\\plugins\\quality"
        )
        assert (
            plugin_installers._filesystem_path(
                cast(Path, _FakeResolvedPath("\\\\server\\share\\quality"))
            )
            == "\\\\?\\UNC\\server\\share\\quality"
        )


def test_config_manager_filesystem_path_and_retry_helpers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(plugin_config_manager.os, "name", "nt")
        assert (
            plugin_config_manager._filesystem_path(
                cast(Path, _FakeResolvedPath("C:\\plugins\\quality"))
            )
            == "\\\\?\\C:\\plugins\\quality"
        )
        assert (
            plugin_config_manager._filesystem_path(
                cast(Path, _FakeResolvedPath("\\\\?\\C:\\plugins\\quality"))
            )
            == "\\\\?\\C:\\plugins\\quality"
        )
        assert (
            plugin_config_manager._filesystem_path(
                cast(Path, _FakeResolvedPath("\\\\server\\share\\quality"))
            )
            == "\\\\?\\UNC\\server\\share\\quality"
        )

    retry_target = tmp_path / "readonly.txt"
    retry_target.write_text("locked", encoding="utf-8")
    retry_target.chmod(stat.S_IREAD)
    calls: list[str] = []

    def remove_after_chmod(path: str) -> object:
        calls.append(path)
        Path(path).unlink()
        return None

    plugin_config_manager._make_writable_and_retry(
        remove_after_chmod,
        str(retry_target),
        RuntimeError("denied"),
    )

    assert calls == [str(retry_target)]
    assert not retry_target.exists()


def test_archive_digest_helpers_handle_supported_and_invalid_values(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "plugin.zip"
    archive_path.write_bytes(b"archive")

    assert plugin_installers._expected_archive_digest("a" * 128) == (
        "sha512",
        "a" * 128,
    )
    assert plugin_installers._expected_archive_digest("b" * 96) == (
        "sha384",
        "b" * 96,
    )
    assert plugin_installers._expected_archive_digest("c" * 64) == (
        "sha256",
        "c" * 64,
    )
    assert plugin_installers._expected_archive_digest("d" * 40) == (
        "sha1",
        "d" * 40,
    )
    assert plugin_installers._expected_archive_digest("short") is None
    assert (
        plugin_installers._archive_digest(
            archive_path=archive_path,
            algorithm="unknown",
        )
        == ""
    )
    assert plugin_installers._sri_digest("sha256-not-base64!") is None
    with pytest.raises(ValueError, match="digest format is unsupported"):
        plugin_installers._verify_archive_digest(
            archive_path=archive_path,
            expected_digest="not-a-digest",
        )


def test_extract_archive_rejects_unsupported_file(tmp_path: Path) -> None:
    archive_path = tmp_path / "plugin.bin"
    archive_path.write_text("not an archive", encoding="utf-8")

    with pytest.raises(ValueError, match="not a supported zip or tar file"):
        plugin_installers._extract_archive(
            archive_path=archive_path,
            target_dir=tmp_path / "target",
        )


def test_extract_archive_rejects_unsafe_zip_path(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../plugin.json", "{}")

    with pytest.raises(ValueError, match="archive path is unsafe"):
        plugin_installers._extract_archive(
            archive_path=archive_path,
            target_dir=tmp_path / "target",
        )


def test_http_archive_install_rejects_native_only_openclaw_plugin(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "archive-root" / "native-only"
    source_root.mkdir(parents=True)
    (source_root / "openclaw.plugin.json").write_text(
        json.dumps(
            {
                "id": "native-only",
                "version": "1.0.0",
                "runtimeExtensions": ["./dist/index.js"],
            }
        ),
        encoding="utf-8",
    )
    archive_path = tmp_path / "native-only.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(
            source_root / "openclaw.plugin.json",
            "native-only/openclaw.plugin.json",
        )

    with pytest.raises(ValueError, match="native runtime extension plugins"):
        PluginConfigManager(app_config_dir=tmp_path / "app").install_from_source(
            source=PluginInstallSource(
                kind=PluginInstallSourceKind.HTTP_ARCHIVE,
                value=archive_path.resolve().as_uri(),
                adapter="openclaw",
            ),
            scope=PluginScope.USER,
        )


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


def test_marketplace_update_passes_clawhub_policy_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_policies: list[PluginMarketplaceInstallPolicy | None] = []

    def fake_load_provider_entry(
        self: PluginMarketplaceService,
        *,
        source: PluginMarketplaceSource,
        name: str,
        app_config_dir: Path,
        install_policy: PluginMarketplaceInstallPolicy | None = None,
    ) -> PluginMarketplaceEntry:
        _ = (self, source, name, app_config_dir)
        captured_policies.append(install_policy)
        return PluginMarketplaceEntry(
            name="quality",
            latest="1.0.0",
            versions=(
                PluginMarketplaceVersion(
                    version="1.0.0",
                    source=PluginInstallSource(
                        kind=PluginInstallSourceKind.LOCAL,
                        value=str(tmp_path / "quality"),
                    ),
                    warnings=("ClawHub package artifact has no digest metadata.",),
                ),
            ),
        )

    monkeypatch.setattr(
        PluginMarketplaceService,
        "load_provider_entry",
        fake_load_provider_entry,
    )
    policy = PluginMarketplaceInstallPolicy().with_overrides(allow_missing_digest=True)
    manager = PluginConfigManager(app_config_dir=tmp_path / "app")

    manager._resolve_update_install_source(
        source=PluginInstallSource(
            kind=PluginInstallSourceKind.MARKETPLACE,
            value="quality",
            marketplace="clawhub",
            marketplace_provider="clawhub",
            marketplace_source="https://clawhub.test",
        ),
        version=None,
        install_policy=policy,
    )

    assert captured_policies == [policy]


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
    assert "tools:" in content
    assert "- Glob" in content
    assert "- Grep" in content
    assert "- Read" in content
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
