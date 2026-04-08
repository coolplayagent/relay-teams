# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import io
import os
import shutil
import tarfile
import zipfile
from pathlib import Path

from relay_teams.logger import get_logger
from relay_teams.net.clients import create_async_http_client
from relay_teams.paths import get_app_bin_dir
from relay_teams.sessions.runs.background_tasks.github_cli_errors import (
    DownloadFailedError,
    ExtractionFailedError,
    GitHubCliNotFoundError,
    UnsupportedPlatformError,
)

LOGGER = get_logger(__name__)

VERSION = "2.88.1"
BIN_DIR: Path | None = None
_gh_path_cache: Path | None = None
_gh_path_lock = asyncio.Lock()

PLATFORM_MAP = {
    "arm64-darwin": {"platform": "macOS_arm64", "extension": "zip"},
    "arm64-linux": {"platform": "linux_arm64", "extension": "tar.gz"},
    "x64-darwin": {"platform": "macOS_amd64", "extension": "zip"},
    "x64-linux": {"platform": "linux_amd64", "extension": "tar.gz"},
    "x64-windows": {"platform": "windows_amd64", "extension": "zip"},
}

_ARCH_ALIASES = {
    "x86_64": "x64",
    "amd64": "x64",
    "x64": "x64",
    "aarch64": "arm64",
    "arm64": "arm64",
}

_SYSTEM_ALIASES = {
    "darwin": "darwin",
    "linux": "linux",
    "windows": "windows",
}


def _get_platform_key() -> str:
    import platform

    raw_arch = platform.machine().strip().lower()
    raw_system = platform.system().strip().lower()

    arch = _ARCH_ALIASES.get(raw_arch, raw_arch)
    system = _SYSTEM_ALIASES.get(raw_system, raw_system)
    if (
        system.startswith("mingw")
        or system.startswith("msys")
        or system.startswith("cygwin")
    ):
        system = "windows"

    return f"{arch}-{system}"


async def get_gh_path() -> Path:
    global _gh_path_cache

    existing_path = resolve_existing_gh_path()
    if existing_path is not None:
        return existing_path

    async with _gh_path_lock:
        existing_path = resolve_existing_gh_path()
        if existing_path is not None:
            return existing_path
        download_target = _bundled_gh_target_path(ensure_parent=True)
        try:
            await _download_gh(download_target)
            _gh_path_cache = download_target
            return download_target
        except Exception as exc:
            LOGGER.warning("Failed to download bundled GitHub CLI: %s", exc)

    raise GitHubCliNotFoundError()


def clear_gh_path_cache() -> None:
    global _gh_path_cache
    _gh_path_cache = None


def resolve_existing_gh_path() -> Path | None:
    global _gh_path_cache

    try:
        if _gh_path_cache and _gh_path_cache.is_file():
            return _gh_path_cache

        system_path = resolve_system_gh_path()
        if system_path is not None:
            LOGGER.info("Using system GitHub CLI at %s", system_path)
            _gh_path_cache = system_path
            return system_path

        local_path = get_bundled_gh_path()
        if local_path is not None:
            _gh_path_cache = local_path
            return local_path
    except Exception:
        return None
    return None


def resolve_system_gh_path() -> Path | None:
    system_gh = shutil.which("gh")
    if not system_gh:
        return None
    system_path = Path(system_gh)
    if not system_path.is_file():
        return None
    return system_path


def get_bundled_gh_path() -> Path | None:
    local_path = _bundled_gh_target_path()
    if local_path.is_file():
        return local_path
    return None


def _bundled_gh_target_path(*, ensure_parent: bool = False) -> Path:
    bin_dir = BIN_DIR if BIN_DIR is not None else get_app_bin_dir()
    if ensure_parent:
        bin_dir.mkdir(parents=True, exist_ok=True)
    extension = ".exe" if os.name == "nt" else ""
    return bin_dir / f"gh{extension}"


async def _download_gh(target: Path) -> None:
    key = _get_platform_key()
    config = PLATFORM_MAP.get(key)
    if config is None:
        raise UnsupportedPlatformError(key)

    filename = f"gh_{VERSION}_{config['platform']}.{config['extension']}"
    url = f"https://github.com/cli/cli/releases/download/v{VERSION}/{filename}"

    async with create_async_http_client(follow_redirects=True) as client:
        response = await client.get(url)
        if response.status_code != 200:
            raise DownloadFailedError(url, response.status_code)
        content = response.content

    if config["extension"] == "tar.gz":
        _extract_tarball(content, target)
    else:
        _extract_zip(content, target)

    if os.name != "nt":
        os.chmod(target, 0o755)


def _extract_tarball(content: bytes, target: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(content)) as tar:
        for member in tar.getmembers():
            if member.isfile() and (
                member.name.endswith("/bin/gh") or member.name.endswith("/bin/gh.exe")
            ):
                extracted = tar.extractfile(member)
                if extracted is None:
                    break
                target.write_bytes(extracted.read())
                return
        raise ExtractionFailedError("gh not found in tarball")


def _extract_zip(content: bytes, target: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist():
            if name.endswith("/bin/gh") or name.endswith("/bin/gh.exe"):
                with archive.open(name) as source_handle:
                    target.write_bytes(source_handle.read())
                return
        raise ExtractionFailedError("gh not found in zip")
