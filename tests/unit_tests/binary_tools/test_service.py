from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import threading
import time
from types import TracebackType
from typing import Self
import zipfile

import httpx
import pytest

from relay_teams.binary_tools import (
    BinaryToolDownloadError,
    BinaryToolDownloadJob,
    BinaryToolDownloadStatus,
    BinaryToolId,
    BinaryToolItem,
    BinaryToolPathSource,
    BinaryToolService,
    BinaryToolStatus,
    BinaryToolUnavailableError,
    UnsupportedBinaryToolError,
)
from relay_teams.binary_tools import service as binary_tool_service
from relay_teams.env.clawhub_cli import ClawHubCliInstallResult


def test_inspect_missing_tool_does_not_create_bin_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "")
    bin_dir = tmp_path / "missing-bin"
    service = BinaryToolService(bin_dir=bin_dir)

    item = service.inspect_tool(BinaryToolId.RIPGREP)

    assert item.status == BinaryToolStatus.MISSING
    assert not bin_dir.exists()


def test_inspect_managed_ripgrep_reports_ready(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    service = BinaryToolService(bin_dir=bin_dir)
    executable = bin_dir / service.executable_name(BinaryToolId.RIPGREP)
    executable.write_bytes(b"fake")

    item = service.inspect_tool(BinaryToolId.RIPGREP)

    assert item.status == BinaryToolStatus.READY
    assert item.path_source == BinaryToolPathSource.MANAGED
    assert item.path == str(executable)


@pytest.mark.asyncio
async def test_list_tools_reports_running_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "")
    service = BinaryToolService(bin_dir=tmp_path)
    job = BinaryToolDownloadJob(
        job_id="job-running",
        tool_id=BinaryToolId.RIPGREP,
        status=BinaryToolDownloadStatus.RUNNING,
        message="Downloading.",
    )
    service._jobs[job.job_id] = job
    service._running_job_by_tool[BinaryToolId.RIPGREP] = job.job_id

    response = await service.list_tools()

    ripgrep = next(
        item for item in response.items if item.tool_id == BinaryToolId.RIPGREP
    )
    assert ripgrep.status == BinaryToolStatus.DOWNLOADING
    assert ripgrep.download_job_id == job.job_id


@pytest.mark.asyncio
async def test_start_download_returns_completed_job_when_ready(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    service = BinaryToolService(bin_dir=bin_dir)
    executable = bin_dir / service.executable_name(BinaryToolId.RIPGREP)
    executable.write_bytes(b"fake")

    job = await service.start_download(BinaryToolId.RIPGREP)

    assert job.status == BinaryToolDownloadStatus.SUCCEEDED
    assert job.progress_percent == 100
    assert job.path == str(executable)


@pytest.mark.asyncio
async def test_start_download_inspects_ready_tool_off_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = BinaryToolService(bin_dir=tmp_path)
    main_thread_id = threading.get_ident()
    inspect_thread_ids: list[int] = []

    def inspect_tool(tool_id: BinaryToolId | str) -> BinaryToolItem:
        resolved_tool_id = (
            tool_id if isinstance(tool_id, BinaryToolId) else BinaryToolId(str(tool_id))
        )
        inspect_thread_ids.append(threading.get_ident())
        return service._item(
            resolved_tool_id,
            status=BinaryToolStatus.READY,
            path=tmp_path / service.executable_name(resolved_tool_id),
        )

    monkeypatch.setattr(service, "inspect_tool", inspect_tool)

    job = await service.start_download(BinaryToolId.RIPGREP)

    assert job.status == BinaryToolDownloadStatus.SUCCEEDED
    assert inspect_thread_ids
    assert inspect_thread_ids[0] != main_thread_id


@pytest.mark.asyncio
async def test_ensure_tool_path_installs_clawhub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        binary_tool_service,
        "resolve_existing_clawhub_path",
        lambda: None,
    )
    clawhub = tmp_path / "clawhub"

    def install_clawhub(**_kwargs: object) -> ClawHubCliInstallResult:
        return ClawHubCliInstallResult(
            ok=True,
            attempted=True,
            clawhub_path=str(clawhub),
        )

    service = BinaryToolService(bin_dir=tmp_path, install_clawhub=install_clawhub)

    path = await service.ensure_tool_path(
        BinaryToolId.CLAWHUB,
        install_env={"PATH": str(tmp_path)},
    )

    assert path == clawhub


