# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections import deque
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from typing import Callable, cast

import pytest

from agent_teams.sessions.runs.exec_session_manager import (
    ExecSessionManager,
    MAX_EXEC_SESSIONS,
    PROTECTED_RECENT_EXEC_SESSIONS,
)
from agent_teams.sessions.runs.exec_session_models import (
    ExecSessionRecord,
    ExecSessionStatus,
)
from agent_teams.sessions.runs.exec_session_repo import (
    ExecSessionRepository,
)
from agent_teams.sessions.runs.enums import RunEventType
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


@pytest.mark.asyncio
async def test_exec_session_manager_completes_and_publishes_events(
    tmp_path: Path,
) -> None:
    repo = ExecSessionRepository(tmp_path / "background-terminal-manager.db")
    hub = RunEventHub()
    manager = ExecSessionManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)
    queue = hub.subscribe("run-1")

    try:
        started, completed = await manager.exec_command(
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
    assert started.status == ExecSessionStatus.COMPLETED
    assert "hello" in started.recent_output
    log_path = workspace.resolve_read_path(started.log_path)
    assert log_path.read_text(encoding="utf-8") == "hello\n"
    event_types = []
    updated_payload = None
    completed_payload = None
    while not queue.empty():
        event = queue.get_nowait()
        event_types.append(event.event_type)
        if event.event_type == RunEventType.EXEC_SESSION_UPDATED:
            updated_payload = json.loads(event.payload_json)
        if event.event_type == RunEventType.EXEC_SESSION_COMPLETED:
            completed_payload = json.loads(event.payload_json)
    assert RunEventType.EXEC_SESSION_STARTED in event_types
    assert RunEventType.EXEC_SESSION_COMPLETED in event_types
    assert updated_payload is not None
    assert "output_excerpt" not in updated_payload
    assert updated_payload["delta"] == "hello\n"
    assert completed_payload is not None
    assert completed_payload["output_excerpt"] == "hello\n"


@pytest.mark.asyncio
async def test_exec_session_manager_writes_logs_via_to_thread(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent_teams.sessions.runs import exec_session_manager as manager_module

    repo = ExecSessionRepository(tmp_path / "background-terminal-log-thread.db")
    hub = RunEventHub()
    manager = ExecSessionManager(repository=repo, run_event_hub=hub)
    record = repo.upsert(
        ExecSessionRecord(
            exec_session_id="exec-1",
            run_id="run-1",
            session_id="session-1",
            command="printf ready",
            cwd=str(tmp_path),
            status=ExecSessionStatus.RUNNING,
            log_path=str(tmp_path / "exec-1.log"),
        )
    )
    runtime = manager_module._ExecSessionRuntime(
        record=record,
        transport=cast(manager_module._ExecSessionTransport, _FakeTransport()),
        log_file_path=tmp_path / "exec-1.log",
        queue=asyncio.Queue(),
    )
    calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    async def _fake_to_thread(
        func: Callable[..., object],
        *args: object,
        **kwargs: object,
    ) -> None:
        calls.append((func, args, kwargs))
        func(*args, **kwargs)

    monkeypatch.setattr(manager_module.asyncio, "to_thread", _fake_to_thread)

    await manager._handle_output_chunk(
        runtime,
        stream_name="stdout",
        chunk="hello\n",
    )

    assert calls
    assert runtime.log_file_path.read_text(encoding="utf-8") == "hello\n"


@pytest.mark.asyncio
async def test_exec_session_manager_write_finishes_shell_prompt(
    tmp_path: Path,
) -> None:
    repo = ExecSessionRepository(tmp_path / "background-terminal-write.db")
    hub = RunEventHub()
    manager = ExecSessionManager(repository=repo, run_event_hub=hub)
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
            exec_session_id=started.exec_session_id,
            chars="hello\n",
            yield_time_ms=5000,
        )
        completed_record, completed = await manager.wait_for_run(
            run_id="run-1",
            exec_session_id=started.exec_session_id,
            wait_ms=5000,
        )
    finally:
        await manager.close()

    assert completed is True
    assert completed_record.status == ExecSessionStatus.COMPLETED
    assert "line:hello" in completed_record.recent_output


@pytest.mark.asyncio
async def test_exec_session_manager_stop_marks_terminal_stopped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent_teams.sessions.runs import exec_session_manager as manager_module

    repo = ExecSessionRepository(tmp_path / "background-terminal-stop.db")
    hub = RunEventHub()
    manager = ExecSessionManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)
    fake_transport = _WaitableTransport()

    async def _fake_spawn_runtime(
        *,
        record: ExecSessionRecord,
        cwd: Path,
        env: dict[str, str] | None,
        log_file_path: Path,
    ) -> object:
        del cwd, env, log_file_path
        return manager_module._ExecSessionRuntime(
            record=record,
            transport=cast(manager_module._ExecSessionTransport, fake_transport),
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
            exec_session_id=started.exec_session_id,
        )
    finally:
        await manager.close()

    assert stopped.status == ExecSessionStatus.STOPPED


