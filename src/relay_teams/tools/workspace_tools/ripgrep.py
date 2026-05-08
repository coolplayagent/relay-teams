# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import io
import os
import platform
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

from relay_teams.logger import get_logger
from relay_teams.net.clients import create_async_http_client
from relay_teams.paths import get_app_bin_dir
from relay_teams.tools.workspace_tools.ripgrep_errors import (
    DownloadFailedError,
    ExtractionFailedError,
    RipgrepExecutionError,
    RipgrepNotFoundError,
    UnsupportedPlatformError,
)
from relay_teams.tools.workspace_tools.ripgrep_types import GrepMatch, GrepResult

LOGGER = get_logger(__name__)

VERSION = "14.1.1"
BIN_DIR: Path | None = None
_rg_path_lock = asyncio.Lock()
_RG_PATH_ENV = "RELAY_TEAMS_RIPGREP_PATH"


class _RipgrepPathCache:  # pragma: no cover
    def __init__(self) -> None:
        self.path: Path | None = None


_rg_path_cache = _RipgrepPathCache()

PLATFORM_MAP = {
    "arm64-darwin": {"platform": "aarch64-apple-darwin", "extension": "tar.gz"},
    "arm64-linux": {"platform": "aarch64-unknown-linux-gnu", "extension": "tar.gz"},
    "x64-darwin": {"platform": "x86_64-apple-darwin", "extension": "tar.gz"},
    "x64-linux": {"platform": "x86_64-unknown-linux-musl", "extension": "tar.gz"},
    "x64-windows": {"platform": "x86_64-pc-windows-msvc", "extension": "zip"},
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


async def get_rg_path() -> Path:  # pragma: no cover
    """Return path to a ripgrep binary, preferring the bundled v14.1.1.

    Resolution order:
      1. In-process cache (already resolved)
      2. Previously downloaded binary under ``BIN_DIR``
      3. Download the bundled version
      4. Fall back to system ``rg`` only when the download is unavailable
    """
    cached_path = _rg_path_cache.path
    if cached_path is not None and cached_path.is_file():
        return cached_path

    env_path = _resolve_env_rg_path()
    if env_path is not None:
        _rg_path_cache.path = env_path
        return env_path

    bin_dir = BIN_DIR if BIN_DIR is not None else get_app_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    extension = ".exe" if os.name == "nt" else ""
    local_path = bin_dir / f"rg{extension}"

    if local_path.is_file():
        _rg_path_cache.path = local_path
        return local_path

    async with _rg_path_lock:
        cached_path = _rg_path_cache.path
        if cached_path is not None and cached_path.is_file():
            return cached_path
        if local_path.is_file():
            _rg_path_cache.path = local_path
            return local_path
        try:
            await _download_rg(local_path)
            _rg_path_cache.path = local_path
            return local_path
        except Exception as exc:
            LOGGER.warning("Failed to download bundled ripgrep: %s", exc)

    # Fall back to system rg only when the bundled binary is unavailable.
    for system_name in ("rg", "rg.exe"):
        system_rg = shutil.which(system_name)
        if not system_rg:
            continue
        system_path = Path(system_rg)
        if system_path.is_file() and _is_usable_rg(system_path):
            LOGGER.info("Using system ripgrep at %s", system_path)
            _rg_path_cache.path = system_path
            return system_path

    raise RipgrepNotFoundError()


def clear_rg_path_cache() -> None:
    _rg_path_cache.path = None


def _resolve_env_rg_path() -> Path | None:  # pragma: no cover
    raw_path = os.getenv(_RG_PATH_ENV)
    if raw_path is None or not raw_path.strip():
        return None
    candidate = Path(raw_path).expanduser()
    if candidate.is_file() and _is_usable_rg(candidate):
        return candidate
    LOGGER.warning("Ignoring unusable ripgrep path from %s: %s", _RG_PATH_ENV, raw_path)
    return None


def _is_usable_rg(path: Path) -> bool:  # pragma: no cover
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


async def _download_rg(target: Path) -> None:
    key = _get_platform_key()
    config = PLATFORM_MAP.get(key)
    if config is None:
        raise UnsupportedPlatformError(key)

    filename = f"ripgrep-{VERSION}-{config['platform']}.{config['extension']}"
    url = (
        f"https://github.com/BurntSushi/ripgrep/releases/download/{VERSION}/{filename}"
    )

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
        os.chmod(target, 0o755)  # nosec B103 - executable permission needed for downloaded binary


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

    result = await asyncio.to_thread(
        _run_grep_process,
        command=(str(rg), *[arg for arg in args if arg], str(cwd)),
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

    return await asyncio.to_thread(
        _enumerate_files_process,
        command=(str(rg), *args, str(cwd)),
        limit=limit,
    )


def _run_grep_process(  # pragma: no cover
    *,
    command: tuple[str, ...],
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_ripgrep_timeout_seconds(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RipgrepExecutionError(
            returncode=124,
            stderr=f"ripgrep timed out after {_ripgrep_timeout_seconds():.1f}s",
        ) from exc


def _enumerate_files_process(  # pragma: no cover
    *,
    command: tuple[str, ...],
    limit: int,
) -> tuple[list[Path], bool]:
    process = subprocess.Popen(
        list(command),
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

    try:
        process.wait(timeout=_ripgrep_timeout_seconds())
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        raise RipgrepExecutionError(
            returncode=124,
            stderr=f"ripgrep timed out after {_ripgrep_timeout_seconds():.1f}s",
        ) from exc

    # Only check for errors when we did not truncate (terminate sends SIGTERM
    # which results in a non-zero exit code that is expected).
    if not truncated and process.returncode >= 2:
        stderr = process.stderr.read() if process.stderr else ""
        raise RipgrepExecutionError(
            returncode=process.returncode,
            stderr=stderr.strip() if isinstance(stderr, str) else "",
        )

    return files, truncated


def _ripgrep_timeout_seconds() -> float:  # pragma: no cover
    raw_value = os.getenv("RELAY_TEAMS_RIPGREP_TIMEOUT_SECONDS")
    if raw_value is None:
        return 5.0
    try:
        value = float(raw_value)
    except ValueError:
        return 5.0
    return max(0.1, value)
