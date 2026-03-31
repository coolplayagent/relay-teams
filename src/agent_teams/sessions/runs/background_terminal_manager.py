# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections import deque
import contextlib
from datetime import datetime, timezone
import errno
import json
import logging
import os
from pathlib import Path
import struct
from uuid import uuid4

try:
    import fcntl
    import pty
    import termios
except ImportError:
    fcntl = None
    pty = None
    termios = None

from agent_teams.logger import get_logger, log_event
from agent_teams.sessions.runs.background_terminal_models import (
    BackgroundTerminalRecord,
    BackgroundTerminalStatus,
)
from agent_teams.sessions.runs.background_terminal_repo import (
    BackgroundTerminalRepository,
)
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.run_models import RunEvent
from agent_teams.tools.workspace_tools.shell_executor import (
    _creation_flags,
    _kill_process_tree,
    _start_new_session,
    build_shell_env,
    create_shell_subprocess,
    resolve_bash_path,
)
from agent_teams.workspace import WorkspaceHandle

LOGGER = get_logger(__name__)
DEFAULT_BACKGROUND_TERMINAL_TIMEOUT_MS = 30 * 60 * 1000
MAX_BACKGROUND_TERMINALS_PER_RUN = 4
MAX_BACKGROUND_TAIL_LINES = 3
_DEFAULT_PTY_COLUMNS = 120
_DEFAULT_PTY_ROWS = 40


class _TailBuffer:
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


class _TerminalRuntime:
    def __init__(
        self,
        *,
        record: BackgroundTerminalRecord,
        proc: asyncio.subprocess.Process,
        log_file_path: Path,
        tty: bool,
        queue: asyncio.Queue[tuple[str, str] | None],
        stream_count: int,
        master_fd: int | None = None,
        slave_fd: int | None = None,
    ) -> None:
        self.record = record
        self.proc = proc
        self.log_file_path = log_file_path
        self.tty = tty
        self.queue = queue
        self.stream_count = stream_count
        self.master_fd = master_fd
        self.slave_fd = slave_fd
        self.stdout_tail = _TailBuffer(max_lines=MAX_BACKGROUND_TAIL_LINES)
        self.stderr_tail = _TailBuffer(max_lines=MAX_BACKGROUND_TAIL_LINES)
        self.recent_output = _TailBuffer(max_lines=MAX_BACKGROUND_TAIL_LINES)
        self.pump_tasks: list[asyncio.Task[None]] = []
        self.supervisor_task: asyncio.Task[None] | None = None
        self.completed = asyncio.Event()
        self.stop_requested = False
        self.closed = False