@pytest.mark.asyncio
async def test_exec_session_manager_stop_marks_tty_terminal_stopped(
    tmp_path: Path,
) -> None:
    from agent_teams.sessions.runs import exec_session_manager as manager_module

    if not (
        manager_module._posix_pty_supported() or manager_module._windows_tty_supported()
    ):
        pytest.skip("TTY exec sessions are not supported on this host")

    repo = ExecSessionRepository(tmp_path / "background-terminal-stop-tty.db")
    hub = RunEventHub()
    manager = ExecSessionManager(repository=repo, run_event_hub=hub)
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
            exec_session_id=started.exec_session_id,
            chars="hello\n",
            yield_time_ms=5000,
        )
        stopped = await asyncio.wait_for(
            manager.stop_for_run(
                run_id="run-1",
                exec_session_id=started.exec_session_id,
            ),
            timeout=5,
        )
    finally:
        await manager.close()

    assert stopped.status == ExecSessionStatus.STOPPED


@pytest.mark.asyncio
async def test_stop_all_for_run_can_leave_background_sessions_running(
    tmp_path: Path,
) -> None:
    from agent_teams.sessions.runs import exec_session_manager as manager_module

    repo = ExecSessionRepository(tmp_path / "background-terminal-stop-filtered.db")
    hub = RunEventHub()
    manager = ExecSessionManager(repository=repo, run_event_hub=hub)

    foreground = repo.upsert(
        ExecSessionRecord(
            exec_session_id="exec-foreground",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            command="printf ready",
            cwd=str(tmp_path),
            execution_mode="foreground",
            status=ExecSessionStatus.RUNNING,
        )
    )
    background = repo.upsert(
        ExecSessionRecord(
            exec_session_id="exec-background",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-2",
            command="sleep 30",
            cwd=str(tmp_path),
            execution_mode="background",
            status=ExecSessionStatus.RUNNING,
        )
    )

    for record in (foreground, background):
        runtime = manager_module._ExecSessionRuntime(
            record=record,
            transport=cast(manager_module._ExecSessionTransport, _WaitableTransport()),
            log_file_path=tmp_path / f"{record.exec_session_id}.log",
            queue=asyncio.Queue(),
        )
        manager._runtimes[record.exec_session_id] = runtime
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
        assert persisted_foreground.status == ExecSessionStatus.STOPPED
        assert persisted_background.status == ExecSessionStatus.RUNNING
        assert "exec-foreground" not in manager._runtimes
        assert "exec-background" in manager._runtimes
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_exec_session_manager_windows_tty_transport_supports_write_resize_and_stop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent_teams.sessions.runs import exec_session_manager as manager_module
    from agent_teams.tools.workspace_tools.shell_executor import (
        ResolvedShell,
        ShellKind,
    )

    repo = ExecSessionRepository(tmp_path / "exec-session-winpty.db")
    hub = RunEventHub()
    manager = ExecSessionManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)
    fake_process = _FakeWindowsPtyProcess()

    async def _fake_build_shell_env(
        env: dict[str, str] | None = None,
        *,
        shell: ResolvedShell | None = None,
    ) -> dict[str, str]:
        _ = shell
        return dict(env or {})

    async def _fake_kill_process_tree_by_pid(pid: int) -> None:
        assert pid == fake_process.pid

    monkeypatch.setattr(manager_module, "_posix_pty_supported", lambda: False)
    monkeypatch.setattr(manager_module, "_windows_tty_supported", lambda: True)
    monkeypatch.setattr(
        manager_module,
        "resolve_exec_shell",
        lambda: ResolvedShell(
            kind=ShellKind.POWERSHELL,
            executable="powershell.exe",
            display_name="PowerShell",
        ),
    )
    monkeypatch.setattr(manager_module, "build_shell_env", _fake_build_shell_env)
    monkeypatch.setattr(
        manager_module,
        "_spawn_windows_pty_process",
        lambda **kwargs: fake_process,
    )
    monkeypatch.setattr(
        manager_module,
        "_kill_process_tree_by_pid",
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
            exec_session_id=started.exec_session_id,
            chars="hello\r\n",
            yield_time_ms=5000,
        )
        echoed, _ = await manager.interact_for_run(
            run_id="run-1",
            exec_session_id=started.exec_session_id,
            chars="",
            yield_time_ms=5000,
        )
        resized = await manager.resize_for_run(
            run_id="run-1",
            exec_session_id=started.exec_session_id,
            columns=100,
            rows=30,
        )
        stopped = await manager.stop_for_run(
            run_id="run-1",
            exec_session_id=started.exec_session_id,
        )
    finally:
        await manager.close()

    assert completed is False
    assert updated.recent_output == ("ready",)
    assert "echo:hello" in echoed.recent_output
    assert resized.exec_session_id == started.exec_session_id
    assert fake_process.writes == ["hello\r\n"]
    assert fake_process.sizes == [(30, 100)]
    assert stopped.status == ExecSessionStatus.STOPPED


