# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import io
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

from relay_teams.binary_tools import (
    BinaryToolId,
    BinaryToolService,
)
from relay_teams.binary_tools.service import (
    RIPGREP_PLATFORM_MAP,
    RIPGREP_VERSION,
    get_platform_key,
)
from relay_teams.logger import get_logger
from relay_teams.paths import get_app_bin_dir
from relay_teams.tools.workspace_tools.ripgrep_errors import (
    ExtractionFailedError,
    RipgrepExecutionError,
    RipgrepNotFoundError,
)
from relay_teams.tools.workspace_tools.ripgrep_types import GrepMatch, GrepResult

LOGGER = get_logger(__name__)

VERSION = RIPGREP_VERSION
BIN_DIR: Path | None = None
_rg_path_cache: Path | None = None
_rg_path_lock = asyncio.Lock()

PLATFORM_MAP = RIPGREP_PLATFORM_MAP

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


async def get_rg_path() -> Path:
    """Return path to a ripgrep binary, preferring the bundled v14.1.1.

    Resolution order:
      1. In-process cache (already resolved)
      2. Previously downloaded binary under ``BIN_DIR``
      3. Download the bundled version
      4. Fall back to system ``rg`` only when the download is unavailable
    """
    global _rg_path_cache

    if _rg_path_cache and _rg_path_cache.is_file():
        return _rg_path_cache

    bin_dir = BIN_DIR if BIN_DIR is not None else get_app_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    local_path = _build_binary_tool_service().managed_target_path(BinaryToolId.RIPGREP)

    if local_path.is_file():
        _rg_path_cache = local_path
        return local_path

    async with _rg_path_lock:
        if _rg_path_cache and _rg_path_cache.is_file():
            return _rg_path_cache
        if local_path.is_file():
            _rg_path_cache = local_path
            return local_path
        try:
            await _download_rg(local_path)
            _rg_path_cache = local_path
            return local_path
        except Exception as exc:
            LOGGER.warning("Failed to download bundled ripgrep: %s", exc)

    system_rg = shutil.which("rg")
    if system_rg:
        system_path = Path(system_rg)
        if system_path.is_file():
            LOGGER.info("Using system ripgrep at %s", system_path)
            _rg_path_cache = system_path
            return system_path

    raise RipgrepNotFoundError()


def clear_rg_path_cache() -> None:
    global _rg_path_cache
    _rg_path_cache = None


async def _download_rg(target: Path) -> None:
    await _build_binary_tool_service().download_tool_to_path(
        BinaryToolId.RIPGREP,
        target,
    )


def _extract_tarball(content: bytes, target: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(content)) as tar:
        for member in tar.getmembers():
            if member.name.endswith("rg") or member.name.endswith("rg.exe"):
                member.name = target.name
                tar.extract(member, target.parent)
                return
        raise ExtractionFailedError("rg not found in tarball")


def _extract_zip(content: bytes, target: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist():
            if name.endswith("rg.exe"):
                archive.extract(name, target.parent)
                extracted = target.parent / name
                extracted.replace(target)
                return
        raise ExtractionFailedError("rg.exe not found in zip")


def _build_binary_tool_service() -> BinaryToolService:
    return BinaryToolService(bin_dir=BIN_DIR)


async def grep_search(
    cwd: Path,
    pattern: str,
    *,
    glob: str | None = None,
    hidden: bool = True,
    case_sensitive: bool = True,
    limit: int = 100,
) -> GrepResult:
    rg = await get_rg_path()

    args = [
        "-nH",
        "--hidden" if hidden else "",
        "--field-match-separator=|",
        "--max-count",
        str(limit),
        "--regexp",
        pattern,
    ]
    if glob:
        args.extend(["--glob", glob])
    if not case_sensitive:
        args.append("-i")

    result = subprocess.run(
        [str(rg), *[arg for arg in args if arg], str(cwd)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    # ripgrep exit codes: 0 = matches found, 1 = no matches, 2+ = error
    if result.returncode >= 2:
        raise RipgrepExecutionError(
            returncode=result.returncode,
            stderr=result.stderr.strip(),
        )

    matches: list[GrepMatch] = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 3:
            continue
        matches.append(
            GrepMatch(
                path=parts[0],
                line_num=int(parts[1]),
                line_text="|".join(parts[2:]),
            )
        )

    return GrepResult(
        matches=matches,
        truncated=len(matches) >= limit,
        total=len(matches),
    )


async def enumerate_files(
    cwd: Path,
    pattern: str,
    *,
    hidden: bool = True,
    follow: bool = False,
    limit: int = 100,
) -> tuple[list[Path], bool]:
    rg = await get_rg_path()

    args = [
        "--files",
        "--glob=!.git/*",
    ]
    if hidden:
        args.append("--hidden")
    if follow:
        args.append("--follow")
    args.extend(["--glob", pattern])

    process = subprocess.Popen(
        [str(rg), *args, str(cwd)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    files: list[Path] = []
    truncated = False
    if process.stdout is None:
        return files, truncated

    for line in process.stdout:
        if len(files) >= limit:
            truncated = True
            process.terminate()
            break
        files.append(Path(line.strip()))

    process.wait()

    # Only check for errors when we did not truncate (terminate sends SIGTERM
    # which results in a non-zero exit code that is expected).
    if not truncated and process.returncode >= 2:
        stderr = process.stderr.read() if process.stderr else ""
        raise RipgrepExecutionError(
            returncode=process.returncode,
            stderr=stderr.strip() if isinstance(stderr, str) else "",
        )

    return files, truncated
