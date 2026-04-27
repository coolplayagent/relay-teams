# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable
from collections import deque
import codecs
import contextlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import errno
from functools import partial
import importlib
import json
import logging
import os
import posixpath
from pathlib import Path
import struct
import shutil
from typing import Literal, ParamSpec, Protocol, TypeVar, cast
from uuid import uuid4

try:
    import fcntl
    import pty
    import termios
except ImportError:
    fcntl = None
    pty = None
    termios = None

from pydantic import JsonValue

from relay_teams.logger import get_logger, log_event
from relay_teams.monitors import MonitorEventEnvelope, MonitorService, MonitorSourceKind
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub, publish_run_event_async
from relay_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from relay_teams.sessions.runs.background_tasks.repository import (
    BackgroundTaskRepository,
)
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.background_tasks.command_runtime import (
    ResolvedCommandRuntime,
    _PipeProcess,
    _kill_process_tree,
    _start_new_session,
    build_command_argv,
    build_command_env,
    create_command_subprocess,
    create_prepared_subprocess,
    kill_process_tree_by_pid,
    resolve_command_runtime,
    windows_conpty_supported,
)
from relay_teams.workspace import WorkspaceHandle
from relay_teams.workspace.ssh_profile_service import SshProfileService
from relay_teams.workspace.workspace_models import (
    WorkspaceMountProvider,
    WorkspaceMountRecord,
    WorkspaceSshMountConfig,
)

LOGGER = get_logger(__name__)
ParamT = ParamSpec("ParamT")
ResultT = TypeVar("ResultT")

DEFAULT_BACKGROUND_TASK_TIMEOUT_MS = 30 * 60 * 1000
MIN_EXEC_COMMAND_YIELD_MS = 250
MIN_EMPTY_POLL_YIELD_MS = 5000
MAX_WRITE_WAIT_MS = 30000
MAX_BACKGROUND_POLL_MS = 300000
MAX_BACKGROUND_TASKS = 64
PROTECTED_RECENT_BACKGROUND_TASKS = 8
MAX_RECENT_OUTPUT_LINES = 3
MAX_OUTPUT_BUFFER_BYTES = 1024 * 1024
MAX_DELTA_BYTES = 8192
COMMAND_BLOCKING_WORKER_COUNT = 8
COMMAND_PTY_WORKER_COUNT = MAX_BACKGROUND_TASKS * 2
_DEFAULT_PTY_COLUMNS = 120
_DEFAULT_PTY_ROWS = 40
_STOP_WAIT_TIMEOUT_SECONDS = 10.0


class _RecentOutputBuffer:
    def __init__(self, *, max_lines: int) -> None:
        self._lines: deque[str] = deque(maxlen=max_lines)
        self._partial = ""

    def feed(self, chunk: str) -> None:
        normalized = chunk.replace("\r\n", "\n").replace("\r", "\n")
        text = self._partial + normalized
        parts = text.split("\n")
        self._partial = parts.pop() if parts else ""
        for line in parts:
            safe_line = line.strip()
            if safe_line:
                self._lines.append(safe_line)

    def finalize(self) -> None:
        safe_line = self._partial.strip()
        if safe_line:
            self._lines.append(safe_line)
        self._partial = ""

    def snapshot(self) -> tuple[str, ...]:
        return tuple(self._lines)


