from __future__ import annotations

import io
import os
import shutil
import subprocess
import zipfile
import tarfile
from pathlib import Path
import httpx

from agent_teams.tools.ripgrep_errors import (
    UnsupportedPlatformError,
    DownloadFailedError,
    ExtractionFailedError,
)
from agent_teams.tools.ripgrep_types import GrepMatch, GrepResult

VERSION = "14.1.1"
BIN_DIR = Path.home() / ".agent-teams" / "bin"
_RG_PATH_CACHE: Path | None = None

PLATFORM_MAP = {
    "arm64-darwin": {"platform": "aarch64-apple-darwin", "extension": "tar.gz"},
    "arm64-linux": {"platform": "aarch64-unknown-linux-gnu", "extension": "tar.gz"},
    "x64-darwin": {"platform": "x86_64-apple-darwin", "extension": "tar.gz"},
    "x64-linux": {"platform": "x86_64-unknown-linux-musl", "extension": "tar.gz"},
    "AMD64-windows": {"platform": "x86_64-pc-windows-msvc", "extension": "zip"},
}


def _get_platform_key() -> str:
    import platform

    return f"{platform.machine()}-{platform.system().lower()}"


async def get_rg_path() -> Path:
    """获取 ripgrep 可执行文件路径 (带缓存)

    优先级:
    1. 系统 ripgrep (shutil.which)
    2. 本地缓存 (~/.agent-teams/bin/rg)
    3. 自动下载

    Returns:
        Path: ripgrep 可执行文件路径

    Raises:
        UnsupportedPlatformError: 不支持的平台
        DownloadFailedError: 下载失败
        ExtractionFailedError: 解压失败
    """
    # 1. 系统 ripgrep
    global _RG_PATH_CACHE

    if _RG_PATH_CACHE and _RG_PATH_CACHE.is_file():
        return _RG_PATH_CACHE

    system_rg = shutil.which("rg")
    if system_rg:
        p = Path(system_rg)
        if p.is_file():
            _RG_PATH_CACHE = p
            return p

    # 2. 本地缓存
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    ext = ".exe" if os.name == "nt" else ""
    local_path = BIN_DIR / f"rg{ext}"

    if local_path.exists() and local_path.is_file():
        _RG_PATH_CACHE = local_path
        return local_path

    # 3. 下载
    await _download_rg(local_path)
    _RG_PATH_CACHE = local_path
    return local_path


def _clear_get_rg_path_cache() -> None:
    global _RG_PATH_CACHE
    _RG_PATH_CACHE = None


get_rg_path.cache_clear = _clear_get_rg_path_cache  # type: ignore[attr-defined]


async def _download_rg(target: Path) -> None:
    """下载并解压 ripgrep"""
    key = _get_platform_key()
    config = PLATFORM_MAP.get(key)
    if not config:
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
    """解压 tar.gz"""
    with tarfile.open(fileobj=io.BytesIO(content)) as tar:
        for member in tar.getmembers():
            name = member.name
            if name.endswith("rg") or name.endswith("rg.exe"):
                member.name = target.name
                tar.extract(member, target.parent)
                return
        raise ExtractionFailedError("rg not found in tarball")


def _extract_zip(content: bytes, target: Path) -> None:
    """解压 zip"""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for name in zf.namelist():
            if name.endswith("rg.exe"):
                zf.extract(name, target.parent)
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
    """执行 grep 搜索

    Args:
        cwd: 搜索目录
        pattern: 正则表达式模式
        glob: 文件名过滤 (如 "*.py")
        hidden: 是否包含隐藏文件
        case_sensitive: 是否大小写敏感
        limit: 最大结果数

    Returns:
        GrepResult: 搜索结果
    """
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

    args = [a for a in args if a]

    result = subprocess.run(
        [str(rg), *args, str(cwd)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    matches = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
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

    truncated = len(matches) >= limit
    return GrepResult(
        matches=matches,
        truncated=truncated,
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
    """枚举匹配 glob 模式的文件

    Args:
        cwd: 搜索目录
        pattern: glob 模式
        hidden: 是否包含隐藏文件
        follow: 是否跟随符号链接
        limit: 最大文件数

    Returns:
        (files, truncated): 文件列表和是否截断
    """
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

    proc = subprocess.Popen(
        [str(rg), *args, str(cwd)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    files: list[Path] = []
    truncated = False

    if proc.stdout is None:
        return files, truncated

    for line in proc.stdout:
        if len(files) >= limit:
            truncated = True
            proc.terminate()
            break
        files.append(Path(line.strip()))

    proc.wait()
    return files, truncated