@pytest.mark.asyncio
async def test_ensure_tool_path_reports_clawhub_install_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        binary_tool_service,
        "resolve_existing_clawhub_path",
        lambda: None,
    )

    def install_clawhub(**_kwargs: object) -> ClawHubCliInstallResult:
        return ClawHubCliInstallResult(
            ok=False,
            attempted=True,
            error_message="npm failed",
        )

    service = BinaryToolService(bin_dir=tmp_path, install_clawhub=install_clawhub)

    with pytest.raises(BinaryToolUnavailableError, match="npm failed"):
        await service.ensure_tool_path(BinaryToolId.CLAWHUB)


@pytest.mark.asyncio
async def test_ensure_tool_path_falls_back_to_system_ripgrep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system_dir = tmp_path / "system"
    system_dir.mkdir()
    system_rg = system_dir / "rg"
    _write_executable(system_rg, b"system")
    monkeypatch.setenv("PATH", str(system_dir))
    monkeypatch.setattr(binary_tool_service, "get_platform_key", lambda: "x64-windows")
    service = BinaryToolService(
        bin_dir=tmp_path / "bin",
        create_http_client=lambda **_kwargs: _FailingClient(),
    )

    path = await service.ensure_tool_path(BinaryToolId.RIPGREP)

    assert path == system_rg


@pytest.mark.asyncio
async def test_download_streams_release_archive_to_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(binary_tool_service, "get_platform_key", lambda: "x64-windows")
    target = tmp_path / "gh.exe"
    service = BinaryToolService(
        bin_dir=tmp_path,
        create_http_client=lambda **_kwargs: _FakeClient(_build_gh_zip()),
    )

    await service.download_tool_to_path(BinaryToolId.GITHUB_CLI, target)

    assert target.read_bytes() == b"new"


@pytest.mark.asyncio
async def test_download_streams_tarball_to_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(binary_tool_service, "get_platform_key", lambda: "x64-linux")
    target = tmp_path / "rg"
    service = BinaryToolService(
        bin_dir=tmp_path,
        create_http_client=lambda **_kwargs: _FakeClient(_build_tarball("rg")),
    )

    await service.download_tool_to_path(BinaryToolId.RIPGREP, target)

    assert target.read_bytes() == b"new"


@pytest.mark.asyncio
async def test_download_rejects_clawhub_direct_download(tmp_path: Path) -> None:
    service = BinaryToolService(bin_dir=tmp_path)

    with pytest.raises(UnsupportedBinaryToolError):
        await service.download_tool_to_path(BinaryToolId.CLAWHUB, tmp_path / "clawhub")


@pytest.mark.asyncio
async def test_download_job_reuses_running_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(binary_tool_service, "get_platform_key", lambda: "x64-windows")
    monkeypatch.setenv("PATH", "")
    service = BinaryToolService(
        bin_dir=tmp_path,
        create_http_client=lambda **_kwargs: _SlowFakeClient(_build_rg_zip()),
    )

    first = await service.start_download(BinaryToolId.RIPGREP)
    second = await service.start_download(BinaryToolId.RIPGREP)

    assert second.job_id == first.job_id
    for _ in range(20):
        job = service.get_download_job(first.job_id)
        if job.status == BinaryToolDownloadStatus.SUCCEEDED:
            break
        await asyncio.sleep(0.01)
    assert (
        service.get_download_job(first.job_id).status
        == BinaryToolDownloadStatus.SUCCEEDED
    )


@pytest.mark.asyncio
async def test_managed_downloads_share_lock_across_service_instances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(binary_tool_service, "get_platform_key", lambda: "x64-windows")
    active_lock = threading.Lock()
    active_count = 0
    max_active_count = 0
    stream_count = 0

    class LockTrackingClient(_FakeClient):
        def stream(
            self,
            method: str,
            url: httpx.URL | str,
        ) -> "_LockTrackingResponse":
            return _LockTrackingResponse(self._content)

    class _LockTrackingResponse(_FakeResponse):
        async def aiter_bytes(self) -> AsyncIterator[bytes]:
            nonlocal active_count, max_active_count, stream_count
            with active_lock:
                active_count += 1
                stream_count += 1
                max_active_count = max(max_active_count, active_count)
            await asyncio.sleep(0.02)
            try:
                async for chunk in super().aiter_bytes():
                    yield chunk
            finally:
                with active_lock:
                    active_count -= 1

    service_a = BinaryToolService(
        bin_dir=tmp_path,
        create_http_client=lambda **_kwargs: LockTrackingClient(_build_rg_zip()),
    )
    service_b = BinaryToolService(
        bin_dir=tmp_path,
        create_http_client=lambda **_kwargs: LockTrackingClient(_build_rg_zip()),
    )
    target = tmp_path / "rg.exe"

    await asyncio.gather(
        service_a.download_tool_to_path(BinaryToolId.RIPGREP, target),
        service_b.download_tool_to_path(BinaryToolId.RIPGREP, target),
    )

    assert target.read_bytes() == b"new"
    assert max_active_count == 1
    assert stream_count == 1


