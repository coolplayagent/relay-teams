from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from relay_teams.sessions.runs.background_tasks.command_runtime import (
    CommandRuntimeKind,
    ResolvedCommandRuntime,
    resolve_bash_path,
)
from relay_teams.sessions.runs.background_tasks.manager import BackgroundTaskManager
from relay_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from relay_teams.sessions.runs.background_tasks.repository import (
    BackgroundTaskRepository,
)
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.workspace import WorkspaceHandle
from relay_teams.workspace.workspace_models import (
    WorkspaceLocations,
    WorkspaceRef,
    build_local_workspace_mount,
)


def _build_workspace_handle(tmp_path: Path) -> WorkspaceHandle:
    scope_root = tmp_path / "project"
    workspace_dir = tmp_path / ".agent-teams" / "workspaces" / "project"
    scope_root.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    tmp_root = workspace_dir / "tmp"
    return WorkspaceHandle(
        ref=WorkspaceRef(
            workspace_id="workspace-1",
            session_id="session-1",
            role_id="writer",
            conversation_id="conversation-1",
            default_mount_name="default",
        ),
        mounts=(
            build_local_workspace_mount(
                mount_name="default",
                root_path=scope_root,
            ),
        ),
        locations=WorkspaceLocations(
            workspace_dir=workspace_dir,
            scope_root=scope_root,
            execution_root=scope_root,
            tmp_root=tmp_root,
            readable_roots=(scope_root, tmp_root),
            writable_roots=(scope_root, tmp_root),
        ),
    )


async def _wait_for_expected_output(
    manager: BackgroundTaskManager,
    *,
    run_id: str,
    background_task_id: str,
    expected_output: str,
) -> BackgroundTaskRecord:
    for _ in range(8):
        updated, completed = await manager.interact_for_run(
            run_id=run_id,
            background_task_id=background_task_id,
            chars="",
            yield_time_ms=1_000,
            is_initial_poll=False,
        )
        if any(expected_output in line for line in updated.recent_output):
            return updated
        if expected_output in updated.output_excerpt:
            return updated
        if completed:
            return updated
    return manager.get_for_run(run_id=run_id, background_task_id=background_task_id)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows-specific stop regression")