class _HeadTailBuffer:
    def __init__(self, *, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._head_budget = max_bytes // 2
        self._tail_budget = max_bytes - self._head_budget
        self._head = bytearray()
        self._tail = bytearray()
        self._truncated = False

    def append(self, chunk: str) -> None:
        raw = chunk.encode("utf-8", errors="replace")
        if not raw:
            return
        if len(self._head) < self._head_budget:
            remaining = self._head_budget - len(self._head)
            self._head.extend(raw[:remaining])
            raw = raw[remaining:]
        if raw:
            self._truncated = True
            combined = bytes(self._tail) + raw
            self._tail = bytearray(combined[-self._tail_budget :])

    def render(self) -> str:
        if not self._truncated:
            return bytes(self._head).decode("utf-8", errors="replace")
        head_text = bytes(self._head).decode("utf-8", errors="replace")
        tail_text = bytes(self._tail).decode("utf-8", errors="replace")
        if not head_text:
            return tail_text
        if not tail_text:
            return head_text
        return f"{head_text}\n\n... omitted ...\n\n{tail_text}"


class _MonitorOutputLineBuffer:
    def __init__(self) -> None:
        self._partial = ""

    def feed(self, chunk: str) -> tuple[str, ...]:
        normalized = chunk.replace("\r\n", "\n").replace("\r", "\n")
        text = self._partial + normalized
        parts = text.split("\n")
        self._partial = parts.pop() if parts else ""
        return tuple(line.strip() for line in parts if line.strip())

    def finalize(self) -> tuple[str, ...]:
        line = self._partial.strip()
        self._partial = ""
        if not line:
            return ()
        return (line,)


class _WindowsPtyProcessProtocol(Protocol):
    pid: int | None

    def read(self, size: int = 1024) -> str: ...

    def write(self, s: str) -> int: ...

    def isalive(self) -> bool: ...

    def wait(self) -> int: ...

    def close(self, force: bool = False) -> None: ...

    def setwinsize(self, rows: int, cols: int) -> None: ...


class _WindowsPtyProcessFactoryProtocol(Protocol):
    @staticmethod
    def spawn(
        argv: str | list[str] | tuple[str, ...],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        dimensions: tuple[int, int] = (24, 80),
        backend: int | None = None,
    ) -> _WindowsPtyProcessProtocol: ...


class _BackgroundTaskCompletionListener(Protocol):
    async def __call__(self, record: BackgroundTaskRecord) -> None: ...


class _BackgroundTaskTransport(ABC):
    @property
    @abstractmethod
    def tty(self) -> bool:
        raise NotImplementedError

    @property
    @abstractmethod
    def stream_count(self) -> int:
        raise NotImplementedError

    @property
    @abstractmethod
    def pid(self) -> int | None:
        raise NotImplementedError

    @property
    @abstractmethod
    def returncode(self) -> int | None:
        raise NotImplementedError

    @abstractmethod
    def start_pumps(
        self,
        *,
        manager: BackgroundTaskManager,
        runtime: _BackgroundTaskRuntime,
    ) -> list[asyncio.Task[None]]: ...

    @abstractmethod
    async def wait(self) -> int | None: ...

    @abstractmethod
    async def write(self, chars: str) -> None: ...

    @abstractmethod
    async def resize(self, *, columns: int, rows: int) -> None: ...

    @abstractmethod
    async def terminate(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


class _PipeTransport(_BackgroundTaskTransport):
    def __init__(self, proc: _PipeProcess, *, cleanup_root: Path | None = None) -> None:
        self._proc = proc
        self._cleanup_root = cleanup_root

    @property
    def tty(self) -> bool:
        return False

    @property
    def stream_count(self) -> int:
        return 2

    @property
    def pid(self) -> int | None:
        return self._proc.pid

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    def start_pumps(
        self,
        *,
        manager: BackgroundTaskManager,
        runtime: _BackgroundTaskRuntime,
    ) -> list[asyncio.Task[None]]:
        stdout = self._proc.stdout
        stderr = self._proc.stderr
        if stdout is None or stderr is None:
            raise RuntimeError("Failed to capture background task streams")
        return [
            asyncio.create_task(manager._pump_stream("stdout", stdout, runtime.queue)),
            asyncio.create_task(manager._pump_stream("stderr", stderr, runtime.queue)),
        ]

    async def wait(self) -> int | None:
        return await self._proc.wait()

    async def write(self, chars: str) -> None:
        writer = self._proc.stdin
        if writer is None:
            raise RuntimeError("Background task stdin is not available")
        writer.write(chars.encode("utf-8", errors="replace"))
        await writer.drain()

    async def resize(self, *, columns: int, rows: int) -> None:
        _ = (columns, rows)
        raise ValueError("resize is only supported for active TTY background tasks")

    async def terminate(self) -> None:
        await _kill_process_tree(self._proc)

    async def close(self) -> None:
        if self._proc.stdin is not None:
            self._proc.stdin.close()
        if self._cleanup_root is not None:
            await asyncio.to_thread(shutil.rmtree, self._cleanup_root, True)


class _PosixPtyTransport(_BackgroundTaskTransport):
    def __init__(
        self,
        *,
        proc: asyncio.subprocess.Process,
        master_fd: int,
        cleanup_root: Path | None = None,
    ) -> None:
        self._proc = proc
        self._master_fd = master_fd
        self._cleanup_root = cleanup_root

    @property
    def tty(self) -> bool:
        return True

    @property
    def stream_count(self) -> int:
        return 1

    @property
    def pid(self) -> int | None:
        return self._proc.pid

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    def start_pumps(
        self,
        *,
        manager: BackgroundTaskManager,
        runtime: _BackgroundTaskRuntime,
    ) -> list[asyncio.Task[None]]:
        return [asyncio.create_task(manager._pump_master_fd(runtime, self._master_fd))]

    async def wait(self) -> int | None:
        return await self._proc.wait()

    async def write(self, chars: str) -> None:
        os.write(self._master_fd, chars.encode("utf-8", errors="replace"))

    async def resize(self, *, columns: int, rows: int) -> None:
        _set_terminal_size(self._master_fd, columns=columns, rows=rows)

    async def terminate(self) -> None:
        await _kill_process_tree(self._proc)

    async def close(self) -> None:
        with contextlib.suppress(OSError):
            os.close(self._master_fd)
        if self._cleanup_root is not None:
            await asyncio.to_thread(shutil.rmtree, self._cleanup_root, True)


class _WindowsConPtyTransport(_BackgroundTaskTransport):
    def __init__(
        self,
        proc: _WindowsPtyProcessProtocol,
        *,
        blocking_executor: ThreadPoolExecutor,
        pty_executor: ThreadPoolExecutor,
        cleanup_root: Path | None = None,
    ) -> None:
        self._proc = proc
        self._blocking_executor = blocking_executor
        self._pty_executor = pty_executor
        self._cached_returncode: int | None = None
        self._cleanup_root = cleanup_root

    async def _run_blocking(
        self,
        function: Callable[ParamT, ResultT],
        /,
        *args: ParamT.args,
        **kwargs: ParamT.kwargs,
    ) -> ResultT:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._blocking_executor,
            partial(function, *args, **kwargs),
        )

    async def _run_pty_blocking(
        self,
        function: Callable[ParamT, ResultT],
        /,
        *args: ParamT.args,
        **kwargs: ParamT.kwargs,
    ) -> ResultT:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._pty_executor,
            partial(function, *args, **kwargs),
        )

    @property
    def tty(self) -> bool:
        return True

    @property
    def stream_count(self) -> int:
        return 1

    @property
    def pid(self) -> int | None:
        return self._proc.pid

    @property
    def returncode(self) -> int | None:
        return self._cached_returncode

    def start_pumps(
        self,
        *,
        manager: BackgroundTaskManager,
        runtime: _BackgroundTaskRuntime,
    ) -> list[asyncio.Task[None]]:
        return [
            asyncio.create_task(manager._pump_windows_pty_process(runtime, self._proc))
        ]

    async def wait(self) -> int | None:
        if self._cached_returncode is not None:
            return self._cached_returncode
        self._cached_returncode = await self._run_pty_blocking(self._proc.wait)
        return self._cached_returncode

    async def write(self, chars: str) -> None:
        await self._run_blocking(self._proc.write, chars)

    async def resize(self, *, columns: int, rows: int) -> None:
        await self._run_blocking(self._proc.setwinsize, rows, columns)

    async def terminate(self) -> None:
        pid = self._proc.pid
        if pid is not None:
            _ = await self._run_blocking(kill_process_tree_by_pid, pid)
        await self._run_blocking(self._proc.close, True)

    async def close(self) -> None:
        await self._run_blocking(self._proc.close)
        if self._cleanup_root is not None:
            await self._run_blocking(shutil.rmtree, self._cleanup_root, True)


class _BackgroundTaskRuntime:
    def __init__(
        self,
        *,
        record: BackgroundTaskRecord,
        transport: _BackgroundTaskTransport,
        log_file_path: Path,
        queue: asyncio.Queue[tuple[str, str] | None],
    ) -> None:
        self.record = record
        self.transport = transport
        self.log_file_path = log_file_path
        self.tty = transport.tty
        self.queue = queue
        self.stream_count = transport.stream_count
        self.recent_output = _RecentOutputBuffer(max_lines=MAX_RECENT_OUTPUT_LINES)
        self.output_buffer = _HeadTailBuffer(max_bytes=MAX_OUTPUT_BUFFER_BYTES)
        self.monitor_line_buffers: dict[str, _MonitorOutputLineBuffer] = {}
        self.monitor_line_sequence = 0
        self.pump_tasks: list[asyncio.Task[None]] = []
        self.supervisor_task: asyncio.Task[None] | None = None
        self.completed = asyncio.Event()
        self.finalize_lock = asyncio.Lock()
        self.change_condition = asyncio.Condition()
        self.change_version = 0
        self.stop_requested = False