@pytest.mark.asyncio
async def test_managed_download_lock_wait_is_cancellation_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(binary_tool_service, "get_platform_key", lambda: "x64-windows")
    target = tmp_path / "rg.exe"
    install_lock = binary_tool_service._managed_tool_install_lock(
        BinaryToolId.RIPGREP,
        target,
    )
    assert install_lock.acquire(blocking=False)
    service = BinaryToolService(
        bin_dir=tmp_path,
        create_http_client=lambda **_kwargs: _FakeClient(_build_rg_zip()),
    )

    blocked_task = asyncio.create_task(
        service.download_tool_to_path(BinaryToolId.RIPGREP, target)
    )
    await asyncio.sleep(0.01)
    blocked_task.cancel()
    cancellation_result = await asyncio.gather(
        blocked_task,
        return_exceptions=True,
    )
    assert isinstance(cancellation_result[0], asyncio.CancelledError)
    install_lock.release()

    await asyncio.wait_for(
        service.download_tool_to_path(BinaryToolId.RIPGREP, target),
        timeout=1,
    )

    assert target.read_bytes() == b"new"


@pytest.mark.asyncio
async def test_clawhub_download_job_uses_configured_proxy_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        binary_tool_service,
        "resolve_existing_clawhub_path",
        lambda: None,
    )
    for key in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
        "NPM_CONFIG_PROXY",
        "npm_config_proxy",
        "NPM_CONFIG_HTTPS_PROXY",
        "npm_config_https_proxy",
        "NPM_CONFIG_NOPROXY",
        "npm_config_noproxy",
    ):
        monkeypatch.delenv(key, raising=False)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / ".env").write_text(
        "HTTP_PROXY=http://proxy.test:8080\n",
        encoding="utf-8",
    )
    captured_env: dict[str, str] = {}
    clawhub = tmp_path / "clawhub"

    def install_clawhub(
        *,
        base_env: Mapping[str, str] | None,
        **_kwargs: object,
    ) -> ClawHubCliInstallResult:
        captured_env.update(dict(base_env or {}))
        return ClawHubCliInstallResult(
            ok=True,
            attempted=True,
            clawhub_path=str(clawhub),
        )

    service = BinaryToolService(
        bin_dir=tmp_path / "bin",
        config_dir=config_dir,
        install_clawhub=install_clawhub,
    )

    started = await service.start_download(BinaryToolId.CLAWHUB)
    for _ in range(20):
        job = service.get_download_job(started.job_id)
        if job.status == BinaryToolDownloadStatus.SUCCEEDED:
            break
        await asyncio.sleep(0.01)

    assert (
        service.get_download_job(started.job_id).status
        == BinaryToolDownloadStatus.SUCCEEDED
    )
    assert captured_env["NPM_CONFIG_PROXY"] == "http://proxy.test:8080"
    assert captured_env["npm_config_proxy"] == "http://proxy.test:8080"


@pytest.mark.asyncio
async def test_clawhub_probe_and_download_installs_share_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        binary_tool_service,
        "resolve_existing_clawhub_path",
        lambda: None,
    )
    active_lock = threading.Lock()
    active_count = 0
    max_active_count = 0
    clawhub = tmp_path / "clawhub"

    def install_clawhub(**_kwargs: object) -> ClawHubCliInstallResult:
        nonlocal active_count, max_active_count
        with active_lock:
            active_count += 1
            max_active_count = max(max_active_count, active_count)
        time.sleep(0.02)
        with active_lock:
            active_count -= 1
        return ClawHubCliInstallResult(
            ok=True,
            attempted=True,
            clawhub_path=str(clawhub),
        )

    service = BinaryToolService(
        bin_dir=tmp_path / "bin",
        install_clawhub=install_clawhub,
    )

    probe_task = asyncio.create_task(
        asyncio.to_thread(
            service.install_clawhub_for_probe,
            install_env={},
            timeout_seconds=1.0,
        )
    )
    download_job = await service.start_download(BinaryToolId.CLAWHUB)
    for _ in range(50):
        job = service.get_download_job(download_job.job_id)
        if job.status == BinaryToolDownloadStatus.SUCCEEDED:
            break
        await asyncio.sleep(0.01)
    assert await probe_task == clawhub

    assert (
        service.get_download_job(download_job.job_id).status
        == BinaryToolDownloadStatus.SUCCEEDED
    )
    assert max_active_count == 1


