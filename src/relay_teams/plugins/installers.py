# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import os
import base64
import re
from collections.abc import Callable
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
from urllib.request import OpenerDirector, ProxyHandler, Request, build_opener
import zipfile

from relay_teams.env.proxy_env import load_proxy_env_config
from relay_teams.plugins.claude_plugin_adapter import adapt_plugin_tree
from relay_teams.plugins.openclaw_plugin_adapter import adapt_openclaw_plugin_tree
from relay_teams.plugins.plugin_models import (
    PluginInstallSource,
    PluginInstallSourceKind,
)
from relay_teams.plugins.state_paths import (
    get_plugin_cache_root,
)

_GIT_CLONE_TIMEOUT_SECONDS = 120.0
_HTTP_DOWNLOAD_TIMEOUT_SECONDS = 120.0
_IGNORED_COPY_DIR_NAMES = frozenset({".git", "__pycache__", "__MACOSX", ".DS_Store"})
_SRI_RE = re.compile(r"^(sha(?:256|384|512))-(.+)$")
_WINDOWS_ZIP_ILLEGAL_CHARS_RE = re.compile(r'[:<>|"?*]')
_DIGEST_ALGORITHMS = {
    "sha256": hashlib.sha256,
    "sha384": hashlib.sha384,
    "sha512": hashlib.sha512,
}


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
    _adapt_installed_plugin_tree(
        plugin_root=target_dir,
        adapter=source.adapter,
        app_config_dir=app_config_dir,
        source_version=source.requested_version,
    )


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
    _adapt_installed_plugin_tree(
        plugin_root=target_dir,
        adapter=source.adapter,
        app_config_dir=app_config_dir,
        source_version=source.requested_version,
    )


