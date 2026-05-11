# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import datetime, timezone
import io
import os
from os import pathsep
from pathlib import Path
import platform
import subprocess
import tarfile
import threading
from types import TracebackType
from typing import Protocol, cast
import uuid
import zipfile

import httpx

from relay_teams.binary_tools.models import (
    BinaryToolDownloadJob,
    BinaryToolDownloadStatus,
    BinaryToolId,
    BinaryToolItem,
    BinaryToolListResponse,
    BinaryToolPathSource,
    BinaryToolSourceKind,
    BinaryToolStatus,
)
from relay_teams.env.clawhub_cli import (
    ClawHubCliInstallResult,
    install_clawhub_via_npm,
    resolve_existing_clawhub_path,
    resolve_npm_global_clawhub_path,
    resolve_system_clawhub_path,
)
from relay_teams.env.clawhub_env import build_clawhub_subprocess_env
from relay_teams.logger import get_logger
from relay_teams.net.clients import create_async_http_client
from relay_teams.paths import get_app_bin_dir

LOGGER = get_logger(__name__)

RIPGREP_VERSION = "14.1.1"
GITHUB_CLI_VERSION = "2.88.1"

RIPGREP_PLATFORM_MAP: Mapping[str, Mapping[str, str]] = {
    "arm64-darwin": {"platform": "aarch64-apple-darwin", "extension": "tar.gz"},
    "arm64-linux": {"platform": "aarch64-unknown-linux-gnu", "extension": "tar.gz"},
    "x64-darwin": {"platform": "x86_64-apple-darwin", "extension": "tar.gz"},
    "x64-linux": {"platform": "x86_64-unknown-linux-musl", "extension": "tar.gz"},
    "x64-windows": {"platform": "x86_64-pc-windows-msvc", "extension": "zip"},
}

GITHUB_CLI_PLATFORM_MAP: Mapping[str, Mapping[str, str]] = {
    "arm64-darwin": {"platform": "macOS_arm64", "extension": "zip"},
    "arm64-linux": {"platform": "linux_arm64", "extension": "tar.gz"},
    "x64-darwin": {"platform": "macOS_amd64", "extension": "zip"},
    "x64-linux": {"platform": "linux_amd64", "extension": "tar.gz"},
    "x64-windows": {"platform": "windows_amd64", "extension": "zip"},
}

_ARCH_ALIASES: Mapping[str, str] = {
    "x86_64": "x64",
    "amd64": "x64",
    "x64": "x64",
    "aarch64": "arm64",
    "arm64": "arm64",
}

_SYSTEM_ALIASES: Mapping[str, str] = {
    "darwin": "darwin",
    "linux": "linux",
    "windows": "windows",
}

_VERSION_TIMEOUT_SECONDS = 5.0
_NPM_INSTALL_TIMEOUT_SECONDS = 180.0
_MANAGED_TOOL_INSTALL_LOCK_POLL_SECONDS = 0.05
_MANAGED_TOOL_INSTALL_LOCKS_LOCK = threading.Lock()
_MANAGED_TOOL_INSTALL_LOCKS: dict[tuple[BinaryToolId, str], threading.Lock] = {}
_CLAWHUB_INSTALL_LOCK = threading.Lock()


class BinaryToolHttpResponse(Protocol):
    status_code: int
    headers: httpx.Headers

    def aiter_bytes(self) -> AsyncIterator[bytes]:
        raise NotImplementedError  # pragma: no cover


class BinaryToolHttpStream(Protocol):
    async def __aenter__(self) -> BinaryToolHttpResponse:
        raise NotImplementedError  # pragma: no cover

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        raise NotImplementedError  # pragma: no cover


class BinaryToolHttpClient(Protocol):
    async def __aenter__(self) -> "BinaryToolHttpClient":
        raise NotImplementedError  # pragma: no cover

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        raise NotImplementedError  # pragma: no cover

    def stream(self, method: str, url: httpx.URL | str) -> BinaryToolHttpStream:
        raise NotImplementedError  # pragma: no cover