@pytest.mark.asyncio
async def test_download_job_records_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(binary_tool_service, "get_platform_key", lambda: "x64-windows")
    monkeypatch.setenv("PATH", "")
    service = BinaryToolService(
        bin_dir=tmp_path,
        create_http_client=lambda **_kwargs: _FailingClient(),
    )

    started = await service.start_download(BinaryToolId.RIPGREP)

    for _ in range(20):
        job = service.get_download_job(started.job_id)
        if job.status == BinaryToolDownloadStatus.FAILED:
            break
        await asyncio.sleep(0.01)
    job = service.get_download_job(started.job_id)
    assert job.status == BinaryToolDownloadStatus.FAILED
    assert job.error_message is not None


def test_get_download_job_raises_for_unknown_job(tmp_path: Path) -> None:
    service = BinaryToolService(bin_dir=tmp_path)

    with pytest.raises(KeyError):
        service.get_download_job("missing")


def test_failed_latest_job_is_visible_in_tool_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "")
    service = BinaryToolService(bin_dir=tmp_path)
    job = BinaryToolDownloadJob(
        job_id="job-failed",
        tool_id=BinaryToolId.GITHUB_CLI,
        status=BinaryToolDownloadStatus.FAILED,
        message="Download failed.",
        error_message="network failed",
    )
    service._jobs[job.job_id] = job
    service._latest_job_by_tool[BinaryToolId.GITHUB_CLI] = job.job_id

    item = service.inspect_tool(BinaryToolId.GITHUB_CLI)

    assert item.status == BinaryToolStatus.ERROR
    assert item.download_job_id == job.job_id
    assert item.error_message == "network failed"


@pytest.mark.asyncio
async def test_zip_extraction_uses_executable_basename_without_trusting_archive_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(binary_tool_service, "get_platform_key", lambda: "x64-windows")
    target = tmp_path / "rg.exe"
    outside = tmp_path.parent / "rg.exe"
    if outside.exists():
        outside.unlink()
    service = BinaryToolService(
        bin_dir=tmp_path,
        create_http_client=lambda **_kwargs: _FakeClient(
            _build_zip_with_unsafe_member("nested/../../rg.exe")
        ),
    )

    await service.download_tool_to_path(BinaryToolId.RIPGREP, target)

    assert target.read_bytes() == b"new"
    assert not outside.exists()


def test_resolve_existing_and_path_source_for_clawhub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system_clawhub = tmp_path / "clawhub"
    npm_clawhub = tmp_path / "npm" / "clawhub"
    monkeypatch.setattr(
        binary_tool_service,
        "resolve_existing_clawhub_path",
        lambda: system_clawhub,
    )
    monkeypatch.setattr(
        binary_tool_service,
        "resolve_system_clawhub_path",
        lambda: system_clawhub,
    )
    monkeypatch.setattr(
        binary_tool_service,
        "resolve_npm_global_clawhub_path",
        lambda: npm_clawhub,
    )
    service = BinaryToolService(bin_dir=tmp_path)

    assert service.resolve_existing_tool_path(BinaryToolId.CLAWHUB) == system_clawhub
    assert (
        service.resolve_path_source(BinaryToolId.CLAWHUB, system_clawhub)
        == BinaryToolPathSource.SYSTEM
    )
    assert (
        service.resolve_path_source(BinaryToolId.CLAWHUB, npm_clawhub)
        == BinaryToolPathSource.NPM_GLOBAL
    )
    assert (
        service.resolve_path_source(BinaryToolId.CLAWHUB, tmp_path / "other")
        == BinaryToolPathSource.SYSTEM
    )


def test_resolve_system_tool_path_scans_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    executable = tool_dir / "gh"
    _write_executable(executable, b"gh")
    monkeypatch.setenv("PATH", str(tool_dir))
    service = BinaryToolService(bin_dir=tmp_path / "bin")

    assert service.resolve_system_tool_path(BinaryToolId.GITHUB_CLI) == executable


