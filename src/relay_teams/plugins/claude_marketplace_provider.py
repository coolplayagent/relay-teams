# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import subprocess

from pydantic import JsonValue

from relay_teams.env import load_proxy_env_config
from relay_teams.plugins.marketplace_models import (
    PluginMarketplaceEntry,
    PluginMarketplaceIndex,
    PluginMarketplaceSource,
    PluginMarketplaceVersion,
)
from relay_teams.plugins.plugin_models import (
    PluginDependency,
    PluginInstallSource,
    PluginInstallSourceKind,
)
from relay_teams.plugins.state_paths import get_plugin_cache_root

_CLAUDE_MARKETPLACE_FILE = ".claude-plugin/marketplace.json"
_DEFAULT_CLAUDE_MARKETPLACE = "claude-plugins-official"
_DEFAULT_CLAUDE_MARKETPLACE_SOURCE = "anthropics/claude-plugins-official"
_GIT_TIMEOUT_SECONDS = 120.0


class ClaudeMarketplaceProvider:
    def load_index(
        self,
        *,
        source: PluginMarketplaceSource,
        app_config_dir: Path,
    ) -> PluginMarketplaceIndex:
        marketplace_root = self._materialize_marketplace(
            source=source,
            app_config_dir=app_config_dir,
        )
        raw = self._read_marketplace_json(marketplace_root)
        metadata = raw.get("metadata")
        plugin_root = ""
        if isinstance(metadata, Mapping):
            raw_plugin_root = metadata.get("pluginRoot")
            if isinstance(raw_plugin_root, str):
                plugin_root = raw_plugin_root.strip()
        entries: list[PluginMarketplaceEntry] = []
        raw_plugins = raw.get("plugins", ())
        if not isinstance(raw_plugins, list):
            raise ValueError("Claude marketplace plugins must be a list")
        for raw_plugin in raw_plugins:
            if not isinstance(raw_plugin, Mapping):
                raise ValueError("Claude marketplace plugin entries must be objects")
            plugin_payload = {str(key): value for key, value in raw_plugin.items()}
            entries.append(
                self._entry_from_raw_plugin(
                    raw_plugin=plugin_payload,
                    marketplace_root=marketplace_root,
                    plugin_root=plugin_root,
                )
            )
        return PluginMarketplaceIndex(
            version=str(raw.get("version") or "1"),
            plugins=tuple(entries),
        )

    def _entry_from_raw_plugin(
        self,
        *,
        raw_plugin: Mapping[str, object],
        marketplace_root: Path,
        plugin_root: str,
    ) -> PluginMarketplaceEntry:
        name = self._required_string(raw_plugin, "name")
        description = self._optional_string(raw_plugin, "description")
        source, unsupported_reason = self._source_from_raw_plugin(
            raw_plugin=raw_plugin,
            marketplace_root=marketplace_root,
            plugin_root=plugin_root,
        )
        version = self._version_from_raw_plugin(raw_plugin=raw_plugin, source=source)
        warnings = self._warnings_for_source(source)
        return PluginMarketplaceEntry(
            name=name,
            description=description,
            latest=version,
            versions=(
                PluginMarketplaceVersion(
                    version=version,
                    source=source,
                    dependencies=self._dependencies_from_raw_plugin(raw_plugin),
                    warnings=warnings,
                    unsupported_reason=unsupported_reason,
                ),
            ),
        )

    def _source_from_raw_plugin(
        self,
        *,
        raw_plugin: Mapping[str, object],
        marketplace_root: Path,
        plugin_root: str,
    ) -> tuple[PluginInstallSource, str]:
        raw_source = raw_plugin.get("source")
        if isinstance(raw_source, str):
            return (
                self._string_source(
                    value=raw_source,
                    marketplace_root=marketplace_root,
                    plugin_root=plugin_root,
                ),
                "",
            )
        if isinstance(raw_source, Mapping):
            return self._object_source(
                {str(key): value for key, value in raw_source.items()}
            )
        raise ValueError("Claude marketplace plugin source is required")

    @staticmethod
    def _string_source(
        *,
        value: str,
        marketplace_root: Path,
        plugin_root: str,
    ) -> PluginInstallSource:
        normalized = value.strip()
        if _looks_like_git_source(normalized):
            return PluginInstallSource(
                kind=PluginInstallSourceKind.GIT,
                value=_github_shorthand_to_url(normalized),
                adapter="claude",
            )
        relative = _safe_relative_path(normalized)
        if plugin_root.strip():
            relative = _join_relative_paths(
                _safe_relative_path(plugin_root),
                relative,
            )
        source_path = _resolve_marketplace_local_source(
            marketplace_root=marketplace_root,
            relative=relative,
        )
        return PluginInstallSource(
            kind=PluginInstallSourceKind.LOCAL,
            value=str(source_path),
            adapter="claude",
        )

    def _object_source(
        self, raw_source: Mapping[str, object]
    ) -> tuple[PluginInstallSource, str]:
        source_type = self._required_string(raw_source, "source")
        ref = self._optional_string(raw_source, "ref")
        sha = self._optional_string(raw_source, "sha") or self._optional_string(
            raw_source, "commit"
        )
        path = self._optional_string(raw_source, "path")
        if source_type == "github":
            repo = self._required_string(raw_source, "repo")
            return (
                self._git_or_subdir_source(
                    value=_github_shorthand_to_url(repo),
                    ref=ref,
                    sha=sha,
                    path=path,
                ),
                "",
            )
        if source_type == "url":
            return (
                self._git_or_subdir_source(
                    value=self._required_string(raw_source, "url"),
                    ref=ref,
                    sha=sha,
                    path=path,
                ),
                "",
            )
        if source_type == "git-subdir":
            return (
                PluginInstallSource(
                    kind=PluginInstallSourceKind.GIT_SUBDIR,
                    value=_github_shorthand_to_url(
                        self._required_string(raw_source, "url")
                    ),
                    ref=ref,
                    sha=sha,
                    adapter="claude",
                    subdir=_safe_relative_path(
                        self._required_string(raw_source, "path")
                    ),
                ),
                "",
            )
        if source_type == "npm":
            return (
                PluginInstallSource(
                    kind=PluginInstallSourceKind.UNSUPPORTED,
                    value=self._optional_string(raw_source, "package")
                    or self._optional_string(raw_source, "name")
                    or source_type,
                ),
                "Claude marketplace npm plugin sources are not supported",
            )
        return (
            PluginInstallSource(
                kind=PluginInstallSourceKind.UNSUPPORTED,
                value=source_type,
            ),
            f"Unsupported Claude marketplace plugin source: {source_type}",
        )

    @staticmethod
    def _git_or_subdir_source(
        *,
        value: str,
        ref: str,
        sha: str,
        path: str,
    ) -> PluginInstallSource:
        if path.strip():
            return PluginInstallSource(
                kind=PluginInstallSourceKind.GIT_SUBDIR,
                value=_github_shorthand_to_url(value),
                ref=ref,
                sha=sha,
                adapter="claude",
                subdir=_safe_relative_path(path),
            )
        return PluginInstallSource(
            kind=PluginInstallSourceKind.GIT,
            value=_github_shorthand_to_url(value),
            ref=ref,
            sha=sha,
            adapter="claude",
        )

    @staticmethod
    def _version_from_raw_plugin(
        *,
        raw_plugin: Mapping[str, object],
        source: PluginInstallSource,
    ) -> str:
        version = raw_plugin.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
        if source.sha.strip():
            return source.sha.strip()
        if source.ref.strip():
            return source.ref.strip()
        return "latest"

    @staticmethod
    def _dependencies_from_raw_plugin(
        raw_plugin: Mapping[str, object],
    ) -> tuple[PluginDependency, ...]:
        raw_dependencies = raw_plugin.get("dependencies")
        if raw_dependencies is None:
            return ()
        if not isinstance(raw_dependencies, list):
            raise ValueError("Claude marketplace plugin dependencies must be a list")
        dependencies: list[PluginDependency] = []
        for raw_dependency in raw_dependencies:
            if isinstance(raw_dependency, str):
                dependencies.append(PluginDependency(name=raw_dependency))
                continue
            if isinstance(raw_dependency, Mapping):
                name = raw_dependency.get("name")
                version = raw_dependency.get("version")
                if not isinstance(name, str):
                    raise ValueError("Claude marketplace dependency name is required")
                dependencies.append(
                    PluginDependency(
                        name=name,
                        version=version if isinstance(version, str) else None,
                    )
                )
                continue
            raise ValueError("Claude marketplace dependency entries must be objects")
        return tuple(dependencies)

    @staticmethod
    def _warnings_for_source(source: PluginInstallSource) -> tuple[str, ...]:
        if source.kind == PluginInstallSourceKind.UNSUPPORTED:
            return ()
        warnings: list[str] = []
        if source.kind in (
            PluginInstallSourceKind.GIT,
            PluginInstallSourceKind.GIT_SUBDIR,
        ):
            if not source.sha.strip():
                warnings.append("Plugin source is not pinned to a commit sha.")
        return tuple(warnings)

    @staticmethod
    def _materialize_marketplace(
        *,
        source: PluginMarketplaceSource,
        app_config_dir: Path,
    ) -> Path:
        value = source.value.strip() or _DEFAULT_CLAUDE_MARKETPLACE_SOURCE
        local_path = Path(value).expanduser()
        if local_path.exists():
            if local_path.is_file():
                if (
                    local_path.name == "marketplace.json"
                    and local_path.parent.name == ".claude-plugin"
                ):
                    return local_path.parent.parent.resolve()
                return local_path.parent.resolve()
            return local_path.resolve()
        cache_root = (
            get_plugin_cache_root(app_config_dir=app_config_dir)
            / "marketplaces"
            / "claude"
        )
        cache_root.mkdir(parents=True, exist_ok=True)
        name = source.name.strip() or _DEFAULT_CLAUDE_MARKETPLACE
        checkout_dir = cache_root / _cache_dir_name(f"{name}:{value}:{source.ref}")
        if checkout_dir.exists():
            if _has_claude_marketplace_file(checkout_dir) and not source.refresh:
                return checkout_dir
            _remove_tree(checkout_dir)
        git_url = _github_shorthand_to_url(value)
        try:
            if source.ref.strip():
                _run_git(["git", "clone", "--no-checkout", git_url, str(checkout_dir)])
                _checkout_git_ref(clone_dir=checkout_dir, ref=source.ref.strip())
            else:
                _run_git(["git", "clone", "--depth", "1", git_url, str(checkout_dir)])
        except subprocess.CalledProcessError as exc:
            raise ValueError(
                f"Failed to clone Claude marketplace source: {exc.stderr.strip()}"
            ) from exc
        except OSError as exc:
            raise ValueError(f"Failed to run git: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Timed out cloning Claude marketplace source") from exc
        return checkout_dir

    @staticmethod
    def _read_marketplace_json(marketplace_root: Path) -> dict[str, JsonValue]:
        marketplace_file = marketplace_root / _CLAUDE_MARKETPLACE_FILE
        if marketplace_root.name == "marketplace.json":
            marketplace_file = marketplace_root
        if not marketplace_file.exists():
            raise ValueError(f"Claude marketplace file not found: {marketplace_file}")
        try:
            raw = json.loads(marketplace_file.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid Claude marketplace JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("Claude marketplace JSON must be an object")
        return {str(key): _json_value(value) for key, value in raw.items()}

    @staticmethod
    def _required_string(raw: Mapping[str, object], key: str) -> str:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Claude marketplace field is required: {key}")
        return value.strip()

    @staticmethod
    def _optional_string(raw: Mapping[str, object], key: str) -> str:
        value = raw.get(key)
        return value.strip() if isinstance(value, str) else ""


def _run_git(args: list[str]) -> None:
    subprocess.run(
        _git_args(args),
        check=True,
        capture_output=True,
        env=_git_subprocess_env(),
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def _looks_like_git_source(value: str) -> bool:
    return value.startswith(
        ("http://", "https://", "ssh://", "git@")
    ) or value.endswith(".git")


def _github_shorthand_to_url(value: str) -> str:
    normalized = value.strip()
    if _is_github_shorthand(normalized):
        suffix = "" if normalized.endswith(".git") else ".git"
        return f"https://github.com/{normalized}{suffix}"
    return normalized


def _is_github_shorthand(value: str) -> bool:
    if PureWindowsPath(value.strip()).drive:
        return False
    parts = value.strip().split("/")
    return (
        len(parts) == 2
        and all(part.strip() for part in parts)
        and not value.startswith((".", "http://", "https://", "ssh://", "git@"))
    )


def _safe_relative_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("/"):
        raise ValueError(f"Claude marketplace relative path is unsafe: {value}")
    normalized = normalized.strip("/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or PureWindowsPath(normalized).drive
        or not normalized
        or "//" in normalized
        or any(part == ".." for part in path.parts)
    ):
        raise ValueError(f"Claude marketplace relative path is unsafe: {value}")
    return normalized


def _join_relative_paths(parent: str, child: str) -> str:
    return _safe_relative_path(f"{parent}/{child}")


def _resolve_marketplace_local_source(*, marketplace_root: Path, relative: str) -> Path:
    resolved_root = marketplace_root.expanduser().resolve()
    source_path = resolved_root / relative
    try:
        source_path.resolve().relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"Claude marketplace plugin source escapes marketplace root: {relative}"
        ) from exc
    return source_path


def _checkout_git_ref(*, clone_dir: Path, ref: str) -> None:
    try:
        _run_git(["git", "-C", str(clone_dir), "checkout", "--detach", ref])
    except subprocess.CalledProcessError:
        _run_git(["git", "-C", str(clone_dir), "fetch", "--depth", "1", "origin", ref])
        _run_git(["git", "-C", str(clone_dir), "checkout", "--detach", "FETCH_HEAD"])


def _cache_dir_name(value: str) -> str:
    readable = "".join(char if char.isalnum() else "_" for char in value).strip("_")
    prefix = readable[:16].strip("_") or "marketplace"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _has_claude_marketplace_file(path: Path) -> bool:
    return (path / _CLAUDE_MARKETPLACE_FILE).exists()


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


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
