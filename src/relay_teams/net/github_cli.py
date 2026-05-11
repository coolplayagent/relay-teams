# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import io
import os
import tarfile
import zipfile
from pathlib import Path

from relay_teams.binary_tools import (
    BinaryToolId,
    BinaryToolService,
)
from relay_teams.binary_tools.service import (
    GITHUB_CLI_PLATFORM_MAP,
    GITHUB_CLI_VERSION,
    get_platform_key,
)
from relay_teams.logger import get_logger
from relay_teams.paths import get_app_bin_dir
from relay_teams.net.github_cli_errors import (
    ExtractionFailedError,
    GitHubCliNotFoundError,
)

LOGGER = get_logger(__name__)

VERSION = GITHUB_CLI_VERSION
BIN_DIR: Path | None = None
_gh_path_cache: Path | None = None
_gh_path_lock = asyncio.Lock()

PLATFORM_MAP = GITHUB_CLI_PLATFORM_MAP

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
    return get_platform_key()


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
        path = _build_binary_tool_service().resolve_existing_tool_path(
            BinaryToolId.GITHUB_CLI
        )
        if path is not None:
            _gh_path_cache = path
            return path
    except Exception:
        return None
    return None


def resolve_system_gh_path() -> Path | None:
    return _build_binary_tool_service().resolve_system_tool_path(
        BinaryToolId.GITHUB_CLI
    )


def get_bundled_gh_path() -> Path | None:
    local_path = _build_binary_tool_service().managed_target_path(
        BinaryToolId.GITHUB_CLI
    )
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
    await _build_binary_tool_service().download_tool_to_path(
        BinaryToolId.GITHUB_CLI,
        target,
    )


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


def _build_binary_tool_service() -> BinaryToolService:
    return BinaryToolService(bin_dir=BIN_DIR)
