# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path
import shutil
import stat
import subprocess

from relay_teams.env import load_proxy_env_config
from relay_teams.plugins.claude_plugin_adapter import adapt_plugin_tree
from relay_teams.plugins.plugin_models import (
    PluginInstallSource,
    PluginInstallSourceKind,
)
from relay_teams.plugins.state_paths import (
    get_plugin_cache_root,
)

_GIT_CLONE_TIMEOUT_SECONDS = 120.0
_IGNORED_COPY_DIR_NAMES = frozenset({".git", "__pycache__"})


def copy_local_plugin_source(*, source_dir: Path, target_dir: Path) -> None:
    resolved_source = source_dir.expanduser().resolve()
    if not resolved_source.exists() or not resolved_source.is_dir():
        raise ValueError(f"Plugin source directory does not exist: {resolved_source}")
    _copy_plugin_tree(source_dir=resolved_source, target_dir=target_dir)


def install_git_plugin_source(
    *,
    source: PluginInstallSource,
    app_config_dir: Path,
    target_dir: Path,
) -> None:
    cache_root = get_plugin_cache_root(app_config_dir=app_config_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
    clone_dir = cache_root / _cache_dir_name(
        f"{source.value}:{target_dir.expanduser().resolve()}"
    )
    if clone_dir.exists():
        _remove_tree(clone_dir)
    try:
        if source.ref.strip() or source.sha.strip():
            _clone_git_ref(source=source, clone_dir=clone_dir)
        else:
            _run_git(["git", "clone", "--depth", "1", source.value, str(clone_dir)])
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        raise ValueError(f"Failed to clone plugin git source: {stderr}") from exc
    except OSError as exc:
        raise ValueError(f"Failed to run git: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Timed out cloning plugin git source") from exc
    _verify_git_sha(clone_dir=clone_dir, expected_sha=source.sha)
    _copy_plugin_tree(source_dir=clone_dir, target_dir=target_dir)
    adapt_plugin_tree(plugin_root=target_dir, adapter=source.adapter)


def install_git_subdir_plugin_source(
    *,
    source: PluginInstallSource,
    app_config_dir: Path,
    target_dir: Path,
) -> None:
    cache_root = get_plugin_cache_root(app_config_dir=app_config_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
    clone_dir = cache_root / _cache_dir_name(
        f"{source.value}:{source.subdir}:{target_dir.expanduser().resolve()}"
    )
    if clone_dir.exists():
        _remove_tree(clone_dir)
    try:
        if source.ref.strip() or source.sha.strip():
            _clone_git_ref(source=source, clone_dir=clone_dir)
        else:
            _run_git(["git", "clone", "--depth", "1", source.value, str(clone_dir)])
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        raise ValueError(f"Failed to clone plugin git source: {stderr}") from exc
    except OSError as exc:
        raise ValueError(f"Failed to run git: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Timed out cloning plugin git source") from exc
    _verify_git_sha(clone_dir=clone_dir, expected_sha=source.sha)
    source_dir = _resolve_git_subdir(clone_dir=clone_dir, subdir=source.subdir)
    _copy_plugin_tree(source_dir=source_dir, target_dir=target_dir)
    adapt_plugin_tree(plugin_root=target_dir, adapter=source.adapter)


def _clone_git_ref(*, source: PluginInstallSource, clone_dir: Path) -> None:
    ref = source.sha.strip() or source.ref.strip()
    _run_git(["git", "clone", "--no-checkout", source.value, str(clone_dir)])
    try:
        _run_git(["git", "-C", str(clone_dir), "checkout", "--detach", ref])
    except subprocess.CalledProcessError:
        _run_git(["git", "-C", str(clone_dir), "fetch", "--depth", "1", "origin", ref])
        _run_git(["git", "-C", str(clone_dir), "checkout", "--detach", "FETCH_HEAD"])


def _run_git(args: list[str]) -> None:
    subprocess.run(
        _git_args(args),
        check=True,
        capture_output=True,
        env=_git_subprocess_env(),
        text=True,
        timeout=_GIT_CLONE_TIMEOUT_SECONDS,
    )


def install_plugin_source(
    *,
    source: PluginInstallSource,
    app_config_dir: Path,
    target_dir: Path,
) -> None:
    if source.kind == PluginInstallSourceKind.LOCAL:
        copy_local_plugin_source(source_dir=Path(source.value), target_dir=target_dir)
        adapt_plugin_tree(plugin_root=target_dir, adapter=source.adapter)
        return
    if source.kind == PluginInstallSourceKind.GIT:
        install_git_plugin_source(
            source=source,
            app_config_dir=app_config_dir,
            target_dir=target_dir,
        )
        return
    if source.kind == PluginInstallSourceKind.GIT_SUBDIR:
        install_git_subdir_plugin_source(
            source=source,
            app_config_dir=app_config_dir,
            target_dir=target_dir,
        )
        return
    if source.kind == PluginInstallSourceKind.UNSUPPORTED:
        raise ValueError(f"Unsupported plugin source kind: {source.value}")
    raise ValueError(f"Unsupported install source kind: {source.kind.value}")


def _copy_plugin_tree(*, source_dir: Path, target_dir: Path) -> None:
    resolved_target = target_dir.expanduser().resolve()
    if resolved_target.exists():
        raise ValueError(f"Installed plugin target already exists: {resolved_target}")
    _ensure_no_plugin_tree_symlinks(source_dir=source_dir)
    resolved_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source_dir,
        resolved_target,
        ignore=shutil.ignore_patterns(".git", "__pycache__"),
    )


def _ensure_no_plugin_tree_symlinks(*, source_dir: Path) -> None:
    if source_dir.is_symlink():
        raise ValueError(f"Plugin source contains unsupported symlink: {source_dir}")
    for path in source_dir.rglob("*"):
        if _is_ignored_copy_path(source_dir=source_dir, path=path):
            continue
        if path.is_symlink():
            raise ValueError(f"Plugin source contains unsupported symlink: {path}")


def _is_ignored_copy_path(*, source_dir: Path, path: Path) -> bool:
    try:
        relative_path = path.relative_to(source_dir)
    except ValueError:
        return False
    return any(part in _IGNORED_COPY_DIR_NAMES for part in relative_path.parts)


def _verify_git_sha(*, clone_dir: Path, expected_sha: str) -> None:
    normalized = expected_sha.strip()
    if not normalized:
        return
    try:
        completed = subprocess.run(
            ["git", "-C", str(clone_dir), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            env=_git_subprocess_env(),
            text=True,
            timeout=_GIT_CLONE_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            f"Failed to resolve plugin git commit: {exc.stderr.strip()}"
        ) from exc
    actual = completed.stdout.strip().lower()
    expected = normalized.lower()
    if actual != expected and not actual.startswith(expected):
        raise ValueError(
            f"Plugin git source commit mismatch: expected {normalized}, got {actual}"
        )


def _resolve_git_subdir(*, clone_dir: Path, subdir: str) -> Path:
    relative = Path(subdir.strip().replace("\\", "/"))
    if relative.is_absolute() or not subdir.strip() or ".." in relative.parts:
        raise ValueError(f"Plugin git subdirectory is unsafe: {subdir}")
    resolved = (clone_dir / relative).resolve()
    try:
        resolved.relative_to(clone_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"Plugin git subdirectory is unsafe: {subdir}") from exc
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"Plugin git subdirectory does not exist: {subdir}")
    return resolved


def _cache_dir_name(value: str) -> str:
    readable = "".join(char if char.isalnum() else "_" for char in value).strip("_")
    prefix = readable[:16].strip("_") or "git"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _git_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(load_proxy_env_config().normalized_env())
    return env


def _git_args(args: list[str]) -> list[str]:
    if args and args[0] == "git":
        return ["git", "-c", "core.longpaths=true", *args[1:]]
    return args


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, onexc=_make_writable_and_retry)


def _make_writable_and_retry(
    function: Callable[[str], object],
    path: str,
    excinfo: BaseException,
) -> None:
    _ = excinfo
    resolved_path = Path(path)
    resolved_path.chmod(stat.S_IWRITE)
    function(path)
