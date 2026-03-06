# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import httpx

from agent_teams.tools.workspace_tools.ripgrep_errors import (
    DownloadFailedError,
    ExtractionFailedError,
    UnsupportedPlatformError,
)
from agent_teams.tools.workspace_tools.ripgrep_types import GrepMatch, GrepResult

VERSION = "14.1.1"
BIN_DIR = Path.home() / ".agent-teams" / "bin"
_rg_path_cache: Path | None = None

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


async def get_rg_path() -> Path:
    global _rg_path_cache

    if _rg_path_cache and _rg_path_cache.is_file():
        return _rg_path_cache

    system_rg = shutil.which("rg")
    if system_rg:
        system_path = Path(system_rg)
        if system_path.is_file():
            _rg_path_cache = system_path
            return system_path

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    extension = ".exe" if os.name == "nt" else ""
    local_path = BIN_DIR / f"rg{extension}"

    if local_path.is_file():
        _rg_path_cache = local_path
        return local_path

    await _download_rg(local_path)
    _rg_path_cache = local_path
    return local_path


def clear_rg_path_cache() -> None:
    global _rg_path_cache
    _rg_path_cache = None


async def _download_rg(target: Path) -> None:
    key = _get_platform_key()
    config = PLATFORM_MAP.get(key)
    if config is None:
        raise UnsupportedPlatformError(key)

    filename = f"ripgrep-{VERSION}-{config['platform']}.{config['extension']}"
    url = (
        f"https://github.com/BurntSushi/ripgrep/releases/download/{VERSION}/{filename}"
    )

    async with httpx.AsyncClient(follow_redirects=True) as client:
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
                extracted.rename(target)
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

    result = subprocess.run(
        [str(rg), *[arg for arg in args if arg], str(cwd)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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
                line_text=parts[2],
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
    return files, truncated