def test_windows_executable_names_use_pathext(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(binary_tool_service.os, "name", "nt")
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD")
    service = BinaryToolService()

    assert service.executable_name(BinaryToolId.GITHUB_CLI) == "gh.exe"
    assert service.executable_name(BinaryToolId.CLAWHUB) == "clawhub"
    assert service._system_executable_names("gh") == ("gh", "gh.exe", "gh.cmd")
    assert service._system_executable_names("gh.exe") == ("gh.exe",)


def test_version_parsing_and_display_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = BinaryToolService(bin_dir=tmp_path)
    executable = tmp_path / "gh"

    monkeypatch.setattr(
        binary_tool_service.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout="gh version 2.88.1 (2026-01-01)\n",
            stderr="",
        ),
    )
    assert service.read_tool_version(BinaryToolId.GITHUB_CLI, executable) == "2.88.1"

    monkeypatch.setattr(
        binary_tool_service.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout="ripgrep 14.1.1\n",
            stderr="",
        ),
    )
    assert service.read_tool_version(BinaryToolId.RIPGREP, executable) == "14.1.1"

    monkeypatch.setattr(
        binary_tool_service.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=(),
            returncode=1,
            stdout="",
            stderr="bad",
        ),
    )
    assert service.read_tool_version(BinaryToolId.RIPGREP, executable) is None
    assert service.display_name(BinaryToolId.GITHUB_CLI) == "GitHub CLI"
    assert service.display_name(BinaryToolId.CLAWHUB) == "ClawHub CLI"


def test_clawhub_version_uses_cli_version_before_generic_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = BinaryToolService(bin_dir=tmp_path)
    executable = tmp_path / "clawhub"
    commands: list[list[str]] = []

    def run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[1] == "--cli-version":
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="1.2.3\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr="bad",
        )

    monkeypatch.setattr(binary_tool_service.subprocess, "run", run)

    assert service.read_tool_version(BinaryToolId.CLAWHUB, executable) == "1.2.3"
    assert commands == [[str(executable), "--cli-version"]]


def test_binary_tools_package_imports_without_net_github_cli_cycle() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from relay_teams.binary_tools import BinaryToolService; print(BinaryToolService.__name__)",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "BinaryToolService"


def test_normalize_rejects_unknown_tool(tmp_path: Path) -> None:
    service = BinaryToolService(bin_dir=tmp_path)

    with pytest.raises(UnsupportedBinaryToolError):
        service.inspect_tool("not-a-tool")


def test_archive_helpers_and_header_parsing(tmp_path: Path) -> None:
    target = tmp_path / "missing"
    with pytest.raises(BinaryToolDownloadError):
        BinaryToolService._extract_zip(
            _build_zip_with_unsafe_member("nested/nope"),
            target,
            executable_name="gh.exe",
        )
    with pytest.raises(BinaryToolDownloadError):
        BinaryToolService._extract_tarball(
            _build_tarball("nope"),
            target,
            executable_name="gh",
        )

    assert (
        BinaryToolService._archive_executable_name(
            BinaryToolId.GITHUB_CLI,
            {"platform": "windows_amd64"},
        )
        == "gh.exe"
    )
    assert binary_tool_service._content_length(httpx.Headers()) is None
    assert (
        binary_tool_service._content_length(httpx.Headers({"content-length": "bad"}))
        is None
    )
    assert (
        binary_tool_service._content_length(httpx.Headers({"content-length": "-1"}))
        is None
    )
    assert binary_tool_service._first_meaningful_line("", "\nvalue\n") == "value"
    assert binary_tool_service._first_meaningful_line("", "\n") is None


def _build_gh_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("gh_2.88.1_windows_amd64/bin/gh.exe", b"new")
    return buffer.getvalue()


def _build_rg_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ripgrep-14.1.1-x86_64-pc-windows-msvc/rg.exe", b"new")
    return buffer.getvalue()


def _build_zip_with_unsafe_member(member_name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member_name, b"new")
    return buffer.getvalue()


def _build_tarball(member_name: str) -> bytes:
    buffer = io.BytesIO()
    payload = b"new"
    info = tarfile.TarInfo(f"archive/bin/{member_name}")
    info.size = len(payload)
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _write_executable(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    if binary_tool_service.os.name != "nt":
        path.chmod(0o755)


class _FakeClient:
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def stream(self, method: str, url: httpx.URL | str) -> "_FakeResponse":
        return _FakeResponse(self._content)


class _SlowFakeClient(_FakeClient):
    def stream(self, method: str, url: httpx.URL | str) -> "_SlowFakeResponse":
        return _SlowFakeResponse(self._content)


class _FailingClient:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def stream(self, method: str, url: httpx.URL | str) -> "_FailingResponse":
        return _FailingResponse()


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.status_code = 200
        self.headers = httpx.Headers({"content-length": str(len(content))})
        self._content = content

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        midpoint = max(1, len(self._content) // 2)
        yield self._content[:midpoint]
        yield self._content[midpoint:]


class _SlowFakeResponse(_FakeResponse):
    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        await asyncio.sleep(0.02)
        async for chunk in super().aiter_bytes():
            yield chunk


class _FailingResponse:
    status_code = 500
    headers = httpx.Headers()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        if False:
            yield b""
