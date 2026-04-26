# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
import time
from typing import Callable, cast

import pytest

from relay_teams.monitors import (
    MonitorAction,
    MonitorRepository,
    MonitorRule,
    MonitorService,
    MonitorSourceKind,
)
from relay_teams.sessions.runs.background_tasks.manager import (
    BackgroundTaskManager,
    MAX_BACKGROUND_TASKS,
    PROTECTED_RECENT_BACKGROUND_TASKS,
)
from relay_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from relay_teams.sessions.runs.background_tasks.repository import (
    BackgroundTaskRepository,
)
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.workspace import WorkspaceHandle
from relay_teams.workspace.ssh_profile_models import SshProfilePreparedCommand
from relay_teams.workspace.ssh_profile_service import SshProfileService
from relay_teams.workspace.workspace_models import (
    WorkspaceLocations,
    WorkspaceMountProvider,
    WorkspaceMountRecord,
    WorkspaceRef,
    WorkspaceRemoteMountRoot,
    WorkspaceSshMountConfig,
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


def _build_ssh_workspace_handle(tmp_path: Path) -> WorkspaceHandle:
    workspace_dir = tmp_path / ".agent-teams" / "workspaces" / "remote"
    local_root = workspace_dir / "ssh_mounts" / "prod"
    tmp_root = workspace_dir / "tmp"
    local_root.mkdir(parents=True, exist_ok=True)
    tmp_root.mkdir(parents=True, exist_ok=True)
    return WorkspaceHandle(
        ref=WorkspaceRef(
            workspace_id="workspace-1",
            session_id="session-1",
            role_id="writer",
            conversation_id="conversation-1",
            default_mount_name="prod",
            mount_names=("prod",),
        ),
        mounts=(
            WorkspaceMountRecord(
                mount_name="prod",
                provider=WorkspaceMountProvider.SSH,
                provider_config=WorkspaceSshMountConfig(
                    ssh_profile_id="prod",
                    remote_root="/srv/app",
                ),
            ),
        ),
        locations=WorkspaceLocations(
            workspace_dir=workspace_dir,
            mount_name="prod",
            provider=WorkspaceMountProvider.SSH,
            scope_root=local_root,
            execution_root=local_root,
            tmp_root=tmp_root,
            readable_roots=(local_root, tmp_root),
            writable_roots=(local_root, tmp_root),
            remote_mount_roots=(
                WorkspaceRemoteMountRoot(
                    mount_name="prod",
                    local_root=local_root,
                    remote_root="/srv/app",
                ),
            ),
        ),
    )


class _FakeWindowsPtyProcess:
    def __init__(self) -> None:
        self.pid = 43210
        self._alive = True
        self._exitstatus = 0
        self._outputs: deque[str] = deque(["ready\r\n"])
        self.writes: list[str] = []
        self.sizes: list[tuple[int, int]] = []

    def read(self, size: int = 1024) -> str:
        _ = size
        deadline = time.monotonic() + 1.0
        while not self._outputs:
            if not self._alive:
                raise EOFError("pty closed")
            if time.monotonic() >= deadline:
                return ""
            time.sleep(0.01)
        return self._outputs.popleft()

    def write(self, data: str) -> int:
        self.writes.append(data)
        self._outputs.append(f"echo:{data.strip()}\r\n")
        return len(data)

    def isalive(self) -> bool:
        return self._alive

    def wait(self) -> int:
        while self._alive:
            time.sleep(0.01)
        return self._exitstatus

    def close(self, force: bool = False) -> None:
        _ = force
        self._alive = False

    def setwinsize(self, rows: int, cols: int) -> None:
        self.sizes.append((rows, cols))


class _FakePipeProcess:
    pid: int | None = 1234
    returncode: int | None = None
    stdin: None = None
    stdout: None = None
    stderr: None = None

    async def wait(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class _FakePreparedSshProfileService:
    def __init__(self, temp_root: Path) -> None:
        self.temp_root = temp_root
        self.calls: list[tuple[str, str, str, dict[str, str] | None, bool]] = []

    def prepare_remote_command(
        self,
        *,
        ssh_profile_id: str,
        command: str,
        cwd: str,
        env: dict[str, str] | None = None,
        tty: bool = False,
    ) -> SshProfilePreparedCommand:
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.calls.append((ssh_profile_id, command, cwd, env, tty))
        return SshProfilePreparedCommand(
            argv=("ssh", ssh_profile_id, command),
            env={"RELAY_TEAMS_SSH_PASSWORD": "secret"},
            temp_root=self.temp_root,
        )


class _FakeTransport:
    def __init__(self, *, tty: bool = False, returncode: int | None = None) -> None:
        self.tty = tty
        self.stream_count = 0
        self.pid = None
        self.returncode = returncode
        self.terminated = False
        self.closed = False

    def start_pumps(self, **kwargs: object) -> list[asyncio.Task[None]]:
        _ = kwargs
        return []

    async def wait(self) -> int | None:
        return self.returncode

    async def write(self, chars: str) -> None:
        _ = chars

    async def resize(self, *, columns: int, rows: int) -> None:
        _ = (columns, rows)

    async def terminate(self) -> None:
        self.terminated = True
        self.returncode = 1

    async def close(self) -> None:
        self.closed = True


class _WaitableTransport(_FakeTransport):
    def __init__(self, *, tty: bool = False) -> None:
        super().__init__(tty=tty, returncode=None)
        self._terminated = asyncio.Event()

    async def wait(self) -> int | None:
        await self._terminated.wait()
        return self.returncode

    async def terminate(self) -> None:
        self.terminated = True
        self.returncode = 1
        self._terminated.set()


class _CloseFailingWaitableTransport(_WaitableTransport):
    def __init__(self, *, tty: bool = False) -> None:
        super().__init__(tty=tty)
        self.close_attempted = False

    async def close(self) -> None:
        self.close_attempted = True
        raise RuntimeError("close failed")


class _FakeMonitorSink:
    def __init__(self) -> None:
        self.body_texts: list[str] = []

    def handle_monitor_trigger(
        self,
        *,
        subscription,
        envelope,
        message: str,
    ) -> None:
        _ = (subscription, message)
        self.body_texts.append(envelope.body_text)


def test_background_task_manager_resolves_ssh_execution_context(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-terminal-manager.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    workspace = _build_ssh_workspace_handle(tmp_path)
    cwd = workspace.execution_root / "src" / "service"
    cwd.mkdir(parents=True)

    context = manager._resolve_ssh_execution_context(workspace=workspace, cwd=cwd)

    assert context is not None
    mount, remote_cwd = context
    assert mount.mount_name == "prod"
    assert remote_cwd == "/srv/app/src/service"


@pytest.mark.asyncio
async def test_background_task_manager_ssh_pipe_uses_prepared_subprocess_helper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(tmp_path / "background-terminal-ssh-pipe.db")
    hub = RunEventHub()
    ssh_service = _FakePreparedSshProfileService(tmp_path / "ssh-temp-pipe")
    manager = BackgroundTaskManager(
        repository=repo,
        run_event_hub=hub,
        ssh_profile_service=cast(SshProfileService, ssh_service),
    )
    workspace = _build_ssh_workspace_handle(tmp_path)
    ssh_context = manager._resolve_ssh_execution_context(
        workspace=workspace,
        cwd=workspace.execution_root,
    )
    assert ssh_context is not None
    created_calls: list[tuple[tuple[str, ...], dict[str, str] | None]] = []

    async def fake_create_prepared_subprocess(
        *,
        argv: tuple[str, ...],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin: int | None = None,
        stdout: int | None = None,
        stderr: int | None = None,
    ) -> _FakePipeProcess:
        _ = (cwd, stdin, stdout, stderr)
        created_calls.append((argv, env))
        return _FakePipeProcess()

    monkeypatch.setattr(
        manager_module,
        "create_prepared_subprocess",
        fake_create_prepared_subprocess,
    )

    transport = await manager._spawn_ssh_pipe_transport(
        command="pwd",
        ssh_context=ssh_context,
        env={"AGENT_TEAMS_CURRENT_ROLE_ID": "writer"},
    )

    assert transport.tty is False
    assert ssh_service.calls == [
        (
            "prod",
            "pwd",
            "/srv/app",
            {"AGENT_TEAMS_CURRENT_ROLE_ID": "writer"},
            False,
        )
    ]
    assert created_calls == [
        (("ssh", "prod", "pwd"), {"RELAY_TEAMS_SSH_PASSWORD": "secret"})
    ]
    assert ssh_service.temp_root.is_dir()
    await transport.close()
    assert not ssh_service.temp_root.exists()


@pytest.mark.asyncio
async def test_background_task_manager_ssh_tty_uses_windows_conpty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(tmp_path / "background-terminal-ssh-winpty.db")
    hub = RunEventHub()
    ssh_service = _FakePreparedSshProfileService(tmp_path / "ssh-temp-pty")
    manager = BackgroundTaskManager(
        repository=repo,
        run_event_hub=hub,
        ssh_profile_service=cast(SshProfileService, ssh_service),
    )
    workspace = _build_ssh_workspace_handle(tmp_path)
    ssh_context = manager._resolve_ssh_execution_context(
        workspace=workspace,
        cwd=workspace.execution_root,
    )
    assert ssh_context is not None
    fake_process = _FakeWindowsPtyProcess()
    spawn_calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def fake_spawn_windows_pty_argv_process(
        *,
        argv: tuple[str, ...],
        cwd: Path,
        env: dict[str, str],
        columns: int,
        rows: int,
    ) -> _FakeWindowsPtyProcess:
        _ = (cwd, columns, rows)
        spawn_calls.append((argv, env))
        return fake_process

    monkeypatch.setattr(manager_module, "_posix_pty_supported", lambda: False)
    monkeypatch.setattr(manager_module, "_windows_tty_supported", lambda: True)
    monkeypatch.setattr(
        manager_module,
        "_spawn_windows_pty_argv_process",
        fake_spawn_windows_pty_argv_process,
    )

    transport = await manager._spawn_ssh_tty_transport(
        command="bash",
        ssh_context=ssh_context,
        env={"AGENT_TEAMS_CURRENT_ROLE_ID": "writer"},
    )

    assert transport.tty is True
    assert ssh_service.calls == [
        (
            "prod",
            "bash",
            "/srv/app",
            {"AGENT_TEAMS_CURRENT_ROLE_ID": "writer"},
            True,
        )
    ]
    assert spawn_calls == [
        (("ssh", "prod", "bash"), {"RELAY_TEAMS_SSH_PASSWORD": "secret"})
    ]
    assert ssh_service.temp_root.is_dir()
    await transport.close()
    assert not ssh_service.temp_root.exists()


@pytest.mark.asyncio
async def test_background_task_manager_completes_and_publishes_events(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-terminal-manager.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)
    queue = hub.subscribe("run-1")

    try:
        started, completed = await manager.run_command(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            workspace=workspace,
            command="printf 'hello\\n'",
            cwd=workspace.execution_root,
            timeout_ms=5000,
            yield_time_ms=5000,
            env=None,
            tty=False,
        )
        await asyncio.sleep(0)
    finally:
        await manager.close()

    assert completed is True
    assert started.status == BackgroundTaskStatus.COMPLETED
    assert "hello" in started.recent_output
    log_path = workspace.resolve_read_path(started.log_path)
    assert log_path.read_text(encoding="utf-8") == "hello\n"
    event_types = []
    updated_payload = None
    completed_payload = None
    while not queue.empty():
        event = queue.get_nowait()
        event_types.append(event.event_type)
        if event.event_type == RunEventType.BACKGROUND_TASK_UPDATED:
            updated_payload = json.loads(event.payload_json)
        if event.event_type == RunEventType.BACKGROUND_TASK_COMPLETED:
            completed_payload = json.loads(event.payload_json)
    assert RunEventType.BACKGROUND_TASK_STARTED in event_types
    assert RunEventType.BACKGROUND_TASK_COMPLETED in event_types
    assert updated_payload is not None
    assert "output_excerpt" not in updated_payload
    assert updated_payload["delta"] == "hello\n"
    assert completed_payload is not None
    assert completed_payload["output_excerpt"] == "hello\n"


@pytest.mark.asyncio
async def test_background_task_manager_finalizes_runtime_when_listener_fails(
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(tmp_path / "background-terminal-listener.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    record = repo.upsert(
        BackgroundTaskRecord(
            background_task_id="exec-1",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            command="printf ready",
            cwd=str(tmp_path),
            status=BackgroundTaskStatus.RUNNING,
            log_path=str(tmp_path / "exec-1.log"),
        )
    )
    runtime = manager_module._BackgroundTaskRuntime(
        record=record,
        transport=cast(
            manager_module._BackgroundTaskTransport,
            _FakeTransport(returncode=0),
        ),
        log_file_path=tmp_path / "exec-1.log",
        queue=asyncio.Queue(),
    )
    manager._runtimes[record.background_task_id] = runtime

    async def _failing_listener(record: BackgroundTaskRecord) -> None:
        _ = record
        raise RuntimeError("listener failed")

    manager.set_completion_listener(_failing_listener)

    try:
        with pytest.raises(RuntimeError, match="listener failed"):
            await manager._finalize_runtime(runtime, timed_out=False)
        waited, completed = await manager.wait_for_run(
            run_id="run-1",
            background_task_id=record.background_task_id,
        )
    finally:
        await manager.close()

    assert completed is True
    assert waited.status == BackgroundTaskStatus.COMPLETED
    assert runtime.completed.is_set()
    assert record.background_task_id not in manager._runtimes


@pytest.mark.asyncio
async def test_background_task_manager_writes_logs_via_dedicated_executor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(tmp_path / "background-terminal-log-thread.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    record = repo.upsert(
        BackgroundTaskRecord(
            background_task_id="exec-1",
            run_id="run-1",
            session_id="session-1",
            command="printf ready",
            cwd=str(tmp_path),
            status=BackgroundTaskStatus.RUNNING,
            log_path=str(tmp_path / "exec-1.log"),
        )
    )
    runtime = manager_module._BackgroundTaskRuntime(
        record=record,
        transport=cast(manager_module._BackgroundTaskTransport, _FakeTransport()),
        log_file_path=tmp_path / "exec-1.log",
        queue=asyncio.Queue(),
    )
    calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    async def _fake_run_blocking(
        func: Callable[..., object],
        *args: object,
        **kwargs: object,
    ) -> object:
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(manager, "_run_blocking", _fake_run_blocking)

    await manager._handle_output_chunk(
        runtime,
        stream_name="stdout",
        chunk="hello\n",
    )

    assert calls
    assert runtime.log_file_path.read_text(encoding="utf-8") == "hello\n"


@pytest.mark.asyncio
async def test_background_task_manager_emits_monitor_line_events(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-terminal-monitor.db")
    hub = RunEventHub()
    monitor_repository = MonitorRepository(tmp_path / "background-terminal-monitor.db")
    monitor_service = MonitorService(
        repository=monitor_repository,
        run_event_hub=hub,
    )
    sink = _FakeMonitorSink()
    monitor_service.bind_action_sink(sink)
    manager = BackgroundTaskManager(
        repository=repo,
        run_event_hub=hub,
        monitor_service=monitor_service,
    )
    record = repo.upsert(
        BackgroundTaskRecord(
            background_task_id="exec-1",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            command="tail -f app.log",
            cwd=str(tmp_path),
            status=BackgroundTaskStatus.RUNNING,
            log_path=str(tmp_path / "exec-1.log"),
        )
    )
    monitor_service.create_monitor(
        run_id="run-1",
        session_id="session-1",
        source_kind=MonitorSourceKind.BACKGROUND_TASK,
        source_key="exec-1",
        rule=MonitorRule(
            event_names=("background_task.line",),
            text_patterns_any=("ERROR",),
        ),
        action=MonitorAction(),
        created_by_instance_id="inst-1",
        created_by_role_id="writer",
        tool_call_id="toolcall-1",
    )
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    runtime = manager_module._BackgroundTaskRuntime(
        record=record,
        transport=cast(manager_module._BackgroundTaskTransport, _FakeTransport()),
        log_file_path=tmp_path / "exec-1.log",
        queue=asyncio.Queue(),
    )

    await manager._handle_output_chunk(
        runtime,
        stream_name="stderr",
        chunk="INFO ok\nERROR boom\n",
    )

    assert sink.body_texts == ["ERROR boom"]


@pytest.mark.asyncio
async def test_background_task_manager_write_finishes_shell_prompt(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-terminal-write.db")
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
            command='read line; echo "line:$line"',
            cwd=workspace.execution_root,
            timeout_ms=5000,
            env=None,
            tty=False,
        )
        _, _ = await manager.interact_for_run(
            run_id="run-1",
            background_task_id=started.background_task_id,
            chars="hello\n",
            yield_time_ms=5000,
        )
        completed_record, completed = await manager.wait_for_run(
            run_id="run-1",
            background_task_id=started.background_task_id,
        )
    finally:
        await manager.close()

    assert completed is True
    assert completed_record.status == BackgroundTaskStatus.COMPLETED
    assert "line:hello" in completed_record.recent_output


@pytest.mark.asyncio
async def test_background_task_manager_wait_and_poll_keep_active_record_unresolved_without_runtime(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-terminal-orphan-wait.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    record = repo.upsert(
        BackgroundTaskRecord(
            background_task_id="exec-orphan",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            command="sleep 30",
            cwd=str(tmp_path),
            execution_mode="background",
            status=BackgroundTaskStatus.RUNNING,
            pid=3210,
            log_path="tmp/background_tasks/exec-orphan.log",
        )
    )

    try:
        waited, completed = await manager.wait_for_run(
            run_id="run-1",
            background_task_id=record.background_task_id,
        )
        polled, poll_completed = await manager.interact_for_run(
            run_id="run-1",
            background_task_id=record.background_task_id,
            chars="",
            yield_time_ms=250,
        )
    finally:
        await manager.close()

    assert completed is False
    assert waited.status == BackgroundTaskStatus.RUNNING
    assert poll_completed is False
    assert polled.status == BackgroundTaskStatus.RUNNING


@pytest.mark.asyncio
async def test_background_task_manager_stop_falls_back_to_pid_without_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(tmp_path / "background-terminal-orphan-stop.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    queue = hub.subscribe("run-1")
    record = repo.upsert(
        BackgroundTaskRecord(
            background_task_id="exec-orphan",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            command="sleep 30",
            cwd=str(tmp_path),
            execution_mode="background",
            status=BackgroundTaskStatus.RUNNING,
            pid=3210,
            log_path="tmp/background_tasks/exec-orphan.log",
        )
    )
    killed_pids: list[int] = []

    def _fake_kill_process_tree_by_pid(pid: int) -> bool:
        killed_pids.append(pid)
        return True

    monkeypatch.setattr(
        manager_module,
        "kill_process_tree_by_pid",
        _fake_kill_process_tree_by_pid,
    )

    try:
        stopped = await manager.stop_for_run(
            run_id="run-1",
            background_task_id=record.background_task_id,
        )
    finally:
        await manager.close()

    persisted = repo.get(record.background_task_id)
    assert killed_pids == [3210]
    assert stopped.status == BackgroundTaskStatus.STOPPED
    assert stopped.pid is None
    assert stopped.completed_at is not None
    assert persisted is not None
    assert persisted.status == BackgroundTaskStatus.STOPPED
    event = queue.get_nowait()
    assert event.event_type == RunEventType.BACKGROUND_TASK_STOPPED


@pytest.mark.asyncio
async def test_background_task_manager_stop_preserves_active_record_when_pid_fallback_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(
        tmp_path / "background-terminal-orphan-stop-fail.db"
    )
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    record = repo.upsert(
        BackgroundTaskRecord(
            background_task_id="exec-orphan",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            command="sleep 30",
            cwd=str(tmp_path),
            execution_mode="background",
            status=BackgroundTaskStatus.RUNNING,
            pid=3210,
            log_path="tmp/background_tasks/exec-orphan.log",
        )
    )

    def _fake_kill_process_tree_by_pid(pid: int) -> bool:
        _ = pid
        return False

    monkeypatch.setattr(
        manager_module,
        "kill_process_tree_by_pid",
        _fake_kill_process_tree_by_pid,
    )

    try:
        unchanged = await manager.stop_for_run(
            run_id="run-1",
            background_task_id=record.background_task_id,
        )
    finally:
        await manager.close()

    persisted = repo.get(record.background_task_id)
    assert unchanged.status == BackgroundTaskStatus.RUNNING
    assert unchanged.pid == 3210
    assert persisted is not None
    assert persisted.status == BackgroundTaskStatus.RUNNING
    assert persisted.pid == 3210


@pytest.mark.asyncio
async def test_background_task_manager_stop_marks_terminal_stopped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(tmp_path / "background-terminal-stop.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)
    fake_transport = _WaitableTransport()

    async def _fake_spawn_runtime(
        *,
        record: BackgroundTaskRecord,
        workspace: WorkspaceHandle,
        cwd: Path,
        env: dict[str, str] | None,
        log_file_path: Path,
    ) -> object:
        del cwd, env, log_file_path
        return manager_module._BackgroundTaskRuntime(
            record=record,
            transport=cast(manager_module._BackgroundTaskTransport, fake_transport),
            log_file_path=workspace.resolve_tmp_path("stopped.log", write=True),
            queue=asyncio.Queue(),
        )

    monkeypatch.setattr(manager, "_spawn_runtime", _fake_spawn_runtime)

    try:
        started = await manager.start_session(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            workspace=workspace,
            command="sleep 30",
            cwd=workspace.execution_root,
            timeout_ms=5000,
            env=None,
            tty=False,
        )
        stopped = await manager.stop_for_run(
            run_id="run-1",
            background_task_id=started.background_task_id,
        )
    finally:
        await manager.close()

    assert stopped.status == BackgroundTaskStatus.STOPPED


@pytest.mark.asyncio
async def test_background_task_manager_stop_completes_when_close_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(tmp_path / "background-terminal-stop-close.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)
    fake_transport = _CloseFailingWaitableTransport()

    async def _fake_spawn_runtime(
        *,
        record: BackgroundTaskRecord,
        workspace: WorkspaceHandle,
        cwd: Path,
        env: dict[str, str] | None,
        log_file_path: Path,
    ) -> object:
        del cwd, env, log_file_path
        return manager_module._BackgroundTaskRuntime(
            record=record,
            transport=cast(manager_module._BackgroundTaskTransport, fake_transport),
            log_file_path=workspace.resolve_tmp_path("close-failed.log", write=True),
            queue=asyncio.Queue(),
        )

    monkeypatch.setattr(manager, "_spawn_runtime", _fake_spawn_runtime)

    try:
        started = await manager.start_session(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            workspace=workspace,
            command="sleep 30",
            cwd=workspace.execution_root,
            timeout_ms=5000,
            env=None,
            tty=False,
        )
        stopped = await asyncio.wait_for(
            manager.stop_for_run(
                run_id="run-1",
                background_task_id=started.background_task_id,
            ),
            timeout=1,
        )
    finally:
        await manager.close()

    assert stopped.status == BackgroundTaskStatus.STOPPED
    assert fake_transport.close_attempted is True


@pytest.mark.asyncio
async def test_background_task_manager_stop_marks_tty_terminal_stopped(
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    if not (
        manager_module._posix_pty_supported() or manager_module._windows_tty_supported()
    ):
        pytest.skip("TTY background tasks are not supported on this host")

    repo = BackgroundTaskRepository(tmp_path / "background-terminal-stop-tty.db")
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
                "python -u -c \"import time; print('ready', flush=True); "
                "value = input(); print('echo:' + value, flush=True); time.sleep(30)\""
            ),
            cwd=workspace.execution_root,
            timeout_ms=5000,
            env=None,
            tty=True,
        )
        _, _ = await manager.interact_for_run(
            run_id="run-1",
            background_task_id=started.background_task_id,
            chars="hello\n",
            yield_time_ms=5000,
        )
        stopped = await asyncio.wait_for(
            manager.stop_for_run(
                run_id="run-1",
                background_task_id=started.background_task_id,
            ),
            timeout=5,
        )
    finally:
        await manager.close()

    assert stopped.status == BackgroundTaskStatus.STOPPED


@pytest.mark.asyncio
async def test_stop_all_for_run_can_leave_background_sessions_running(
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(tmp_path / "background-terminal-stop-filtered.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)

    foreground = repo.upsert(
        BackgroundTaskRecord(
            background_task_id="exec-foreground",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            command="printf ready",
            cwd=str(tmp_path),
            execution_mode="foreground",
            status=BackgroundTaskStatus.RUNNING,
        )
    )
    background = repo.upsert(
        BackgroundTaskRecord(
            background_task_id="exec-background",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-2",
            command="sleep 30",
            cwd=str(tmp_path),
            execution_mode="background",
            status=BackgroundTaskStatus.RUNNING,
        )
    )

    for record in (foreground, background):
        runtime = manager_module._BackgroundTaskRuntime(
            record=record,
            transport=cast(
                manager_module._BackgroundTaskTransport, _WaitableTransport()
            ),
            log_file_path=tmp_path / f"{record.background_task_id}.log",
            queue=asyncio.Queue(),
        )
        manager._runtimes[record.background_task_id] = runtime
        runtime.supervisor_task = asyncio.create_task(manager._supervise(runtime))

    try:
        await manager.stop_all_for_run(
            run_id="run-1",
            reason="run_finalized",
            execution_mode="foreground",
        )

        persisted_foreground = repo.get("exec-foreground")
        persisted_background = repo.get("exec-background")
        assert persisted_foreground is not None
        assert persisted_background is not None
        assert persisted_foreground.status == BackgroundTaskStatus.STOPPED
        assert persisted_background.status == BackgroundTaskStatus.RUNNING
        assert "exec-foreground" not in manager._runtimes
        assert "exec-background" in manager._runtimes
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_background_task_manager_windows_tty_transport_supports_write_resize_and_stop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module
    from relay_teams.sessions.runs.background_tasks.command_runtime import (
        CommandRuntimeKind,
        ResolvedCommandRuntime,
    )

    repo = BackgroundTaskRepository(tmp_path / "exec-session-winpty.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)
    fake_process = _FakeWindowsPtyProcess()

    resolved_commands: list[str | None] = []
    env_commands: list[str | None] = []

    async def _fake_build_command_env(
        env: dict[str, str] | None = None,
        *,
        runtime: ResolvedCommandRuntime | None = None,
        command: str | None = None,
    ) -> dict[str, str]:
        _ = runtime
        env_commands.append(command)
        return dict(env or {})

    def _fake_kill_process_tree_by_pid(pid: int) -> bool:
        assert pid == fake_process.pid
        return True

    monkeypatch.setattr(manager_module, "_posix_pty_supported", lambda: False)
    monkeypatch.setattr(manager_module, "_windows_tty_supported", lambda: True)
    monkeypatch.setattr(
        manager_module,
        "resolve_command_runtime",
        lambda *, command=None: (
            resolved_commands.append(command),
            ResolvedCommandRuntime(
                kind=CommandRuntimeKind.POWERSHELL,
                executable="powershell.exe",
                display_name="PowerShell",
            ),
        )[1],
    )
    monkeypatch.setattr(manager_module, "build_command_env", _fake_build_command_env)
    monkeypatch.setattr(
        manager_module,
        "_spawn_windows_pty_process",
        lambda **kwargs: fake_process,
    )
    monkeypatch.setattr(
        manager_module,
        "kill_process_tree_by_pid",
        _fake_kill_process_tree_by_pid,
    )

    try:
        started = await manager.start_session(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            workspace=workspace,
            command="powershell interactive",
            cwd=workspace.execution_root,
            timeout_ms=5000,
            env={"AGENT_TEAMS_CURRENT_ROLE_ID": "writer"},
            tty=True,
        )
        updated, completed = await manager.interact_for_run(
            run_id="run-1",
            background_task_id=started.background_task_id,
            chars="hello\r\n",
            yield_time_ms=5000,
        )
        echoed, _ = await manager.interact_for_run(
            run_id="run-1",
            background_task_id=started.background_task_id,
            chars="",
            yield_time_ms=5000,
        )
        resized = await manager.resize_for_run(
            run_id="run-1",
            background_task_id=started.background_task_id,
            columns=100,
            rows=30,
        )
        stopped = await manager.stop_for_run(
            run_id="run-1",
            background_task_id=started.background_task_id,
        )
    finally:
        await manager.close()

    assert completed is False
    assert updated.recent_output == ("ready",)
    assert "echo:hello" in echoed.recent_output
    assert resized.background_task_id == started.background_task_id
    assert fake_process.writes == ["hello\r\n"]
    assert fake_process.sizes == [(30, 100)]
    assert resolved_commands == ["powershell interactive"]
    assert env_commands == ["powershell interactive"]
    assert stopped.status == BackgroundTaskStatus.STOPPED


@pytest.mark.asyncio
async def test_background_task_manager_windows_tty_uses_dedicated_blocking_executor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module
    from relay_teams.sessions.runs.background_tasks.command_runtime import (
        CommandRuntimeKind,
        ResolvedCommandRuntime,
    )

    repo = BackgroundTaskRepository(tmp_path / "exec-session-winpty-executor.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)
    fake_process = _FakeWindowsPtyProcess()
    loop = asyncio.get_running_loop()
    default_executor = ThreadPoolExecutor(max_workers=1)
    default_executor_started = threading.Event()
    default_executor_release = threading.Event()
    loop.set_default_executor(default_executor)

    def _block_default_executor() -> bool:
        default_executor_started.set()
        return default_executor_release.wait(timeout=5.0)

    default_blocker = loop.run_in_executor(None, _block_default_executor)

    async def _fake_build_command_env(
        env: dict[str, str] | None = None,
        *,
        runtime: ResolvedCommandRuntime | None = None,
        command: str | None = None,
    ) -> dict[str, str]:
        _ = (runtime, command)
        return dict(env or {})

    def _fake_kill_process_tree_by_pid(pid: int) -> bool:
        assert pid == fake_process.pid
        return True

    monkeypatch.setattr(manager_module, "_posix_pty_supported", lambda: False)
    monkeypatch.setattr(manager_module, "_windows_tty_supported", lambda: True)
    monkeypatch.setattr(
        manager_module,
        "resolve_command_runtime",
        lambda *, command=None: ResolvedCommandRuntime(
            kind=CommandRuntimeKind.POWERSHELL,
            executable="powershell.exe",
            display_name="PowerShell",
        ),
    )
    monkeypatch.setattr(manager_module, "build_command_env", _fake_build_command_env)
    monkeypatch.setattr(
        manager_module,
        "_spawn_windows_pty_process",
        lambda **kwargs: fake_process,
    )
    monkeypatch.setattr(
        manager_module,
        "kill_process_tree_by_pid",
        _fake_kill_process_tree_by_pid,
    )

    try:
        for _ in range(50):
            if default_executor_started.is_set():
                break
            await asyncio.sleep(0.01)
        assert default_executor_started.is_set() is True

        started = await manager.start_session(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            workspace=workspace,
            command="powershell interactive",
            cwd=workspace.execution_root,
            timeout_ms=5000,
            env={"AGENT_TEAMS_CURRENT_ROLE_ID": "writer"},
            tty=True,
        )

        updated, completed = await asyncio.wait_for(
            manager.interact_for_run(
                run_id="run-1",
                background_task_id=started.background_task_id,
                chars="hello\r\n",
                yield_time_ms=5000,
            ),
            timeout=1.0,
        )
    finally:
        default_executor_release.set()
        await asyncio.wait_for(default_blocker, timeout=1.0)
        await manager.close()
        default_executor.shutdown(wait=True)

    assert completed is False
    assert updated.recent_output == ("ready",)
    assert fake_process.writes == ["hello\r\n"]


@pytest.mark.asyncio
async def test_background_task_manager_windows_tty_requires_supported_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(tmp_path / "exec-session-winpty-unsupported.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)

    monkeypatch.setattr(manager_module, "_posix_pty_supported", lambda: False)
    monkeypatch.setattr(manager_module, "_windows_tty_supported", lambda: False)
    monkeypatch.setattr(
        manager_module,
        "_tty_unsupported_message",
        lambda: "TTY background tasks are not supported on this Windows host",
    )

    with pytest.raises(
        ValueError,
        match="TTY background tasks are not supported on this Windows host",
    ):
        await manager.start_session(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            workspace=workspace,
            command="powershell interactive",
            cwd=workspace.execution_root,
            timeout_ms=5000,
            env=None,
            tty=True,
        )


@pytest.mark.asyncio
async def test_start_session_serializes_admission_with_async_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(tmp_path / "exec-session-admission.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)
    in_flight = 0
    max_in_flight = 0

    async def _fake_spawn_runtime(
        *,
        record: BackgroundTaskRecord,
        workspace: WorkspaceHandle,
        cwd: Path,
        env: dict[str, str] | None,
        log_file_path: Path,
    ) -> object:
        del cwd, env, log_file_path
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return manager_module._BackgroundTaskRuntime(
            record=record,
            transport=cast(manager_module._BackgroundTaskTransport, _FakeTransport()),
            log_file_path=workspace.resolve_tmp_path("noop.log", write=True),
            queue=asyncio.Queue(),
        )

    async def _fake_supervise(runtime: object) -> None:
        _ = runtime

    monkeypatch.setattr(manager, "_spawn_runtime", _fake_spawn_runtime)
    monkeypatch.setattr(manager, "_supervise", _fake_supervise)

    await asyncio.gather(
        manager.start_session(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            workspace=workspace,
            command="sleep 1",
            cwd=workspace.execution_root,
            timeout_ms=5000,
            env=None,
            tty=False,
        ),
        manager.start_session(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-2",
            workspace=workspace,
            command="sleep 1",
            cwd=workspace.execution_root,
            timeout_ms=5000,
            env=None,
            tty=False,
        ),
    )

    assert max_in_flight == 1


@pytest.mark.asyncio
async def test_start_session_rolls_back_runtime_when_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(tmp_path / "exec-session-rollback.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)
    fake_transport = _FakeTransport()

    async def _fake_spawn_runtime(
        *,
        record: BackgroundTaskRecord,
        workspace: WorkspaceHandle,
        cwd: Path,
        env: dict[str, str] | None,
        log_file_path: Path,
    ) -> object:
        del cwd, env, log_file_path
        return manager_module._BackgroundTaskRuntime(
            record=record,
            transport=cast(manager_module._BackgroundTaskTransport, fake_transport),
            log_file_path=workspace.resolve_tmp_path("rollback.log", write=True),
            queue=asyncio.Queue(),
        )

    monkeypatch.setattr(manager, "_spawn_runtime", _fake_spawn_runtime)
    monkeypatch.setattr(
        repo,
        "upsert",
        lambda record: (_ for _ in ()).throw(RuntimeError("db write failed")),
    )

    with pytest.raises(RuntimeError, match="db write failed"):
        await manager.start_session(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            workspace=workspace,
            command="sleep 1",
            cwd=workspace.execution_root,
            timeout_ms=5000,
            env=None,
            tty=False,
        )

    assert fake_transport.terminated is True
    assert fake_transport.closed is True
    assert manager._runtimes == {}


@pytest.mark.asyncio
async def test_prune_sessions_if_needed_reclaims_until_below_cap(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-terminal-prune.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    created_at = datetime.now(tz=timezone.utc) - timedelta(hours=2)

    for index in range(MAX_BACKGROUND_TASKS + 2):
        record = BackgroundTaskRecord(
            background_task_id=f"exec_{index:03d}",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id=f"call-{index}",
            command="printf 'done\\n'",
            cwd=str(tmp_path),
            status=BackgroundTaskStatus.COMPLETED,
            created_at=created_at + timedelta(minutes=index),
            updated_at=created_at + timedelta(minutes=index),
            completed_at=created_at + timedelta(minutes=index),
        )
        repo.upsert(record)

    try:
        await manager._prune_sessions_if_needed()
    finally:
        await manager.close()

    remaining_ids = {record.background_task_id for record in repo.list_all()}
    protected_ids = {
        f"exec_{index:03d}"
        for index in range(
            MAX_BACKGROUND_TASKS + 2 - PROTECTED_RECENT_BACKGROUND_TASKS,
            MAX_BACKGROUND_TASKS + 2,
        )
    }

    assert len(remaining_ids) == MAX_BACKGROUND_TASKS - 1
    assert "exec_000" not in remaining_ids
    assert "exec_001" not in remaining_ids
    assert "exec_002" not in remaining_ids
    assert protected_ids.issubset(remaining_ids)


@pytest.mark.asyncio
async def test_prune_sessions_if_needed_drops_stale_active_records_when_at_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(tmp_path / "background-terminal-prune-active.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    created_at = datetime.now(tz=timezone.utc) - timedelta(hours=2)

    killed_pids: list[int] = []

    def _fake_kill_process_tree_by_pid(pid: int) -> bool:
        killed_pids.append(pid)
        return True

    monkeypatch.setattr(
        manager_module,
        "kill_process_tree_by_pid",
        _fake_kill_process_tree_by_pid,
    )

    for index in range(MAX_BACKGROUND_TASKS):
        record = BackgroundTaskRecord(
            background_task_id=f"exec_{index:03d}",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id=f"call-{index}",
            command="sleep 30",
            cwd=str(tmp_path),
            status=BackgroundTaskStatus.RUNNING,
            pid=4000 + index,
            created_at=created_at + timedelta(minutes=index),
            updated_at=created_at + timedelta(minutes=index),
        )
        repo.upsert(record)

    try:
        await manager._prune_sessions_if_needed()
    finally:
        await manager.close()

    remaining_ids = {record.background_task_id for record in repo.list_all()}

    assert killed_pids == [4000]
    assert len(remaining_ids) == MAX_BACKGROUND_TASKS - 1
    assert "exec_000" not in remaining_ids


@pytest.mark.asyncio
async def test_prune_sessions_if_needed_keeps_active_records_when_stop_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import manager as manager_module

    repo = BackgroundTaskRepository(
        tmp_path / "background-terminal-prune-active-stop-fail.db"
    )
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
    created_at = datetime.now(tz=timezone.utc) - timedelta(hours=2)

    def _fake_kill_process_tree_by_pid(pid: int) -> bool:
        _ = pid
        return False

    monkeypatch.setattr(
        manager_module,
        "kill_process_tree_by_pid",
        _fake_kill_process_tree_by_pid,
    )

    for index in range(MAX_BACKGROUND_TASKS):
        record = BackgroundTaskRecord(
            background_task_id=f"exec_{index:03d}",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id=f"call-{index}",
            command="sleep 30",
            cwd=str(tmp_path),
            status=BackgroundTaskStatus.RUNNING,
            pid=5000 + index,
            created_at=created_at + timedelta(minutes=index),
            updated_at=created_at + timedelta(minutes=index),
        )
        repo.upsert(record)

    try:
        await manager._prune_sessions_if_needed()
    finally:
        await manager.close()

    remaining_ids = {record.background_task_id for record in repo.list_all()}

    assert len(remaining_ids) == MAX_BACKGROUND_TASKS
    assert "exec_000" in remaining_ids
