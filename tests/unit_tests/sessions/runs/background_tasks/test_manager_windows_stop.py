from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from agent_teams.sessions.runs.background_tasks.command_runtime import (
    CommandRuntimeKind,
    ResolvedCommandRuntime,
    resolve_bash_path,
)
from agent_teams.sessions.runs.background_tasks.manager import BackgroundTaskManager
from agent_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from agent_teams.sessions.runs.background_tasks.repository import (
    BackgroundTaskRepository,
)
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.workspace import WorkspaceHandle
from agent_teams.workspace.workspace_models import (
    WorkspaceLocations,
    WorkspaceRef,
    default_workspace_profile,
)


def _build_workspace_handle(tmp_path: Path) -> WorkspaceHandle:
    scope_root = tmp_path / "project"
    workspace_dir = tmp_path / ".agent-teams" / "workspaces" / "project"
    scope_root.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    tmp_root = workspace_dir / "tmp"
    profile = default_workspace_profile()
    return WorkspaceHandle(
        ref=WorkspaceRef(
            workspace_id="workspace-1",
            session_id="session-1",
            role_id="writer",
            conversation_id="conversation-1",
            profile=profile,
        ),
        profile=profile,
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
    from agent_teams.sessions.runs.background_tasks import (
        command_runtime as runtime_module,
    )
    from agent_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(tmp_path / "background-terminal-win-runtimes.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)

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
        return await runtime_module.create_command_subprocess(
            command=command,
            cwd=cwd,
            env=env,
            runtime=runtime,
            login=login,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )

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

    assert any(expected_output in line for line in stopped.recent_output)
    assert stopped.status in {
        BackgroundTaskStatus.RUNNING,
        BackgroundTaskStatus.STOPPED,
        BackgroundTaskStatus.COMPLETED,
    }