class BackgroundTaskManager:
    def __init__(
        self,
        *,
        repository: BackgroundTaskRepository,
        run_event_hub: RunEventHub,
        monitor_service: MonitorService | None = None,
        ssh_profile_service: SshProfileService | None = None,
    ) -> None:
        self._repository = repository
        self._run_event_hub = run_event_hub
        self._monitor_service = monitor_service
        self._ssh_profile_service = ssh_profile_service
        self._runtimes: dict[str, _BackgroundTaskRuntime] = {}
        self._admission_lock = asyncio.Lock()
        self._completion_listener: _BackgroundTaskCompletionListener | None = None
        self._blocking_executor = ThreadPoolExecutor(
            max_workers=COMMAND_BLOCKING_WORKER_COUNT,
            thread_name_prefix="background-task-control",
        )
        self._pty_executor = ThreadPoolExecutor(
            max_workers=COMMAND_PTY_WORKER_COUNT,
            thread_name_prefix="background-task-pty",
        )

    async def _run_blocking(
        self,
        function: Callable[ParamT, ResultT],
        /,
        *args: ParamT.args,
        **kwargs: ParamT.kwargs,
    ) -> ResultT:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._blocking_executor,
            partial(function, *args, **kwargs),
        )

    async def _run_pty_blocking(
        self,
        function: Callable[ParamT, ResultT],
        /,
        *args: ParamT.args,
        **kwargs: ParamT.kwargs,
    ) -> ResultT:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._pty_executor,
            partial(function, *args, **kwargs),
        )

    def set_completion_listener(
        self,
        listener: _BackgroundTaskCompletionListener | None,
    ) -> None:
        self._completion_listener = listener

    async def start_session(
        self,
        *,
        run_id: str,
        session_id: str,
        instance_id: str,
        role_id: str,
        tool_call_id: str | None,
        workspace: WorkspaceHandle,
        command: str,
        cwd: Path,
        timeout_ms: int | None,
        env: dict[str, str] | None,
        tty: bool,
        execution_mode: Literal["foreground", "background"] = "background",
    ) -> BackgroundTaskRecord:
        async with self._admission_lock:
            await self._prune_sessions_if_needed()
            effective_timeout_ms = timeout_ms or DEFAULT_BACKGROUND_TASK_TIMEOUT_MS
            background_task_id = f"background_task_{uuid4().hex[:12]}"
            log_dir = workspace.resolve_tmp_path("background_tasks", write=True)
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file_path = log_dir / f"{background_task_id}.log"
            log_file_path.touch(exist_ok=True)
            logical_log_path = workspace.logical_tmp_path(log_file_path)
            record = BackgroundTaskRecord(
                background_task_id=background_task_id,
                run_id=run_id,
                session_id=session_id,
                instance_id=instance_id,
                role_id=role_id,
                tool_call_id=tool_call_id,
                command=command,
                cwd=str(cwd),
                execution_mode=execution_mode,
                tty=tty,
                timeout_ms=effective_timeout_ms,
                log_path=logical_log_path,
            )
            runtime = await self._spawn_runtime(
                record=record,
                workspace=workspace,
                cwd=cwd,
                env=env,
                log_file_path=log_file_path,
            )
            self._runtimes[background_task_id] = runtime
            record = record.model_copy(update={"pid": runtime.transport.pid})
            runtime.record = record
            persisted = False
            try:
                runtime.record = await self._repository.upsert_async(record)
                persisted = True
                await self._publish_background_task_event_async(
                    event_type=RunEventType.BACKGROUND_TASK_STARTED,
                    record=runtime.record,
                )
                await self._emit_monitor_state_event_async(
                    record=runtime.record,
                    event_name="background_task.started",
                )
            except Exception:
                self._runtimes.pop(background_task_id, None)
                if persisted:
                    await self._repository.delete_async(background_task_id)
                await self._rollback_runtime(runtime)
                raise
            runtime.supervisor_task = asyncio.create_task(self._supervise(runtime))
            return runtime.record

    async def run_command(
        self,
        *,
        run_id: str,
        session_id: str,
        instance_id: str,
        role_id: str,
        tool_call_id: str | None,
        workspace: WorkspaceHandle,
        command: str,
        cwd: Path,
        timeout_ms: int | None,
        yield_time_ms: int | None,
        env: dict[str, str] | None,
        tty: bool,
    ) -> tuple[BackgroundTaskRecord, bool]:
        record = await self.start_session(
            run_id=run_id,
            session_id=session_id,
            instance_id=instance_id,
            role_id=role_id,
            tool_call_id=tool_call_id,
            workspace=workspace,
            command=command,
            cwd=cwd,
            timeout_ms=timeout_ms,
            env=env,
            tty=tty,
            execution_mode="background",
        )
        updated, _ = await self.interact_for_run(
            run_id=run_id,
            background_task_id=record.background_task_id,
            chars="",
            yield_time_ms=yield_time_ms,
            is_initial_poll=True,
        )
        if updated.is_active or record.background_task_id in self._runtimes:
            follow_up, completed = await self.wait_for_run(
                run_id=run_id,
                background_task_id=record.background_task_id,
            )
            return follow_up, completed
        return updated, True

    def list_for_run(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
        return self._repository.list_by_run(run_id)

    async def list_for_run_async(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
        return await self._repository.list_by_run_async(run_id)

    def get_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> BackgroundTaskRecord:
        record = self._get_record(background_task_id)
        if record.run_id != run_id:
            raise KeyError(
                f"Background task {background_task_id} does not belong to run {run_id}"
            )
        return record

    async def get_for_run_async(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> BackgroundTaskRecord:
        record = await self._get_record_async(background_task_id)
        if record.run_id != run_id:
            raise KeyError(
                f"Background task {background_task_id} does not belong to run {run_id}"
            )
        return record

    async def wait_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> tuple[BackgroundTaskRecord, bool]:
        record = await self.get_for_run_async(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        runtime = self._runtimes.get(background_task_id)
        if runtime is not None:
            await runtime.completed.wait()
            return await self._get_record_async(background_task_id), True
        if not record.is_active:
            return record, True
        return record, False

    async def interact_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
        chars: str,
        yield_time_ms: int | None,
        is_initial_poll: bool = False,
    ) -> tuple[BackgroundTaskRecord, bool]:
        record = await self.get_for_run_async(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        runtime = self._runtimes.get(background_task_id)
        if not record.is_active:
            return record, True
        if runtime is None:
            return record, False
        before_version = runtime.change_version
        if chars:
            await self._write_chars(runtime, chars)
        timeout_ms = _normalize_poll_timeout(
            chars=chars,
            yield_time_ms=yield_time_ms,
            is_initial_poll=is_initial_poll,
        )
        _ = await self._wait_for_runtime_change(
            runtime=runtime,
            before_version=before_version,
            timeout_ms=timeout_ms,
        )
        updated = await self._get_record_async(background_task_id)
        return updated, not updated.is_active

    async def resize_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
        columns: int,
        rows: int,
    ) -> BackgroundTaskRecord:
        if columns < 1 or rows < 1:
            raise ValueError("columns and rows must both be >= 1")
        record = await self.get_for_run_async(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        runtime = self._runtimes.get(background_task_id)
        if runtime is None or not record.is_active:
            return record
        if not runtime.tty:
            raise ValueError("resize is only supported for active TTY background tasks")
        await runtime.transport.resize(columns=columns, rows=rows)
        return self._get_record(background_task_id)

    async def stop_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
        reason: str = "stopped_by_user",
    ) -> BackgroundTaskRecord:
        _ = reason
        record = await self.get_for_run_async(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        runtime = self._runtimes.get(background_task_id)
        if not record.is_active:
            return record
        if runtime is None:
            return await self._stop_persisted_record_without_runtime(record)
        runtime.stop_requested = True
        if runtime.transport.returncode is None:
            await runtime.transport.terminate()
        await self._signal_runtime_stop(runtime)
        try:
            if runtime.supervisor_task is not None:
                await asyncio.wait_for(
                    asyncio.shield(runtime.supervisor_task),
                    timeout=_STOP_WAIT_TIMEOUT_SECONDS,
                )
            else:
                await asyncio.wait_for(
                    runtime.completed.wait(),
                    timeout=_STOP_WAIT_TIMEOUT_SECONDS,
                )
        except asyncio.TimeoutError:
            LOGGER.warning(
                "Timed out waiting for background task supervisor to stop",
                extra={"background_task_id": background_task_id},
            )
            supervisor_task = runtime.supervisor_task
            if supervisor_task is not None and not supervisor_task.done():
                supervisor_task.cancel()
                with contextlib.suppress(Exception):
                    await asyncio.gather(supervisor_task, return_exceptions=True)
            await self._finalize_runtime(
                runtime,
                timed_out=False,
                wait_for_exit=False,
            )
        return await self._get_record_async(background_task_id)

    async def _stop_persisted_record_without_runtime(
        self,
        record: BackgroundTaskRecord,
    ) -> BackgroundTaskRecord:
        if record.pid is None:
            LOGGER.warning(
                "Cannot stop persisted active background task without runtime or pid",
                extra={"background_task_id": record.background_task_id},
            )
            return record
        killed = await self._run_blocking(kill_process_tree_by_pid, record.pid)
        if not killed:
            LOGGER.warning(
                "Failed to stop persisted background task without runtime",
                extra={
                    "background_task_id": record.background_task_id,
                    "pid": record.pid,
                },
            )
            return await self._get_record_async(record.background_task_id)
        completed_at = datetime.now(tz=timezone.utc)
        updated = await self._repository.upsert_async(
            record.model_copy(
                update={
                    "status": BackgroundTaskStatus.STOPPED,
                    "pid": None,
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                }
            )
        )
        await self._publish_background_task_event_async(
            event_type=RunEventType.BACKGROUND_TASK_STOPPED,
            record=updated,
        )
        await self._emit_monitor_state_event_async(
            record=updated,
            event_name="background_task.stopped",
        )
        return updated

    async def stop_all_for_run(
        self,
        *,
        run_id: str,
        reason: str,
        execution_mode: str | None = None,
    ) -> None:
        active_ids = [
            record.background_task_id
            for record in await self._repository.list_by_run_async(run_id)
            if record.is_active
            and (execution_mode is None or record.execution_mode == execution_mode)
        ]
        for background_task_id in active_ids:
            with contextlib.suppress(KeyError):
                _ = await self.stop_for_run(
                    run_id=run_id,
                    background_task_id=background_task_id,
                    reason=reason,
                )

    async def close(self) -> None:
        active_ids = list(self._runtimes.keys())
        for background_task_id in active_ids:
            record = await self._repository.get_async(background_task_id)
            if record is None:
                continue
            with contextlib.suppress(KeyError):
                _ = await self.stop_for_run(
                    run_id=record.run_id,
                    background_task_id=background_task_id,
                    reason="server_shutdown",
                )
        self._blocking_executor.shutdown(wait=False, cancel_futures=True)
        self._pty_executor.shutdown(wait=False, cancel_futures=True)

    async def _spawn_runtime(
        self,
        *,
        record: BackgroundTaskRecord,
        workspace: WorkspaceHandle,
        cwd: Path,
        env: dict[str, str] | None,
        log_file_path: Path,
    ) -> _BackgroundTaskRuntime:
        queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        ssh_context = self._resolve_ssh_execution_context(
            workspace=workspace,
            cwd=cwd,
        )
        if record.tty:
            if ssh_context is None:
                transport = await self._spawn_tty_transport(
                    command=record.command,
                    cwd=cwd,
                    env=env,
                )
            else:
                transport = await self._spawn_ssh_tty_transport(
                    command=record.command,
                    ssh_context=ssh_context,
                    env=env,
                )
        else:
            if ssh_context is None:
                proc = await create_command_subprocess(
                    command=record.command,
                    cwd=cwd,
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                transport = _PipeTransport(proc)
            else:
                transport = await self._spawn_ssh_pipe_transport(
                    command=record.command,
                    ssh_context=ssh_context,
                    env=env,
                )
        runtime = _BackgroundTaskRuntime(
            record=record,
            transport=transport,
            log_file_path=log_file_path,
            queue=queue,
        )
        runtime.pump_tasks.extend(transport.start_pumps(manager=self, runtime=runtime))
        return runtime

    def _resolve_ssh_execution_context(
        self,
        *,
        workspace: WorkspaceHandle,
        cwd: Path,
    ) -> tuple[WorkspaceMountRecord, str] | None:
        resolved_cwd = cwd.resolve()
        for remote_mount_root in workspace.locations.remote_mount_roots:
            local_root = remote_mount_root.local_root.resolve()
            if resolved_cwd != local_root and local_root not in resolved_cwd.parents:
                continue
            mount = workspace.mount_by_name(remote_mount_root.mount_name)
            if mount.provider != WorkspaceMountProvider.SSH:
                continue
            remote_cwd = remote_mount_root.remote_root
            if resolved_cwd != local_root:
                remote_cwd = posixpath.join(
                    remote_mount_root.remote_root,
                    resolved_cwd.relative_to(local_root).as_posix(),
                )
            return mount, remote_cwd
        return None

    async def _spawn_ssh_pipe_transport(
        self,
        *,
        command: str,
        ssh_context: tuple[WorkspaceMountRecord, str],
        env: dict[str, str] | None,
    ) -> _PipeTransport:
        proc, cleanup_root = await self._create_ssh_subprocess(
            command=command,
            ssh_context=ssh_context,
            env=env,
            tty=False,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return _PipeTransport(proc, cleanup_root=cleanup_root)

    async def _create_ssh_subprocess(
        self,
        *,
        command: str,
        ssh_context: tuple[WorkspaceMountRecord, str],
        env: dict[str, str] | None,
        tty: bool,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
    ) -> tuple[_PipeProcess, Path]:
        if self._ssh_profile_service is None:
            raise ValueError("SSH workspace command execution requires ssh profiles")
        mount, remote_cwd = ssh_context
        provider_config = mount.provider_config
        if not isinstance(provider_config, WorkspaceSshMountConfig):
            raise ValueError(
                f"Workspace ssh mount is missing ssh config: {mount.mount_name}"
            )
        prepared = self._ssh_profile_service.prepare_remote_command(
            ssh_profile_id=provider_config.ssh_profile_id,
            command=command,
            cwd=remote_cwd,
            env=env,
            tty=tty,
        )
        try:
            proc = await create_prepared_subprocess(
                argv=prepared.argv,
                env=prepared.env,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
            )
        except Exception:
            shutil.rmtree(prepared.temp_root, ignore_errors=True)
            raise
        return proc, prepared.temp_root

    async def _spawn_ssh_tty_transport(
        self,
        *,
        command: str,
        ssh_context: tuple[WorkspaceMountRecord, str],
        env: dict[str, str] | None,
    ) -> _BackgroundTaskTransport:
        if _posix_pty_supported():
            assert pty is not None
            proc, cleanup_root = await self._create_ssh_pty_process(
                command=command,
                ssh_context=ssh_context,
                env=env,
            )
            return _PosixPtyTransport(
                proc=proc[0],
                master_fd=proc[1],
                cleanup_root=cleanup_root,
            )
        if _windows_tty_supported():
            return self._spawn_ssh_windows_conpty_transport(
                command=command,
                ssh_context=ssh_context,
                env=env,
            )
        raise ValueError(_tty_unsupported_message())

    def _spawn_ssh_windows_conpty_transport(
        self,
        *,
        command: str,
        ssh_context: tuple[WorkspaceMountRecord, str],
        env: dict[str, str] | None,
    ) -> _WindowsConPtyTransport:
        if self._ssh_profile_service is None:
            raise ValueError("SSH workspace command execution requires ssh profiles")
        mount, remote_cwd = ssh_context
        provider_config = mount.provider_config
        if not isinstance(provider_config, WorkspaceSshMountConfig):
            raise ValueError(
                f"Workspace ssh mount is missing ssh config: {mount.mount_name}"
            )
        prepared = self._ssh_profile_service.prepare_remote_command(
            ssh_profile_id=provider_config.ssh_profile_id,
            command=command,
            cwd=remote_cwd,
            env=env,
            tty=True,
        )
        try:
            process = _spawn_windows_pty_argv_process(
                argv=prepared.argv,
                cwd=Path.cwd(),
                env=prepared.env,
                columns=_DEFAULT_PTY_COLUMNS,
                rows=_DEFAULT_PTY_ROWS,
            )
        except Exception:
            shutil.rmtree(prepared.temp_root, ignore_errors=True)
            raise
        return _WindowsConPtyTransport(
            process,
            blocking_executor=self._blocking_executor,
            pty_executor=self._pty_executor,
            cleanup_root=prepared.temp_root,
        )

    async def _create_ssh_pty_process(
        self,
        *,
        command: str,
        ssh_context: tuple[WorkspaceMountRecord, str],
        env: dict[str, str] | None,
    ) -> tuple[tuple[asyncio.subprocess.Process, int], Path]:
        if self._ssh_profile_service is None:
            raise ValueError("SSH workspace command execution requires ssh profiles")
        mount, remote_cwd = ssh_context
        provider_config = mount.provider_config
        if not isinstance(provider_config, WorkspaceSshMountConfig):
            raise ValueError(
                f"Workspace ssh mount is missing ssh config: {mount.mount_name}"
            )
        prepared = self._ssh_profile_service.prepare_remote_command(
            ssh_profile_id=provider_config.ssh_profile_id,
            command=command,
            cwd=remote_cwd,
            env=env,
            tty=True,
        )
        assert pty is not None
        master_fd, slave_fd = pty.openpty()
        _set_terminal_size(
            master_fd,
            columns=_DEFAULT_PTY_COLUMNS,
            rows=_DEFAULT_PTY_ROWS,
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *prepared.argv,
                env=prepared.env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=_start_new_session(),
            )
        except Exception:
            shutil.rmtree(prepared.temp_root, ignore_errors=True)
            with contextlib.suppress(OSError):
                os.close(master_fd)
            with contextlib.suppress(OSError):
                os.close(slave_fd)
            raise
        with contextlib.suppress(OSError):
            os.close(slave_fd)
        return (proc, master_fd), prepared.temp_root

    async def _spawn_tty_transport(
        self,
        *,
        command: str,
        cwd: Path,
        env: dict[str, str] | None,
    ) -> _BackgroundTaskTransport:
        if _posix_pty_supported():
            return await self._spawn_posix_pty_transport(
                command=command,
                cwd=cwd,
                env=env,
            )
        if _windows_tty_supported():
            return await self._spawn_windows_conpty_transport(
                command=command,
                cwd=cwd,
                env=env,
            )
        raise ValueError(_tty_unsupported_message())

    async def _spawn_posix_pty_transport(
        self,
        *,
        command: str,
        cwd: Path,
        env: dict[str, str] | None,
    ) -> _PosixPtyTransport:
        assert pty is not None
        runtime = resolve_command_runtime(command=command)
        command_env = await build_command_env(
            env,
            runtime=runtime,
            command=command,
        )
        master_fd, slave_fd = pty.openpty()
        _set_terminal_size(
            master_fd,
            columns=_DEFAULT_PTY_COLUMNS,
            rows=_DEFAULT_PTY_ROWS,
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *build_command_argv(runtime=runtime, command=command),
                cwd=str(cwd),
                env=command_env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=_start_new_session(),
            )
        except Exception:
            with contextlib.suppress(OSError):
                os.close(master_fd)
            with contextlib.suppress(OSError):
                os.close(slave_fd)
            raise
        with contextlib.suppress(OSError):
            os.close(slave_fd)
        return _PosixPtyTransport(proc=proc, master_fd=master_fd)

    async def _spawn_windows_conpty_transport(
        self,
        *,
        command: str,
        cwd: Path,
        env: dict[str, str] | None,
    ) -> _WindowsConPtyTransport:
        runtime = resolve_command_runtime(command=command)
        command_env = await build_command_env(
            env,
            runtime=runtime,
            command=command,
        )
        process = _spawn_windows_pty_process(
            command=command,
            cwd=cwd,
            env=command_env,
            runtime=runtime,
            columns=_DEFAULT_PTY_COLUMNS,
            rows=_DEFAULT_PTY_ROWS,
        )
        return _WindowsConPtyTransport(
            process,
            blocking_executor=self._blocking_executor,
            pty_executor=self._pty_executor,
        )

    async def _pump_stream(
        self,
        stream_name: str,
        stream: asyncio.StreamReader,
        queue: asyncio.Queue[tuple[str, str] | None],
    ) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                chunk = await stream.read(MAX_DELTA_BYTES)
                if not chunk:
                    break
                text = decoder.decode(chunk, final=False)
                if text:
                    await queue.put((stream_name, text))
            final_text = decoder.decode(b"", final=True)
            if final_text:
                await queue.put((stream_name, final_text))
        finally:
            await queue.put(None)

    async def _pump_master_fd(self, runtime: _BackgroundTaskRuntime, fd: int) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                try:
                    chunk = await self._run_pty_blocking(self._read_master_fd, fd)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                text = decoder.decode(chunk, final=False)
                if text:
                    await runtime.queue.put(("stdout", text))
            final_text = decoder.decode(b"", final=True)
            if final_text:
                await runtime.queue.put(("stdout", final_text))
        finally:
            await runtime.queue.put(None)

    async def _pump_windows_pty_process(
        self,
        runtime: _BackgroundTaskRuntime,
        proc: _WindowsPtyProcessProtocol,
    ) -> None:
        try:
            while True:
                try:
                    chunk = await self._run_pty_blocking(proc.read, MAX_DELTA_BYTES)
                except EOFError:
                    break
                if not chunk:
                    if not proc.isalive():
                        break
                    await asyncio.sleep(0.05)
                    continue
                await runtime.queue.put(("stdout", chunk))
        finally:
            await runtime.queue.put(None)

    @staticmethod
    def _read_master_fd(fd: int) -> bytes:
        return os.read(fd, MAX_DELTA_BYTES)

    async def _supervise(self, runtime: _BackgroundTaskRuntime) -> None:
        record = runtime.record
        timeout_ms = record.timeout_ms or DEFAULT_BACKGROUND_TASK_TIMEOUT_MS
        timeout_seconds = max(0.001, timeout_ms / 1000.0)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        stream_eof = 0
        timed_out = False
        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    timed_out = True
                    break
                if stream_eof >= runtime.stream_count:
                    try:
                        await asyncio.wait_for(
                            runtime.transport.wait(), timeout=remaining
                        )
                    except asyncio.TimeoutError:
                        timed_out = True
                    break
                try:
                    item = await asyncio.wait_for(
                        runtime.queue.get(), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    timed_out = True
                    break
                if item is None:
                    stream_eof += 1
                    continue
                stream_name, chunk = item
                await self._handle_output_chunk(
                    runtime,
                    stream_name=stream_name,
                    chunk=chunk,
                )
        finally:
            if timed_out and runtime.transport.returncode is None:
                await runtime.transport.terminate()
            for task in runtime.pump_tasks:
                if not task.done():
                    task.cancel()
            with contextlib.suppress(Exception):
                await asyncio.gather(*runtime.pump_tasks, return_exceptions=True)
            await self._finalize_runtime(runtime, timed_out=timed_out)

    async def _handle_output_chunk(
        self,
        runtime: _BackgroundTaskRuntime,
        *,
        stream_name: str,
        chunk: str,
    ) -> None:
        await self._run_blocking(
            self._append_log,
            runtime.log_file_path,
            stream_name=stream_name,
            chunk=chunk,
        )
        runtime.recent_output.feed(chunk)
        runtime.output_buffer.append(chunk)
        runtime.record = await self._repository.upsert_async(
            runtime.record.model_copy(
                update={
                    "recent_output": runtime.recent_output.snapshot(),
                    "output_excerpt": runtime.output_buffer.render(),
                    "updated_at": datetime.now(tz=timezone.utc),
                }
            )
        )
        await self._emit_monitor_lines_async(
            runtime,
            stream_name=stream_name,
            chunk=chunk,
        )
        await self._mark_runtime_changed(runtime)
        await self._publish_background_task_event_async(
            event_type=RunEventType.BACKGROUND_TASK_UPDATED,
            record=runtime.record,
            payload=self._build_background_task_update_payload(
                record=runtime.record,
                stream_name=stream_name,
                chunk=chunk,
            ),
        )

    async def _finalize_runtime(
        self,
        runtime: _BackgroundTaskRuntime,
        *,
        timed_out: bool,
        wait_for_exit: bool = True,
    ) -> None:
        async with runtime.finalize_lock:
            if runtime.completed.is_set():
                return
            runtime.recent_output.finalize()
            await self._emit_monitor_final_lines_async(runtime)
            exit_code = runtime.transport.returncode
            if exit_code is None and not timed_out and wait_for_exit:
                exit_code = await runtime.transport.wait()
            if timed_out:
                exit_code = 124
            status = self._resolve_background_task_status(
                runtime=runtime,
                exit_code=exit_code,
                timed_out=timed_out,
            )
            completed_at = datetime.now(tz=timezone.utc)
            runtime.record = await self._repository.upsert_async(
                runtime.record.model_copy(
                    update={
                        "status": status,
                        "exit_code": exit_code,
                        "pid": None,
                        "recent_output": runtime.recent_output.snapshot(),
                        "output_excerpt": runtime.output_buffer.render(),
                        "updated_at": completed_at,
                        "completed_at": completed_at,
                    }
                )
            )
            runtime_id = runtime.record.background_task_id
            self._runtimes.pop(runtime_id, None)
            runtime.completed.set()
            completion_error: Exception | None = None
            try:
                await self._mark_runtime_changed(runtime)
                await self._publish_background_task_event_async(
                    event_type=(
                        RunEventType.BACKGROUND_TASK_STOPPED
                        if status == BackgroundTaskStatus.STOPPED
                        else RunEventType.BACKGROUND_TASK_COMPLETED
                    ),
                    record=runtime.record,
                )
                await self._emit_monitor_state_event_async(
                    record=runtime.record,
                    event_name=_background_task_state_event_name(status),
                )
            except Exception as exc:
                completion_error = exc
            try:
                await runtime.transport.close()
            except Exception:
                LOGGER.warning(
                    "Failed to close background task transport",
                    extra={"background_task_id": runtime.record.background_task_id},
                    exc_info=True,
                )
            if completion_error is not None:
                raise completion_error
            if self._completion_listener is not None:
                await self._completion_listener(runtime.record)

    async def _signal_runtime_stop(self, runtime: _BackgroundTaskRuntime) -> None:
        for _ in range(runtime.stream_count):
            await runtime.queue.put(None)
        await self._mark_runtime_changed(runtime)

    def _resolve_background_task_status(
        self,
        *,
        runtime: _BackgroundTaskRuntime,
        exit_code: int | None,
        timed_out: bool,
    ) -> BackgroundTaskStatus:
        if runtime.stop_requested:
            return BackgroundTaskStatus.STOPPED
        if timed_out:
            return BackgroundTaskStatus.FAILED
        if exit_code == 0:
            return BackgroundTaskStatus.COMPLETED
        return BackgroundTaskStatus.FAILED

    async def _write_chars(self, runtime: _BackgroundTaskRuntime, chars: str) -> None:
        await runtime.transport.write(chars)

    async def _rollback_runtime(self, runtime: _BackgroundTaskRuntime) -> None:
        with contextlib.suppress(Exception):
            if runtime.transport.returncode is None:
                await runtime.transport.terminate()
        for task in runtime.pump_tasks:
            if not task.done():
                task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*runtime.pump_tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await runtime.transport.close()

    async def _wait_for_runtime_change(
        self,
        *,
        runtime: _BackgroundTaskRuntime,
        before_version: int,
        timeout_ms: int,
    ) -> bool:
        timeout_seconds = max(0.001, timeout_ms / 1000.0)
        async with runtime.change_condition:
            if runtime.change_version > before_version or runtime.completed.is_set():
                return True
            try:
                await asyncio.wait_for(
                    runtime.change_condition.wait_for(
                        lambda: (
                            runtime.change_version > before_version
                            or runtime.completed.is_set()
                        )
                    ),
                    timeout=timeout_seconds,
                )
                return True
            except asyncio.TimeoutError:
                return False

    async def _mark_runtime_changed(self, runtime: _BackgroundTaskRuntime) -> None:
        async with runtime.change_condition:
            runtime.change_version += 1
            runtime.change_condition.notify_all()

    async def _prune_sessions_if_needed(self) -> None:
        records = list(await self._repository.list_all_async())
        if len(records) < MAX_BACKGROUND_TASKS:
            return
        required_slots = len(records) - MAX_BACKGROUND_TASKS + 1
        protected = {
            record.background_task_id
            for record in sorted(
                records, key=lambda item: item.updated_at, reverse=True
            )[:PROTECTED_RECENT_BACKGROUND_TASKS]
        }
        reclaimable = [
            record
            for record in sorted(records, key=lambda item: item.updated_at)
            if record.background_task_id not in protected and not record.is_active
        ]
        for record in reclaimable[:required_slots]:
            await self._repository.delete_async(record.background_task_id)
        required_slots -= min(required_slots, len(reclaimable))
        if required_slots <= 0:
            return
        active_candidates = [
            record
            for record in sorted(records, key=lambda item: item.updated_at)
            if record.background_task_id not in protected and record.is_active
        ]
        for record in active_candidates[:required_slots]:
            stopped_record: BackgroundTaskRecord | None = None
            with contextlib.suppress(KeyError):
                stopped_record = await self.stop_for_run(
                    run_id=record.run_id,
                    background_task_id=record.background_task_id,
                    reason="lru_pruned",
                )
            if stopped_record is None or stopped_record.is_active:
                continue
            await self._repository.delete_async(record.background_task_id)

    async def _publish_background_task_event_async(
        self,
        *,
        event_type: RunEventType,
        record: BackgroundTaskRecord,
        payload: dict[str, JsonValue] | None = None,
    ) -> None:
        await publish_run_event_async(
            self._run_event_hub,
            RunEvent(
                session_id=record.session_id,
                run_id=record.run_id,
                trace_id=record.run_id,
                task_id=None,
                instance_id=record.instance_id,
                role_id=record.role_id,
                event_type=event_type,
                payload_json=json.dumps(
                    (record.model_dump(mode="json") if payload is None else payload),
                    ensure_ascii=False,
                ),
            ),
        )
        with contextlib.suppress(Exception):
            log_event(
                LOGGER,
                logging.INFO,
                event=f"background_task.{event_type.value}",
                message="Background task state updated",
                payload={
                    "background_task_id": record.background_task_id,
                    "run_id": record.run_id,
                    "status": record.status.value,
                },
            )

    def _build_background_task_update_payload(
        self,
        *,
        record: BackgroundTaskRecord,
        stream_name: str,
        chunk: str,
    ) -> dict[str, JsonValue]:
        payload = record.model_dump(mode="json", exclude={"output_excerpt"})
        payload["stream_name"] = stream_name
        payload["delta"] = chunk
        return payload

    async def _emit_monitor_lines_async(
        self,
        runtime: _BackgroundTaskRuntime,
        *,
        stream_name: str,
        chunk: str,
    ) -> None:
        if self._monitor_service is None:
            return
        buffer = runtime.monitor_line_buffers.setdefault(
            stream_name,
            _MonitorOutputLineBuffer(),
        )
        for line in buffer.feed(chunk):
            runtime.monitor_line_sequence += 1
            await self._emit_monitor_event_async(
                record=runtime.record,
                event_name="background_task.line",
                body_text=line,
                attributes={
                    "stream_name": stream_name,
                    "status": runtime.record.status.value,
                    "execution_mode": runtime.record.execution_mode,
                },
                dedupe_key=(
                    f"{runtime.record.background_task_id}:line:{runtime.monitor_line_sequence}"
                ),
            )

    async def _emit_monitor_final_lines_async(
        self, runtime: _BackgroundTaskRuntime
    ) -> None:
        if self._monitor_service is None:
            return
        for stream_name, buffer in runtime.monitor_line_buffers.items():
            for line in buffer.finalize():
                runtime.monitor_line_sequence += 1
                await self._emit_monitor_event_async(
                    record=runtime.record,
                    event_name="background_task.line",
                    body_text=line,
                    attributes={
                        "stream_name": stream_name,
                        "status": runtime.record.status.value,
                        "execution_mode": runtime.record.execution_mode,
                    },
                    dedupe_key=(
                        f"{runtime.record.background_task_id}:line:{runtime.monitor_line_sequence}"
                    ),
                )

    async def _emit_monitor_state_event_async(
        self,
        *,
        record: BackgroundTaskRecord,
        event_name: str,
    ) -> None:
        await self._emit_monitor_event_async(
            record=record,
            event_name=event_name,
            body_text=record.output_excerpt or record.command,
            attributes={
                "status": record.status.value,
                "execution_mode": record.execution_mode,
                "exit_code": "" if record.exit_code is None else str(record.exit_code),
            },
            dedupe_key=(
                f"{record.background_task_id}:state:{event_name}:{record.updated_at.isoformat()}"
            ),
        )

    async def _emit_monitor_event_async(
        self,
        *,
        record: BackgroundTaskRecord,
        event_name: str,
        body_text: str,
        attributes: dict[str, str],
        dedupe_key: str,
    ) -> None:
        if self._monitor_service is None:
            return
        normalized_attributes: dict[str, str] = {
            "background_task_id": record.background_task_id,
            "run_id": record.run_id,
            "session_id": record.session_id,
            "command": record.command,
            "cwd": record.cwd,
        }
        for key, value in attributes.items():
            normalized_value = value.strip()
            if normalized_value:
                normalized_attributes[key] = normalized_value
        if record.instance_id is not None and record.instance_id.strip():
            normalized_attributes["instance_id"] = record.instance_id
        if record.role_id is not None and record.role_id.strip():
            normalized_attributes["role_id"] = record.role_id
        raw_payload = {
            "event_name": event_name,
            "body_text": body_text,
            "attributes": normalized_attributes,
        }
        await self._monitor_service.emit_async(
            MonitorEventEnvelope(
                source_kind=MonitorSourceKind.BACKGROUND_TASK,
                source_key=record.background_task_id,
                event_name=event_name,
                run_id=record.run_id,
                session_id=record.session_id,
                body_text=body_text,
                attributes=normalized_attributes,
                dedupe_key=dedupe_key,
                raw_payload_json=json.dumps(raw_payload, ensure_ascii=False),
            )
        )

    def _get_record(self, background_task_id: str) -> BackgroundTaskRecord:
        record = self._repository.get(background_task_id)
        if record is None:
            raise KeyError(f"Unknown background task: {background_task_id}")
        return record

    async def _get_record_async(self, background_task_id: str) -> BackgroundTaskRecord:
        record = await self._repository.get_async(background_task_id)
        if record is None:
            raise KeyError(f"Unknown background task: {background_task_id}")
        return record

    @staticmethod
    def _append_log(log_path: Path, *, stream_name: str, chunk: str) -> None:
        prefix = "" if stream_name == "stdout" else "[stderr] "
        with log_path.open("a", encoding="utf-8") as handle:
            if prefix:
                handle.write(prefix)
            handle.write(chunk)


def _normalize_poll_timeout(
    *,
    chars: str,
    yield_time_ms: int | None,
    is_initial_poll: bool,
) -> int:
    if yield_time_ms is not None and yield_time_ms < 1:
        raise ValueError("yield_time_ms must be >= 1")
    if is_initial_poll:
        if yield_time_ms is None:
            return MIN_EXEC_COMMAND_YIELD_MS
        return max(MIN_EXEC_COMMAND_YIELD_MS, yield_time_ms)
    if chars:
        if yield_time_ms is None:
            return MAX_WRITE_WAIT_MS
        return min(MAX_WRITE_WAIT_MS, yield_time_ms)
    if yield_time_ms is None:
        return MIN_EMPTY_POLL_YIELD_MS
    return min(MAX_BACKGROUND_POLL_MS, max(MIN_EMPTY_POLL_YIELD_MS, yield_time_ms))


def _background_task_state_event_name(status: BackgroundTaskStatus) -> str:
    if status == BackgroundTaskStatus.COMPLETED:
        return "background_task.completed"
    if status == BackgroundTaskStatus.STOPPED:
        return "background_task.stopped"
    if status == BackgroundTaskStatus.FAILED:
        return "background_task.failed"
    return "background_task.updated"


def _set_terminal_size(fd: int, *, columns: int, rows: int) -> None:
    if not _posix_pty_supported():
        raise ValueError("TTY background tasks are not supported on this host")
    assert fcntl is not None
    assert termios is not None
    winsz = struct.pack("HHHH", rows, columns, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsz)


def _posix_pty_supported() -> bool:
    return (
        os.name != "nt"
        and fcntl is not None
        and pty is not None
        and termios is not None
    )


def _windows_tty_supported() -> bool:
    return windows_conpty_supported()


def _tty_unsupported_message() -> str:
    if os.name == "nt":
        return "TTY background tasks are not supported on this Windows host"
    return "TTY background tasks are only supported on POSIX hosts"


def _spawn_windows_pty_process(
    *,
    command: str,
    cwd: Path,
    env: dict[str, str],
    runtime: ResolvedCommandRuntime,
    columns: int,
    rows: int,
) -> _WindowsPtyProcessProtocol:
    if not _windows_tty_supported():
        raise ValueError(_tty_unsupported_message())
    argv = list(build_command_argv(runtime=runtime, command=command))
    return _spawn_windows_pty_argv_process(
        argv=tuple(argv),
        cwd=cwd,
        env=env,
        columns=columns,
        rows=rows,
    )


def _spawn_windows_pty_argv_process(
    *,
    argv: tuple[str, ...],
    cwd: Path,
    env: dict[str, str],
    columns: int,
    rows: int,
) -> _WindowsPtyProcessProtocol:
    if not _windows_tty_supported():
        raise ValueError(_tty_unsupported_message())
    module = importlib.import_module("winpty")
    factory = cast(
        _WindowsPtyProcessFactoryProtocol,
        module.__dict__["PtyProcess"],
    )
    return factory.spawn(
        list(argv),
        cwd=str(cwd),
        env=env,
        dimensions=(rows, columns),
    )