def install_http_archive_plugin_source(
    *,
    source: PluginInstallSource,
    app_config_dir: Path,
    target_dir: Path,
) -> None:
    cache_root = get_plugin_cache_root(app_config_dir=app_config_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
    archive_path = cache_root / f"{_cache_dir_name(source.value)}.archive"
    extract_dir = cache_root / f"{_cache_dir_name(source.value)}_extract"
    if archive_path.exists():
        archive_path.unlink()
    if extract_dir.exists():
        _remove_tree(extract_dir)
    _download_archive(url=source.value, target=archive_path)
    _verify_archive_digest(archive_path=archive_path, expected_digest=source.sha)
    extract_dir.mkdir(parents=True)
    _extract_archive(archive_path=archive_path, target_dir=extract_dir)
    source_dir = _archive_plugin_root(extract_dir)
    _copy_plugin_tree(source_dir=source_dir, target_dir=target_dir)
    _adapt_installed_plugin_tree(
        plugin_root=target_dir,
        adapter=source.adapter,
        app_config_dir=app_config_dir,
        source_version=source.requested_version,
    )


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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
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
        _adapt_installed_plugin_tree(
            plugin_root=target_dir,
            adapter=source.adapter,
            app_config_dir=app_config_dir,
            source_version=source.requested_version,
        )
        return
    if source.kind == PluginInstallSourceKind.GIT:
        install_git_plugin_source(
            source=source,
            app_config_dir=app_config_dir,
            target_dir=target_dir,
        )
        return
    if source.kind == PluginInstallSourceKind.HTTP_ARCHIVE:
        install_http_archive_plugin_source(
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
        _filesystem_path(source_dir.expanduser().resolve()),
        _filesystem_path(resolved_target),
        ignore=shutil.ignore_patterns(".git", "__pycache__"),
    )


def _download_archive(*, url: str, target: Path) -> None:
    request = Request(url, headers={"User-Agent": "relay-teams-plugin-installer"})
    try:
        with _url_opener().open(
            request,
            timeout=_HTTP_DOWNLOAD_TIMEOUT_SECONDS,
        ) as response:
            target.write_bytes(response.read())
    except OSError as exc:
        raise ValueError(f"Failed to download plugin archive: {exc}") from exc


def _verify_archive_digest(*, archive_path: Path, expected_digest: str) -> None:
    normalized = expected_digest.strip()
    if not normalized:
        return
    expected = _expected_archive_digest(normalized)
    if not expected:
        raise ValueError(f"Plugin archive digest format is unsupported: {normalized}")
    algorithm, expected_hex = expected
    actual = _archive_digest(archive_path=archive_path, algorithm=algorithm)
    if not actual:
        return
    if actual != expected_hex:
        raise ValueError(
            f"Plugin archive digest mismatch: expected {expected_digest}, got {actual}"
        )


def _expected_archive_digest(value: str) -> tuple[str, str] | None:
    sri_digest = _sri_digest(value)
    if sri_digest is not None:
        return sri_digest
    normalized = value.lower()
    if len(normalized) == 128:
        return "sha512", normalized
    if len(normalized) == 96:
        return "sha384", normalized
    if len(normalized) == 64:
        return "sha256", normalized
    if len(normalized) == 40:
        return "sha1", normalized
    return None


def _archive_digest(*, archive_path: Path, algorithm: str) -> str:
    if algorithm == "sha1":
        return hashlib.sha1(
            archive_path.read_bytes(),
            usedforsecurity=False,
        ).hexdigest()
    digest_function = _DIGEST_ALGORITHMS.get(algorithm)
    if digest_function is None:
        return ""
    return digest_function(archive_path.read_bytes()).hexdigest()


def _sri_digest(value: str) -> tuple[str, str] | None:
    match = _SRI_RE.match(value)
    if match is None:
        return None
    algorithm = match.group(1)
    try:
        digest = base64.b64decode(match.group(2), validate=True).hex()
    except ValueError:
        return None
    return algorithm, digest


def _extract_archive(*, archive_path: Path, target_dir: Path) -> None:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            _extract_zip_archive(archive=archive, target_dir=target_dir)
            return
    except zipfile.BadZipFile:
        pass
    try:
        with tarfile.open(archive_path) as archive:
            _extract_tar_archive(archive=archive, target_dir=target_dir)
            return
    except tarfile.TarError as exc:
        raise ValueError("Plugin archive is not a supported zip or tar file") from exc


def _extract_zip_archive(*, archive: zipfile.ZipFile, target_dir: Path) -> None:
    resolved_target = target_dir.resolve()
    for item in archive.infolist():
        if _zip_info_is_symlink(item):
            raise ValueError(
                f"Plugin archive contains unsupported symlink: {item.filename}"
            )
        member_filename = _zip_member_filesystem_name(item.filename)
        destination = (target_dir / member_filename).resolve()
        try:
            destination.relative_to(resolved_target)
        except ValueError as exc:
            raise ValueError(f"Plugin archive path is unsafe: {item.filename}") from exc
        if item.is_dir():
            Path(_filesystem_path(destination)).mkdir(parents=True, exist_ok=True)
        else:
            Path(_filesystem_path(destination.parent)).mkdir(
                parents=True,
                exist_ok=True,
            )
            with (
                archive.open(item) as source,
                open(
                    _filesystem_path(destination),
                    "wb",
                ) as target,
            ):
                shutil.copyfileobj(source, target)
        _apply_archive_mode(path=destination, mode=item.external_attr >> 16)


def _zip_member_filesystem_name(
    filename: str,
    *,
    platform_name: str | None = None,
) -> str:
    active_platform_name = os.name if platform_name is None else platform_name
    if active_platform_name != "nt":
        return filename
    sanitized_parts: list[str] = []
    for part in filename.replace("\\", "/").split("/"):
        sanitized = _WINDOWS_ZIP_ILLEGAL_CHARS_RE.sub("_", part).rstrip(". ")
        if sanitized:
            sanitized_parts.append(sanitized)
    return "/".join(sanitized_parts)


def _extract_tar_archive(*, archive: tarfile.TarFile, target_dir: Path) -> None:
    resolved_target = target_dir.resolve()
    for item in archive.getmembers():
        if item.issym() or item.islnk():
            raise ValueError(
                f"Plugin archive contains unsupported symlink: {item.name}"
            )
        if not item.isdir() and not item.isfile():
            raise ValueError(f"Plugin archive contains unsupported entry: {item.name}")
        destination = (target_dir / item.name).resolve()
        try:
            destination.relative_to(resolved_target)
        except ValueError as exc:
            raise ValueError(f"Plugin archive path is unsafe: {item.name}") from exc
    for item in archive.getmembers():
        destination = target_dir / item.name
        if item.isdir():
            Path(_filesystem_path(destination)).mkdir(parents=True, exist_ok=True)
            continue
        extracted = archive.extractfile(item)
        if extracted is None:
            raise ValueError(f"Plugin archive file cannot be extracted: {item.name}")
        Path(_filesystem_path(destination.parent)).mkdir(parents=True, exist_ok=True)
        with extracted, open(_filesystem_path(destination), "wb") as target:
            shutil.copyfileobj(extracted, target)
        _apply_archive_mode(path=destination, mode=item.mode)


def _zip_info_is_symlink(item: zipfile.ZipInfo) -> bool:
    return stat.S_IFMT(item.external_attr >> 16) == stat.S_IFLNK


def _apply_archive_mode(*, path: Path, mode: int) -> None:
    filesystem_path = Path(_filesystem_path(path))
    if mode and filesystem_path.is_file():
        filesystem_path.chmod(stat.S_IMODE(mode))


def _archive_plugin_root(extract_dir: Path) -> Path:
    entries = [
        item
        for item in extract_dir.iterdir()
        if item.name not in _IGNORED_COPY_DIR_NAMES
    ]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return extract_dir


def _adapt_installed_plugin_tree(
    *,
    plugin_root: Path,
    adapter: str,
    app_config_dir: Path,
    source_version: str | None,
) -> None:
    adapt_plugin_tree(plugin_root=plugin_root, adapter=adapter)
    adapt_openclaw_plugin_tree(
        plugin_root=plugin_root,
        adapter=adapter,
        manifest_config_dir_name=app_config_dir.name,
        source_version=source_version,
    )


def _url_opener() -> OpenerDirector:
    return build_opener(ProxyHandler(_urllib_proxy_map()))


def _urllib_proxy_map() -> dict[str, str]:
    env = load_proxy_env_config().normalized_env()
    proxies: dict[str, str] = {}
    http_proxy = env.get("HTTP_PROXY")
    https_proxy = env.get("HTTPS_PROXY")
    all_proxy = env.get("ALL_PROXY")
    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy
    if all_proxy:
        proxies.setdefault("http", all_proxy)
        proxies.setdefault("https", all_proxy)
        proxies["all"] = all_proxy
    return proxies


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
    shutil.rmtree(_filesystem_path(path), onexc=_make_writable_and_retry)


def _filesystem_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    resolved = str(path.expanduser().resolve())
    if resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved[2:]
    return "\\\\?\\" + resolved


def _make_writable_and_retry(
    function: Callable[[str], object],
    path: str,
    excinfo: BaseException,
) -> None:
    _ = excinfo
    resolved_path = Path(path)
    resolved_path.chmod(stat.S_IWRITE)
    function(path)