async def test_background_task_manager_stop_unblocks_windows_pipe_runtime(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-terminal-stop-win-pipe.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)

    try:
        started = await manager.start_session(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            workspace=workspace,
            command=(
                'python -c "import time,sys; '
                "print('ready', flush=True); time.sleep(90)\""
            ),
            cwd=workspace.execution_root,
            timeout_ms=30_000,
            env=None,
            tty=False,
        )
        updated = await _wait_for_expected_output(
            manager,
            run_id="run-1",
            background_task_id=started.background_task_id,
            expected_output="ready",
        )
        stopped = await asyncio.wait_for(
            manager.stop_for_run(
                run_id="run-1",
                background_task_id=started.background_task_id,
            ),
            timeout=10,
        )
    finally:
        await manager.close()

    assert updated.status == BackgroundTaskStatus.RUNNING
    assert stopped.status == BackgroundTaskStatus.STOPPED


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows-only background process coverage")
async def test_background_task_manager_auto_detects_powershell_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import (
        command_runtime as runtime_module,
    )
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(
        tmp_path / "background-terminal-win-powershell-auto.db"
    )
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)
    observed_runtime_kinds: list[CommandRuntimeKind] = []

    class _FakePipeProcess:
        def __init__(self) -> None:
            self.pid = 43210
            self.returncode: int | None = None
            self.stdin = None
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self._wait_event = asyncio.Event()

        async def wait(self) -> int:
            await self._wait_event.wait()
            assert self.returncode is not None
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self._wait_event.set()

    async def _create_command_subprocess(
        *,
        command: str,
        cwd: Path,
        env: dict[str, str] | None = None,
        login: bool = False,
        stdin: int | None = None,
        stdout: int | None = None,
        stderr: int | None = None,
    ) -> _FakePipeProcess:
        _ = (cwd, env, login, stdin, stdout, stderr)
        observed_runtime_kinds.append(
            runtime_module.resolve_command_runtime(command=command).kind
        )
        proc = _FakePipeProcess()

        async def _feed_output() -> None:
            await asyncio.sleep(0)
            proc.stdout.feed_data(b"PS_AUTO_READY\n")

        asyncio.create_task(_feed_output())
        return proc

    monkeypatch.setattr(
        manager_module,
        "create_command_subprocess",
        _create_command_subprocess,
    )

    try:
        started = await manager.start_session(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            workspace=workspace,
            command="Write-Output 'PS_AUTO_READY'; Start-Sleep -Seconds 90",
            cwd=workspace.execution_root,
            timeout_ms=30_000,
            env=None,
            tty=False,
        )
        updated = await _wait_for_expected_output(
            manager,
            run_id="run-1",
            background_task_id=started.background_task_id,
            expected_output="PS_AUTO_READY",
        )
        stopped = await asyncio.wait_for(
            manager.stop_for_run(
                run_id="run-1",
                background_task_id=started.background_task_id,
            ),
            timeout=10,
        )
    finally:
        await manager.close()

    assert updated.status == BackgroundTaskStatus.RUNNING
    assert observed_runtime_kinds == [CommandRuntimeKind.POWERSHELL]
    assert any("PS_AUTO_READY" in line for line in stopped.recent_output)
    assert stopped.status == BackgroundTaskStatus.STOPPED


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows-only background process coverage")
@pytest.mark.parametrize(
    ("runtime", "command", "expected_output"),
    [
        (
            ResolvedCommandRuntime(
                kind=CommandRuntimeKind.BASH,
                executable=resolve_bash_path(),
                display_name="Git Bash",
            ),
            "git --version && sleep 90",
            "git version",
        ),
        (
            ResolvedCommandRuntime(
                kind=CommandRuntimeKind.POWERSHELL,
                executable="powershell.exe",
                display_name="PowerShell",
            ),
            "Write-Output 'PS_BG_READY'; Start-Sleep -Seconds 90",
            "PS_BG_READY",
        ),
        (
            ResolvedCommandRuntime(
                kind=CommandRuntimeKind.POWERSHELL,
                executable="powershell.exe",
                display_name="PowerShell",
            ),
            'cmd /d /c "echo CMD_BG_READY & powershell -NoProfile -Command Start-Sleep -Seconds 90"',
            "CMD_BG_READY",
        ),
    ],
)
async def test_background_task_manager_windows_supports_multiple_shell_runtimes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runtime: ResolvedCommandRuntime,
    command: str,
    expected_output: str,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(tmp_path / "background-terminal-win-runtimes.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)
    expected_command = command
    observed_runtimes: list[ResolvedCommandRuntime] = []

    class _FakePipeProcess:
        def __init__(self) -> None:
            self.pid = 50001
            self.returncode: int | None = None
            self.stdin = None
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self._wait_event = asyncio.Event()

        async def wait(self) -> int:
            await self._wait_event.wait()
            assert self.returncode is not None
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self._wait_event.set()

    async def _create_command_subprocess(
        *,
        command: str,
        cwd: Path,
        env: dict[str, str] | None = None,
        login: bool = False,
        stdin: int | None = None,
        stdout: int | None = None,
        stderr: int | None = None,
    ):
        _ = (cwd, env, login, stdin, stdout, stderr)
        assert command == expected_command
        observed_runtimes.append(runtime)
        proc = _FakePipeProcess()

        async def _feed_output() -> None:
            await asyncio.sleep(0)
            proc.stdout.feed_data(f"{expected_output}\n".encode("utf-8"))

        asyncio.create_task(_feed_output())
        return proc

    monkeypatch.setattr(
        manager_module,
        "create_command_subprocess",
        _create_command_subprocess,
    )

    try:
        started = await manager.start_session(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            workspace=workspace,
            command=command,
            cwd=workspace.execution_root,
            timeout_ms=30_000,
            env=None,
            tty=False,
        )
        updated = await _wait_for_expected_output(
            manager,
            run_id="run-1",
            background_task_id=started.background_task_id,
            expected_output=expected_output,
        )
        if updated.status == BackgroundTaskStatus.RUNNING:
            stopped = await asyncio.wait_for(
                manager.stop_for_run(
                    run_id="run-1",
                    background_task_id=started.background_task_id,
                ),
                timeout=10,
            )
        else:
            stopped = updated
    finally:
        await manager.close()

    assert observed_runtimes == [runtime]
    assert any(expected_output in line for line in stopped.recent_output)
    assert stopped.status in {
        BackgroundTaskStatus.RUNNING,
        BackgroundTaskStatus.STOPPED,
        BackgroundTaskStatus.COMPLETED,
    }