class BackgroundTerminalManager:
    def __init__(
        self,
        *,
        repository: BackgroundTerminalRepository,
        run_event_hub: RunEventHub,
    ) -> None:
        self._repository = repository
        self._run_event_hub = run_event_hub
        self._runtimes: dict[str, _TerminalRuntime] = {}

    async def start_terminal(
        self,
        *,
        run_id: str,
        session_id: str,
        instance_id: str,
        role_id: str,
        tool_call_id: str,
        workspace: WorkspaceHandle,
        command: str,
        cwd: Path,
        timeout_ms: int | None,
        env: dict[str, str] | None,
        tty: bool,
    ) -> BackgroundTerminalRecord:
        active_count = sum(
            1 for record in self._repository.list_by_run(run_id) if record.is_active
        )
        if active_count >= MAX_BACKGROUND_TERMINALS_PER_RUN:
            raise ValueError(
                "Too many active background terminals for this run. "
                f"Maximum is {MAX_BACKGROUND_TERMINALS_PER_RUN}."
            )
        effective_timeout_ms = timeout_ms or DEFAULT_BACKGROUND_TERMINAL_TIMEOUT_MS
        terminal_id = f"term_{uuid4().hex[:12]}"
        log_dir = workspace.resolve_tmp_path("background_terminals", write=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file_path = log_dir / f"{terminal_id}.log"
        log_file_path.touch(exist_ok=True)
        logical_log_path = workspace.logical_tmp_path(log_file_path)
        record = BackgroundTerminalRecord(
            terminal_id=terminal_id,
            run_id=run_id,
            session_id=session_id,
            instance_id=instance_id,
            role_id=role_id,
            tool_call_id=tool_call_id,
            command=command,
            cwd=str(cwd),
            tty=tty,
            timeout_ms=effective_timeout_ms,
            log_path=logical_log_path,
        )
        runtime = await self._spawn_runtime(
            record=record,
            cwd=cwd,
            env=env,
            log_file_path=log_file_path,
        )
        self._runtimes[terminal_id] = runtime
        persisted = self._repository.upsert(record)
        self._publish_terminal_event(
            event_type=RunEventType.BACKGROUND_TERMINAL_STARTED,
            record=persisted,
        )
        runtime.supervisor_task = asyncio.create_task(self._supervise(runtime))
        return persisted

    def list_for_run(self, run_id: str) -> tuple[BackgroundTerminalRecord, ...]:
        return self._repository.list_by_run(run_id)

    def get_for_run(self, *, run_id: str, terminal_id: str) -> BackgroundTerminalRecord:
        record = self._get_record(terminal_id)
        if record.run_id != run_id:
            raise KeyError(
                f"Background terminal {terminal_id} does not belong to run {run_id}"
            )
        return record

    async def wait_for_run(
        self,
        *,
        run_id: str,
        terminal_id: str,
        wait_ms: int,
    ) -> tuple[BackgroundTerminalRecord, bool]:
        record = self.get_for_run(run_id=run_id, terminal_id=terminal_id)
        runtime = self._runtimes.get(terminal_id)
        if runtime is None or not record.is_active:
            return record, True
        if wait_ms < 1:
            raise ValueError("wait_ms must be >= 1")
        try:
            await asyncio.wait_for(runtime.completed.wait(), timeout=wait_ms / 1000.0)
            return self._get_record(terminal_id), True
        except asyncio.TimeoutError:
            return self._get_record(terminal_id), False

    async def write_for_run(
        self,
        *,
        run_id: str,
        terminal_id: str,
        chars: str,
    ) -> BackgroundTerminalRecord:
        record = self.get_for_run(run_id=run_id, terminal_id=terminal_id)
        runtime = self._runtimes.get(terminal_id)
        if runtime is None or not record.is_active:
            return record
        if runtime.tty:
            if runtime.master_fd is None:
                raise RuntimeError("TTY background terminal is not writable")
            if chars:
                os.write(runtime.master_fd, chars.encode("utf-8", errors="replace"))
            return self._get_record(terminal_id)
        writer = runtime.proc.stdin
        if writer is None:
            raise RuntimeError("Background terminal stdin is not available")
        if chars:
            writer.write(chars.encode("utf-8", errors="replace"))
            await writer.drain()
        return self._get_record(terminal_id)

    async def resize_for_run(
        self,
        *,
        run_id: str,
        terminal_id: str,
        columns: int,
        rows: int,
    ) -> BackgroundTerminalRecord:
        if columns < 1 or rows < 1:
            raise ValueError("columns and rows must both be >= 1")
        record = self.get_for_run(run_id=run_id, terminal_id=terminal_id)
        runtime = self._runtimes.get(terminal_id)
        if runtime is None or not record.is_active:
            return record
        if not runtime.tty or runtime.master_fd is None:
            raise ValueError(
                "Terminal resize is only supported for active TTY background terminals"
            )
        _set_terminal_size(runtime.master_fd, columns=columns, rows=rows)
        return self._get_record(terminal_id)

    async def stop_for_run(
        self,
        *,
        run_id: str,
        terminal_id: str,
        reason: str = "stopped_by_user",
    ) -> BackgroundTerminalRecord:
        record = self.get_for_run(run_id=run_id, terminal_id=terminal_id)
        runtime = self._runtimes.get(terminal_id)
        if runtime is None or not record.is_active:
            return record
        runtime.stop_requested = True
        if runtime.proc.returncode is None:
            await _kill_process_tree(runtime.proc)
        await runtime.completed.wait()
        return self._get_record(terminal_id)

    async def stop_all_for_run(self, *, run_id: str, reason: str) -> None:
        active_ids = [
            record.terminal_id
            for record in self._repository.list_by_run(run_id)
            if record.is_active
        ]
        for terminal_id in active_ids:
            with contextlib.suppress(KeyError):
                _ = await self.stop_for_run(
                    run_id=run_id,
                    terminal_id=terminal_id,
                    reason=reason,
                )

    async def close(self) -> None:
        active_ids = list(self._runtimes.keys())
        for terminal_id in active_ids:
            record = self._repository.get(terminal_id)
            if record is None:
                continue
            with contextlib.suppress(KeyError):
                _ = await self.stop_for_run(
                    run_id=record.run_id,
                    terminal_id=terminal_id,
                    reason="server_shutdown",
                )

    async def _spawn_runtime(
        self,
        *,
        record: BackgroundTerminalRecord,
        cwd: Path,
        env: dict[str, str] | None,
        log_file_path: Path,
    ) -> _TerminalRuntime:
        queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        if record.tty:
            if not _pty_supported():
                raise ValueError(
                    "TTY background terminals are only supported on POSIX hosts"
                )
            assert pty is not None
            master_fd, slave_fd = pty.openpty()
            _set_terminal_size(
                master_fd,
                columns=_DEFAULT_PTY_COLUMNS,
                rows=_DEFAULT_PTY_ROWS,
            )
            shell_env = await build_shell_env(env)
            proc = await asyncio.create_subprocess_exec(
                resolve_bash_path(),
                "-lc",
                record.command,
                cwd=str(cwd),
                env=shell_env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=_start_new_session(),
                creationflags=_creation_flags(),
            )
            runtime = _TerminalRuntime(
                record=record,
                proc=proc,
                log_file_path=log_file_path,
                tty=True,
                queue=queue,
                stream_count=1,
                master_fd=master_fd,
                slave_fd=slave_fd,
            )
            runtime.pump_tasks.append(
                asyncio.create_task(self._pump_master_fd(runtime))
            )
            return runtime
        proc = await create_shell_subprocess(
            command=record.command,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        runtime = _TerminalRuntime(
            record=record,
            proc=proc,
            log_file_path=log_file_path,
            tty=False,
            queue=queue,
            stream_count=2,
        )
        stdout = proc.stdout
        stderr = proc.stderr
        if stdout is None or stderr is None:
            raise RuntimeError("Failed to capture background terminal streams")
        runtime.pump_tasks.append(
            asyncio.create_task(self._pump_stream("stdout", stdout, queue))
        )
        runtime.pump_tasks.append(
            asyncio.create_task(self._pump_stream("stderr", stderr, queue))
        )
        return runtime

    async def _pump_stream(
        self,
        stream_name: str,
        stream: asyncio.StreamReader,
        queue: asyncio.Queue[tuple[str, str] | None],
    ) -> None:
        try:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                await queue.put((stream_name, chunk.decode("utf-8", errors="replace")))
        finally:
            await queue.put(None)

    async def _pump_master_fd(self, runtime: _TerminalRuntime) -> None:
        if runtime.master_fd is None:
            await runtime.queue.put(None)
            return
        loop = asyncio.get_running_loop()
        try:
            while True:
                try:
                    chunk = await loop.run_in_executor(
                        None,
                        self._read_master_fd,
                        runtime.master_fd,
                    )
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                await runtime.queue.put(
                    ("stdout", chunk.decode("utf-8", errors="replace"))
                )
        finally:
            await runtime.queue.put(None)

    @staticmethod
    def _read_master_fd(fd: int) -> bytes:
        return os.read(fd, 4096)

    async def _supervise(self, runtime: _TerminalRuntime) -> None:
        record = runtime.record
        timeout_ms = record.timeout_ms or DEFAULT_BACKGROUND_TERMINAL_TIMEOUT_MS
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
                        await asyncio.wait_for(runtime.proc.wait(), timeout=remaining)
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
                    runtime, stream_name=stream_name, chunk=chunk
                )
        finally:
            if timed_out and runtime.proc.returncode is None:
                await _kill_process_tree(runtime.proc)
            for task in runtime.pump_tasks:
                if not task.done():
                    task.cancel()
            with contextlib.suppress(Exception):
                await asyncio.gather(*runtime.pump_tasks, return_exceptions=True)
            await self._finalize_runtime(runtime, timed_out=timed_out)

    async def _handle_output_chunk(
        self,
        runtime: _TerminalRuntime,
        *,
        stream_name: str,
        chunk: str,
    ) -> None:
        self._append_log(runtime.log_file_path, stream_name=stream_name, chunk=chunk)
        runtime.recent_output.feed(chunk)
        if stream_name == "stderr":
            runtime.stderr_tail.feed(chunk)
        else:
            runtime.stdout_tail.feed(chunk)
        updated = runtime.record.model_copy(
            update={
                "recent_output": runtime.recent_output.snapshot(),
                "stdout_tail": runtime.stdout_tail.snapshot(),
                "stderr_tail": runtime.stderr_tail.snapshot(),
                "updated_at": datetime.now(tz=timezone.utc),
            }
        )
        runtime.record = self._repository.upsert(updated)
        self._publish_terminal_event(
            event_type=RunEventType.BACKGROUND_TERMINAL_UPDATED,
            record=runtime.record,
        )

    async def _finalize_runtime(
        self,
        runtime: _TerminalRuntime,
        *,
        timed_out: bool,
    ) -> None:
        runtime.stdout_tail.finalize()
        runtime.stderr_tail.finalize()
        runtime.recent_output.finalize()
        exit_code = runtime.proc.returncode
        if timed_out:
            exit_code = 124
        status = self._resolve_terminal_status(
            runtime=runtime,
            exit_code=exit_code,
            timed_out=timed_out,
        )
        completed_at = datetime.now(tz=timezone.utc)
        runtime.record = self._repository.upsert(
            runtime.record.model_copy(
                update={
                    "status": status,
                    "exit_code": exit_code,
                    "recent_output": runtime.recent_output.snapshot(),
                    "stdout_tail": runtime.stdout_tail.snapshot(),
                    "stderr_tail": runtime.stderr_tail.snapshot(),
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                }
            )
        )
        self._runtimes.pop(runtime.record.terminal_id, None)
        if runtime.proc.stdin is not None:
            runtime.proc.stdin.close()
        if runtime.master_fd is not None:
            with contextlib.suppress(OSError):
                os.close(runtime.master_fd)
        if runtime.slave_fd is not None:
            with contextlib.suppress(OSError):
                os.close(runtime.slave_fd)
        runtime.closed = True
        runtime.completed.set()
        self._publish_terminal_event(
            event_type=(
                RunEventType.BACKGROUND_TERMINAL_STOPPED
                if status == BackgroundTerminalStatus.STOPPED
                else RunEventType.BACKGROUND_TERMINAL_COMPLETED
            ),
            record=runtime.record,
        )

    def _resolve_terminal_status(
        self,
        *,
        runtime: _TerminalRuntime,
        exit_code: int | None,
        timed_out: bool,
    ) -> BackgroundTerminalStatus:
        if runtime.stop_requested:
            return BackgroundTerminalStatus.STOPPED
        if timed_out:
            return BackgroundTerminalStatus.FAILED
        if exit_code == 0:
            return BackgroundTerminalStatus.COMPLETED
        return BackgroundTerminalStatus.FAILED

    def _publish_terminal_event(
        self,
        *,
        event_type: RunEventType,
        record: BackgroundTerminalRecord,
    ) -> None:
        self._run_event_hub.publish(
            RunEvent(
                session_id=record.session_id,
                run_id=record.run_id,
                trace_id=record.run_id,
                task_id=None,
                instance_id=record.instance_id,
                role_id=record.role_id,
                event_type=event_type,
                payload_json=json.dumps(
                    record.model_dump(mode="json"), ensure_ascii=False
                ),
            )
        )
        with contextlib.suppress(Exception):
            log_event(
                LOGGER,
                logging.INFO,
                event=f"background_terminal.{event_type.value}",
                message="Background terminal state updated",
                payload={
                    "terminal_id": record.terminal_id,
                    "run_id": record.run_id,
                    "status": record.status.value,
                },
            )

    def _get_record(self, terminal_id: str) -> BackgroundTerminalRecord:
        record = self._repository.get(terminal_id)
        if record is None:
            raise KeyError(f"Unknown background terminal: {terminal_id}")
        return record

    @staticmethod
    def _append_log(logical_log_path: Path, *, stream_name: str, chunk: str) -> None:
        prefix = "" if stream_name == "stdout" else "[stderr] "
        with logical_log_path.open("a", encoding="utf-8") as handle:
            if prefix:
                handle.write(prefix)
            handle.write(chunk)


def _set_terminal_size(fd: int, *, columns: int, rows: int) -> None:
    if not _pty_supported():
        raise ValueError("TTY background terminals are not supported on this host")
    assert fcntl is not None
    assert termios is not None
    winsz = struct.pack("HHHH", rows, columns, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsz)


def _pty_supported() -> bool:
    return (
        os.name != "nt"
        and fcntl is not None
        and pty is not None
        and termios is not None
    )