class UnsupportedBinaryToolError(ValueError):
    def __init__(self, tool_id: str) -> None:
        super().__init__(f"Unknown binary tool: {tool_id}")


class BinaryToolUnavailableError(RuntimeError):
    pass


class BinaryToolDownloadError(RuntimeError):
    pass


_DEFAULT_HTTP_CLIENT_FACTORY = cast(
    Callable[..., BinaryToolHttpClient],
    cast(object, create_async_http_client),
)


class BinaryToolService:
    def __init__(
        self,
        *,
        bin_dir: Path | None = None,
        create_http_client: Callable[
            ..., BinaryToolHttpClient
        ] = _DEFAULT_HTTP_CLIENT_FACTORY,
        install_clawhub: Callable[
            ..., ClawHubCliInstallResult
        ] = install_clawhub_via_npm,
        config_dir: Path | None = None,
        build_clawhub_install_env: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self._bin_dir = bin_dir
        self._create_http_client = create_http_client
        self._install_clawhub = install_clawhub
        self._config_dir = None if config_dir is None else config_dir.expanduser()
        self._build_clawhub_install_env = build_clawhub_install_env
        self._jobs: dict[str, BinaryToolDownloadJob] = {}
        self._running_job_by_tool: dict[BinaryToolId, str] = {}
        self._latest_job_by_tool: dict[BinaryToolId, str] = {}
        self._locks = {
            BinaryToolId.RIPGREP: asyncio.Lock(),
            BinaryToolId.GITHUB_CLI: asyncio.Lock(),
            BinaryToolId.CLAWHUB: asyncio.Lock(),
        }

    async def list_tools(self) -> BinaryToolListResponse:
        items = await asyncio.gather(
            *(asyncio.to_thread(self.inspect_tool, tool_id) for tool_id in BinaryToolId)
        )
        return BinaryToolListResponse(items=tuple(items))

    def inspect_tool(self, tool_id: BinaryToolId | str) -> BinaryToolItem:
        resolved_tool_id = self._normalize_tool_id(tool_id)
        running_job_id = self._running_job_by_tool.get(resolved_tool_id)
        if running_job_id is not None:
            return self._item(
                resolved_tool_id,
                status=BinaryToolStatus.DOWNLOADING,
                download_job_id=running_job_id,
            )

        resolved_path = self.resolve_existing_tool_path(resolved_tool_id)
        if resolved_path is None:
            latest_job_id = self._latest_job_by_tool.get(resolved_tool_id)
            latest_job = (
                self._jobs.get(latest_job_id) if latest_job_id is not None else None
            )
            if (
                latest_job is not None
                and latest_job.status == BinaryToolDownloadStatus.FAILED
            ):
                return self._item(
                    resolved_tool_id,
                    status=BinaryToolStatus.ERROR,
                    download_job_id=latest_job.job_id,
                    error_message=latest_job.error_message,
                )
            return self._item(resolved_tool_id, status=BinaryToolStatus.MISSING)

        return self._item(
            resolved_tool_id,
            status=BinaryToolStatus.READY,
            path=resolved_path,
            path_source=self.resolve_path_source(resolved_tool_id, resolved_path),
            version=self.read_tool_version(resolved_tool_id, resolved_path),
        )

    async def ensure_tool_path(
        self,
        tool_id: BinaryToolId | str,
        *,
        install_env: Mapping[str, str] | None = None,
        install_timeout_seconds: float = _NPM_INSTALL_TIMEOUT_SECONDS,
    ) -> Path:
        resolved_tool_id = self._normalize_tool_id(tool_id)
        existing = self.resolve_existing_tool_path(resolved_tool_id)
        if existing is not None:
            return existing

        async with self._locks[resolved_tool_id]:
            existing = self.resolve_existing_tool_path(resolved_tool_id)
            if existing is not None:
                return existing
            if resolved_tool_id == BinaryToolId.CLAWHUB:
                clawhub_install_env = (
                    install_env
                    if install_env is not None
                    else self._default_clawhub_install_env()
                )
                return await asyncio.to_thread(
                    self._install_clawhub_with_lock,
                    timeout_seconds=install_timeout_seconds,
                    base_env=clawhub_install_env,
                )
            target = self.managed_target_path(resolved_tool_id, ensure_parent=True)
            try:
                await self.download_tool_to_path(resolved_tool_id, target)
                return target
            except Exception as exc:
                if resolved_tool_id == BinaryToolId.RIPGREP:
                    system_path = self.resolve_system_tool_path(resolved_tool_id)
                    if system_path is not None:
                        LOGGER.info("Using system ripgrep at %s", system_path)
                        return system_path
                raise BinaryToolUnavailableError(
                    str(exc) or "Binary tool is unavailable."
                ) from exc

    async def start_download(
        self, tool_id: BinaryToolId | str
    ) -> BinaryToolDownloadJob:
        resolved_tool_id = self._normalize_tool_id(tool_id)
        running_job_id = self._running_job_by_tool.get(resolved_tool_id)
        if running_job_id is not None:
            return self._jobs[running_job_id]

        existing_item = await asyncio.to_thread(self.inspect_tool, resolved_tool_id)
        if existing_item.status == BinaryToolStatus.READY:
            return self._create_completed_job(
                resolved_tool_id,
                message=f"{existing_item.display_name} is already available.",
                path=existing_item.path,
            )

        running_job_id = self._running_job_by_tool.get(resolved_tool_id)
        if running_job_id is not None:
            return self._jobs[running_job_id]

        job = BinaryToolDownloadJob(
            job_id=f"bin_{uuid.uuid4().hex}",
            tool_id=resolved_tool_id,
            status=BinaryToolDownloadStatus.QUEUED,
            progress_percent=0,
            message=f"Queued {self.display_name(resolved_tool_id)} download.",
        )
        self._jobs[job.job_id] = job
        self._running_job_by_tool[resolved_tool_id] = job.job_id
        self._latest_job_by_tool[resolved_tool_id] = job.job_id
        asyncio.create_task(self._run_download_job(job.job_id))
        return job

    def get_download_job(self, job_id: str) -> BinaryToolDownloadJob:
        job = self._jobs.get(str(job_id or "").strip())
        if job is None:
            raise KeyError(f"Unknown binary tool download job: {job_id}")
        return job

    async def download_tool_to_path(
        self,
        tool_id: BinaryToolId | str,
        target: Path,
        *,
        job_id: str | None = None,
    ) -> None:
        resolved_tool_id = self._normalize_tool_id(tool_id)
        if resolved_tool_id == BinaryToolId.CLAWHUB:
            raise UnsupportedBinaryToolError(resolved_tool_id.value)

        install_lock = _managed_tool_install_lock(resolved_tool_id, target)
        await _acquire_managed_tool_install_lock(install_lock)
        try:
            if target.is_file():
                return
            platform_config = self._platform_config(resolved_tool_id)
            url = self._download_url(resolved_tool_id, platform_config)
            content = await self._download_bytes(url, job_id=job_id)
            extension = platform_config["extension"]
            archive_executable_name = self._archive_executable_name(
                resolved_tool_id, platform_config
            )
            if extension == "tar.gz":
                self._extract_tarball(
                    content,
                    target,
                    executable_name=archive_executable_name,
                )
            else:
                self._extract_zip(
                    content,
                    target,
                    executable_name=archive_executable_name,
                )
            if os.name != "nt":
                os.chmod(target, 0o755)  # nosec B103 - executable permission needed
        finally:
            install_lock.release()

    def resolve_existing_tool_path(self, tool_id: BinaryToolId | str) -> Path | None:
        resolved_tool_id = self._normalize_tool_id(tool_id)
        if resolved_tool_id == BinaryToolId.CLAWHUB:
            return resolve_existing_clawhub_path()

        managed_path = self.managed_target_path(resolved_tool_id)
        if resolved_tool_id == BinaryToolId.RIPGREP and managed_path.is_file():
            return managed_path
        if resolved_tool_id == BinaryToolId.RIPGREP:
            return None
        if resolved_tool_id == BinaryToolId.GITHUB_CLI:
            system_path = self.resolve_system_tool_path(resolved_tool_id)
            if system_path is not None:
                return system_path
            if managed_path.is_file():
                return managed_path
            return None

        system_path = self.resolve_system_tool_path(resolved_tool_id)
        if system_path is not None:
            return system_path
        return managed_path if managed_path.is_file() else None

    def resolve_system_tool_path(self, tool_id: BinaryToolId | str) -> Path | None:
        resolved_tool_id = self._normalize_tool_id(tool_id)
        executable_name = self.executable_name(
            resolved_tool_id, include_extension=False
        )
        for path_entry in os.environ.get("PATH", "").split(pathsep):
            if not path_entry:
                continue
            directory = Path(path_entry)
            for candidate_name in self._system_executable_names(executable_name):
                candidate = directory / candidate_name
                if candidate.is_file() and (
                    os.name == "nt" or os.access(candidate, os.X_OK)
                ):
                    return candidate
        return None

    def resolve_path_source(
        self,
        tool_id: BinaryToolId | str,
        path: Path,
    ) -> BinaryToolPathSource:
        resolved_tool_id = self._normalize_tool_id(tool_id)
        resolved_path = path.expanduser()
        if resolved_tool_id == BinaryToolId.CLAWHUB:
            system_path = resolve_system_clawhub_path()
            if system_path is not None and system_path == resolved_path:
                return BinaryToolPathSource.SYSTEM
            npm_path = resolve_npm_global_clawhub_path()
            if npm_path is not None and npm_path == resolved_path:
                return BinaryToolPathSource.NPM_GLOBAL
            return BinaryToolPathSource.SYSTEM
        return (
            BinaryToolPathSource.MANAGED
            if resolved_path == self.managed_target_path(resolved_tool_id)
            else BinaryToolPathSource.SYSTEM
        )

    def managed_target_path(
        self,
        tool_id: BinaryToolId | str,
        *,
        ensure_parent: bool = False,
    ) -> Path:
        resolved_tool_id = self._normalize_tool_id(tool_id)
        bin_dir = self._bin_dir if self._bin_dir is not None else get_app_bin_dir()
        if ensure_parent:
            bin_dir.mkdir(parents=True, exist_ok=True)
        return bin_dir / self.executable_name(resolved_tool_id)

    def executable_name(
        self,
        tool_id: BinaryToolId | str,
        *,
        include_extension: bool = True,
    ) -> str:
        resolved_tool_id = self._normalize_tool_id(tool_id)
        base_name = resolved_tool_id.value
        if (
            include_extension
            and os.name == "nt"
            and resolved_tool_id != BinaryToolId.CLAWHUB
        ):
            return f"{base_name}.exe"
        return base_name

    @staticmethod
    def _system_executable_names(executable_name: str) -> tuple[str, ...]:
        if os.name != "nt":
            return (executable_name,)

        suffixes = _windows_path_extensions()
        executable_suffix = Path(executable_name).suffix.lower()
        if executable_suffix in suffixes:
            return (executable_name,)
        names = [
            executable_name,
            *(f"{executable_name}{suffix}" for suffix in suffixes),
        ]
        return tuple(dict.fromkeys(names))

    def read_tool_version(
        self,
        tool_id: BinaryToolId | str,
        path: Path,
    ) -> str | None:
        resolved_tool_id = self._normalize_tool_id(tool_id)
        for command in self._version_commands(resolved_tool_id, path):
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=_VERSION_TIMEOUT_SECONDS,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if completed.returncode != 0:
                continue
            first_line = _first_meaningful_line(completed.stdout, completed.stderr)
            if first_line is None:
                continue
            if resolved_tool_id == BinaryToolId.GITHUB_CLI and first_line.startswith(
                "gh version "
            ):
                return first_line.removeprefix("gh version ").split(" ", maxsplit=1)[0]
            if resolved_tool_id == BinaryToolId.RIPGREP and first_line.startswith(
                "ripgrep "
            ):
                return first_line.removeprefix("ripgrep ").split(" ", maxsplit=1)[0]
            return first_line
        return None

    @staticmethod
    def _version_commands(
        tool_id: BinaryToolId,
        path: Path,
    ) -> tuple[list[str], ...]:
        base_command = [str(path), "--version"]
        if tool_id == BinaryToolId.CLAWHUB:
            return (
                [str(path), "--cli-version"],
                base_command,
            )
        return (base_command,)

    @staticmethod
    def display_name(tool_id: BinaryToolId) -> str:
        if tool_id == BinaryToolId.RIPGREP:
            return "ripgrep"
        if tool_id == BinaryToolId.GITHUB_CLI:
            return "GitHub CLI"
        return "ClawHub CLI"

    @staticmethod
    def _normalize_tool_id(tool_id: BinaryToolId | str) -> BinaryToolId:
        if isinstance(tool_id, BinaryToolId):
            return tool_id
        try:
            return BinaryToolId(str(tool_id or "").strip())
        except ValueError as exc:
            raise UnsupportedBinaryToolError(str(tool_id)) from exc

    def _item(
        self,
        tool_id: BinaryToolId,
        *,
        status: BinaryToolStatus,
        path: Path | None = None,
        path_source: BinaryToolPathSource | None = None,
        version: str | None = None,
        download_job_id: str | None = None,
        error_message: str | None = None,
    ) -> BinaryToolItem:
        return BinaryToolItem(
            tool_id=tool_id,
            display_name=self.display_name(tool_id),
            version=version,
            source_kind=(
                BinaryToolSourceKind.NPM_GLOBAL
                if tool_id == BinaryToolId.CLAWHUB
                else BinaryToolSourceKind.GITHUB_RELEASE
            ),
            status=status,
            path_source=path_source,
            path=None if path is None else str(path),
            executable_name=self.executable_name(tool_id),
            download_job_id=download_job_id,
            error_message=error_message,
        )

    def install_clawhub_for_probe(
        self,
        *,
        install_env: Mapping[str, str],
        timeout_seconds: float,
    ) -> Path:
        return self._install_clawhub_with_lock(
            timeout_seconds=timeout_seconds,
            base_env=install_env,
        )

    def _install_clawhub_with_lock(
        self,
        *,
        timeout_seconds: float,
        base_env: Mapping[str, str] | None,
    ) -> Path:
        existing = self.resolve_existing_tool_path(BinaryToolId.CLAWHUB)
        if existing is not None:
            return existing
        with _CLAWHUB_INSTALL_LOCK:
            existing = self.resolve_existing_tool_path(BinaryToolId.CLAWHUB)
            if existing is not None:
                return existing
            result = self._install_clawhub(
                timeout_seconds=timeout_seconds,
                base_env=base_env,
            )
            if result.ok and result.clawhub_path is not None:
                return Path(result.clawhub_path)
            raise BinaryToolUnavailableError(
                result.error_message or "ClawHub CLI is not available."
            )

    async def _run_download_job(self, job_id: str) -> None:
        job = self._require_job(job_id)
        self._update_job(
            job_id,
            status=BinaryToolDownloadStatus.RUNNING,
            progress_percent=5,
            message=f"Preparing {self.display_name(job.tool_id)}.",
        )
        try:
            path = await self._install_tool_for_job(job)
            self._update_job(
                job_id,
                status=BinaryToolDownloadStatus.SUCCEEDED,
                progress_percent=100,
                path=str(path),
                message=f"{self.display_name(job.tool_id)} is ready.",
            )
        except Exception as exc:
            LOGGER.warning("Binary tool download failed for %s: %s", job.tool_id, exc)
            self._update_job(
                job_id,
                status=BinaryToolDownloadStatus.FAILED,
                message=f"{self.display_name(job.tool_id)} download failed.",
                error_message=str(exc) or "Download failed.",
            )
        finally:
            self._running_job_by_tool.pop(job.tool_id, None)

    async def _install_tool_for_job(self, job: BinaryToolDownloadJob) -> Path:
        async with self._locks[job.tool_id]:
            existing = self.resolve_existing_tool_path(job.tool_id)
            if existing is not None:
                return existing
            if job.tool_id == BinaryToolId.CLAWHUB:
                self._update_job(
                    job.job_id,
                    progress_percent=20,
                    message="Installing ClawHub CLI with npm.",
                )
                path = await asyncio.to_thread(
                    self._install_clawhub_with_lock,
                    timeout_seconds=_NPM_INSTALL_TIMEOUT_SECONDS,
                    base_env=self._default_clawhub_install_env(),
                )
                self._update_job(
                    job.job_id,
                    progress_percent=85,
                    message="Checking installed ClawHub CLI.",
                )
                return path
            target = self.managed_target_path(job.tool_id, ensure_parent=True)
            await self.download_tool_to_path(job.tool_id, target, job_id=job.job_id)
            return target

    async def _download_bytes(self, url: str, *, job_id: str | None) -> bytes:
        chunks: list[bytes] = []
        downloaded = 0
        async with self._create_http_client(follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise BinaryToolDownloadError(
                        f"Failed to download {url}: HTTP {response.status_code}"
                    )
                total = _content_length(response.headers)
                if job_id is not None:
                    self._update_job(
                        job_id,
                        total_bytes=total,
                        message="Downloading archive.",
                    )
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    downloaded += len(chunk)
                    if job_id is not None:
                        percent = None
                        if total and total > 0:
                            percent = min(90, 10 + int((downloaded / total) * 75))
                        self._update_job(
                            job_id,
                            downloaded_bytes=downloaded,
                            progress_percent=percent,
                            message="Downloading archive.",
                        )
        return b"".join(chunks)

    @staticmethod
    def _platform_config(tool_id: BinaryToolId) -> Mapping[str, str]:
        platform_key = get_platform_key()
        mapping = (
            RIPGREP_PLATFORM_MAP
            if tool_id == BinaryToolId.RIPGREP
            else GITHUB_CLI_PLATFORM_MAP
        )
        config = mapping.get(platform_key)
        if config is None:
            raise BinaryToolDownloadError(
                f"Unsupported platform for {tool_id.value}: {platform_key}"
            )
        return config

    @staticmethod
    def _download_url(tool_id: BinaryToolId, config: Mapping[str, str]) -> str:
        platform_name = config["platform"]
        extension = config["extension"]
        if tool_id == BinaryToolId.RIPGREP:
            filename = f"ripgrep-{RIPGREP_VERSION}-{platform_name}.{extension}"
            return (
                "https://github.com/BurntSushi/ripgrep/releases/download/"
                f"{RIPGREP_VERSION}/{filename}"
            )
        filename = f"gh_{GITHUB_CLI_VERSION}_{platform_name}.{extension}"
        return (
            "https://github.com/cli/cli/releases/download/"
            f"v{GITHUB_CLI_VERSION}/{filename}"
        )

    @staticmethod
    def _archive_executable_name(
        tool_id: BinaryToolId,
        config: Mapping[str, str],
    ) -> str:
        platform_name = config["platform"].lower()
        if "windows" in platform_name or "pc-windows" in platform_name:
            return f"{tool_id.value}.exe"
        return tool_id.value

    @staticmethod
    def _extract_tarball(
        content: bytes,
        target: Path,
        *,
        executable_name: str,
    ) -> None:
        with tarfile.open(fileobj=io.BytesIO(content)) as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                if Path(member.name).name != executable_name:
                    continue
                source = archive.extractfile(member)
                if source is None:
                    break
                target.write_bytes(source.read())
                return
        raise BinaryToolDownloadError(f"{executable_name} not found in tarball")

    @staticmethod
    def _extract_zip(
        content: bytes,
        target: Path,
        *,
        executable_name: str,
    ) -> None:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for name in archive.namelist():
                if Path(name).name != executable_name:
                    continue
                with archive.open(name) as source:
                    target.write_bytes(source.read())
                return
        raise BinaryToolDownloadError(f"{executable_name} not found in zip")

    def _create_completed_job(
        self,
        tool_id: BinaryToolId,
        *,
        message: str,
        path: str | None,
    ) -> BinaryToolDownloadJob:
        job = BinaryToolDownloadJob(
            job_id=f"bin_{uuid.uuid4().hex}",
            tool_id=tool_id,
            status=BinaryToolDownloadStatus.SUCCEEDED,
            downloaded_bytes=0,
            total_bytes=None,
            progress_percent=100,
            message=message,
            path=path,
        )
        self._jobs[job.job_id] = job
        self._latest_job_by_tool[tool_id] = job.job_id
        return job

    def _require_job(self, job_id: str) -> BinaryToolDownloadJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"Unknown binary tool download job: {job_id}")
        return job

    def _default_clawhub_install_env(self) -> Mapping[str, str]:
        if self._build_clawhub_install_env is not None:
            return self._build_clawhub_install_env()
        return build_clawhub_subprocess_env(
            None,
            config_dir=self._config_dir,
            base_env=os.environ,
        )

    def _update_job(
        self,
        job_id: str,
        *,
        status: BinaryToolDownloadStatus | None = None,
        downloaded_bytes: int | None = None,
        total_bytes: int | None = None,
        progress_percent: int | None = None,
        message: str | None = None,
        path: str | None = None,
        error_message: str | None = None,
    ) -> BinaryToolDownloadJob:
        job = self._require_job(job_id)
        updated = job.model_copy(
            update={
                "status": job.status if status is None else status,
                "downloaded_bytes": (
                    job.downloaded_bytes
                    if downloaded_bytes is None
                    else downloaded_bytes
                ),
                "total_bytes": job.total_bytes if total_bytes is None else total_bytes,
                "progress_percent": (
                    job.progress_percent
                    if progress_percent is None
                    else progress_percent
                ),
                "message": job.message if message is None else message,
                "path": job.path if path is None else path,
                "error_message": (
                    job.error_message if error_message is None else error_message
                ),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self._jobs[job_id] = updated
        return updated


def get_platform_key() -> str:
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


def _windows_path_extensions() -> tuple[str, ...]:
    raw_extensions = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    suffixes: list[str] = []
    for raw_extension in raw_extensions.split(";"):
        normalized = raw_extension.strip().lower()
        if not normalized:
            continue
        suffixes.append(normalized if normalized.startswith(".") else f".{normalized}")
    return tuple(dict.fromkeys(suffixes)) or (".com", ".exe", ".bat", ".cmd")


def _managed_tool_install_lock(
    tool_id: BinaryToolId,
    target: Path,
) -> threading.Lock:
    key = (tool_id, str(target.expanduser()))
    with _MANAGED_TOOL_INSTALL_LOCKS_LOCK:
        install_lock = _MANAGED_TOOL_INSTALL_LOCKS.get(key)
        if install_lock is None:
            install_lock = threading.Lock()
            _MANAGED_TOOL_INSTALL_LOCKS[key] = install_lock
        return install_lock


async def _acquire_managed_tool_install_lock(install_lock: threading.Lock) -> None:
    while not install_lock.acquire(blocking=False):
        await asyncio.sleep(_MANAGED_TOOL_INSTALL_LOCK_POLL_SECONDS)


def _content_length(headers: httpx.Headers) -> int | None:
    raw_value = headers.get("content-length")
    if raw_value is None:
        return None
    try:
        value = int(raw_value)
    except ValueError:
        return None
    return value if value >= 0 else None


def _first_meaningful_line(*chunks: str) -> str | None:
    for chunk in chunks:
        for line in chunk.splitlines():
            normalized = line.strip()
            if normalized:
                return normalized
    return None