@pytest.mark.asyncio
async def test_exec_session_manager_windows_tty_requires_supported_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent_teams.sessions.runs import exec_session_manager as manager_module

    repo = ExecSessionRepository(tmp_path / "exec-session-winpty-unsupported.db")
    hub = RunEventHub()
    manager = ExecSessionManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)

    monkeypatch.setattr(manager_module, "_posix_pty_supported", lambda: False)
    monkeypatch.setattr(manager_module, "_windows_tty_supported", lambda: False)
    monkeypatch.setattr(
        manager_module,
        "_tty_unsupported_message",
        lambda: "TTY exec sessions are not supported on this Windows host",
    )

    with pytest.raises(
        ValueError,
        match="TTY exec sessions are not supported on this Windows host",
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
    from agent_teams.sessions.runs import exec_session_manager as manager_module

    repo = ExecSessionRepository(tmp_path / "exec-session-admission.db")
    hub = RunEventHub()
    manager = ExecSessionManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)
    in_flight = 0
    max_in_flight = 0

    async def _fake_spawn_runtime(
        *,
        record: ExecSessionRecord,
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
        return manager_module._ExecSessionRuntime(
            record=record,
            transport=cast(manager_module._ExecSessionTransport, _FakeTransport()),
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
    from agent_teams.sessions.runs import exec_session_manager as manager_module

    repo = ExecSessionRepository(tmp_path / "exec-session-rollback.db")
    hub = RunEventHub()
    manager = ExecSessionManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)
    fake_transport = _FakeTransport()

    async def _fake_spawn_runtime(
        *,
        record: ExecSessionRecord,
        cwd: Path,
        env: dict[str, str] | None,
        log_file_path: Path,
    ) -> object:
        del cwd, env, log_file_path
        return manager_module._ExecSessionRuntime(
            record=record,
            transport=cast(manager_module._ExecSessionTransport, fake_transport),
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
    repo = ExecSessionRepository(tmp_path / "background-terminal-prune.db")
    hub = RunEventHub()
    manager = ExecSessionManager(repository=repo, run_event_hub=hub)
    created_at = datetime.now(tz=timezone.utc) - timedelta(hours=2)

    for index in range(MAX_EXEC_SESSIONS + 2):
        record = ExecSessionRecord(
            exec_session_id=f"exec_{index:03d}",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id=f"call-{index}",
            command="printf 'done\\n'",
            cwd=str(tmp_path),
            status=ExecSessionStatus.COMPLETED,
            created_at=created_at + timedelta(minutes=index),
            updated_at=created_at + timedelta(minutes=index),
            completed_at=created_at + timedelta(minutes=index),
        )
        repo.upsert(record)

    try:
        await manager._prune_sessions_if_needed()
    finally:
        await manager.close()

    remaining_ids = {record.exec_session_id for record in repo.list_all()}
    protected_ids = {
        f"exec_{index:03d}"
        for index in range(
            MAX_EXEC_SESSIONS + 2 - PROTECTED_RECENT_EXEC_SESSIONS,
            MAX_EXEC_SESSIONS + 2,
        )
    }

    assert len(remaining_ids) == MAX_EXEC_SESSIONS - 1
    assert "exec_000" not in remaining_ids
    assert "exec_001" not in remaining_ids
    assert "exec_002" not in remaining_ids
    assert protected_ids.issubset(remaining_ids)


@pytest.mark.asyncio
async def test_prune_sessions_if_needed_drops_stale_active_records_when_at_cap(
    tmp_path: Path,
) -> None:
    repo = ExecSessionRepository(tmp_path / "background-terminal-prune-active.db")
    hub = RunEventHub()
    manager = ExecSessionManager(repository=repo, run_event_hub=hub)
    created_at = datetime.now(tz=timezone.utc) - timedelta(hours=2)

    for index in range(MAX_EXEC_SESSIONS):
        record = ExecSessionRecord(
            exec_session_id=f"exec_{index:03d}",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id=f"call-{index}",
            command="sleep 30",
            cwd=str(tmp_path),
            status=ExecSessionStatus.RUNNING,
            created_at=created_at + timedelta(minutes=index),
            updated_at=created_at + timedelta(minutes=index),
        )
        repo.upsert(record)

    try:
        await manager._prune_sessions_if_needed()
    finally:
        await manager.close()

    remaining_ids = {record.exec_session_id for record in repo.list_all()}

    assert len(remaining_ids) == MAX_EXEC_SESSIONS - 1
    assert "exec_000" not in remaining_ids
