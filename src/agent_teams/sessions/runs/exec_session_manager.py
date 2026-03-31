# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections import deque
import codecs
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
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.exec_session_models import (
    ExecSessionRecord,
    ExecSessionStatus,
)
from agent_teams.sessions.runs.exec_session_repo import ExecSessionRepository
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

DEFAULT_EXEC_SESSION_TIMEOUT_MS = 30 * 60 * 1000
MIN_EXEC_COMMAND_YIELD_MS = 250
MIN_EMPTY_POLL_YIELD_MS = 5000
MAX_WRITE_WAIT_MS = 30000
MAX_BACKGROUND_POLL_MS = 300000
MAX_EXEC_SESSIONS = 64
PROTECTED_RECENT_EXEC_SESSIONS = 8
MAX_RECENT_OUTPUT_LINES = 3
MAX_OUTPUT_BUFFER_BYTES = 1024 * 1024
MAX_DELTA_BYTES = 8192
_DEFAULT_PTY_COLUMNS = 120
_DEFAULT_PTY_ROWS = 40


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


class _ExecSessionRuntime:
    def __init__(
        self,
        *,
        record: ExecSessionRecord,
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
        self.recent_output = _RecentOutputBuffer(max_lines=MAX_RECENT_OUTPUT_LINES)
        self.output_buffer = _HeadTailBuffer(max_bytes=MAX_OUTPUT_BUFFER_BYTES)
        self.pump_tasks: list[asyncio.Task[None]] = []
        self.supervisor_task: asyncio.Task[None] | None = None
        self.completed = asyncio.Event()
        self.change_condition = asyncio.Condition()
        self.change_version = 0
        self.stop_requested = False


class ExecSessionManager:
    def __init__(
        self,
        *,
        repository: ExecSessionRepository,
        run_event_hub: RunEventHub,
    ) -> None:
        self._repository = repository
        self._run_event_hub = run_event_hub
        self._runtimes: dict[str, _ExecSessionRuntime] = {}

    async def start_session(
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
    ) -> ExecSessionRecord:
        await self._prune_sessions_if_needed()
        effective_timeout_ms = timeout_ms or DEFAULT_EXEC_SESSION_TIMEOUT_MS
        exec_session_id = f"exec_{uuid4().hex[:12]}"
        log_dir = workspace.resolve_tmp_path("exec_sessions", write=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file_path = log_dir / f"{exec_session_id}.log"
        log_file_path.touch(exist_ok=True)
        logical_log_path = workspace.logical_tmp_path(log_file_path)
        record = ExecSessionRecord(
            exec_session_id=exec_session_id,
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
        self._runtimes[exec_session_id] = runtime
        runtime.record = self._repository.upsert(record)
        self._publish_exec_session_event(
            event_type=RunEventType.EXEC_SESSION_STARTED,
            record=runtime.record,
        )
        runtime.supervisor_task = asyncio.create_task(self._supervise(runtime))
        return runtime.record

    async def exec_command(
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
        yield_time_ms: int | None,
        env: dict[str, str] | None,
        tty: bool,
    ) -> tuple[ExecSessionRecord, bool]:
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
        )
        updated, _ = await self.interact_for_run(
            run_id=run_id,
            exec_session_id=record.exec_session_id,
            chars="",
            yield_time_ms=yield_time_ms,
            is_initial_poll=True,
        )
        if updated.is_active:
            follow_up, completed = await self.wait_for_run(
                run_id=run_id,
                exec_session_id=record.exec_session_id,
                wait_ms=250,
            )
            return follow_up, completed
        return updated, True

    def list_for_run(self, run_id: str) -> tuple[ExecSessionRecord, ...]:
        return self._repository.list_by_run(run_id)

    def get_for_run(
        self,
        *,
        run_id: str,
        exec_session_id: str,
    ) -> ExecSessionRecord:
        record = self._get_record(exec_session_id)
        if record.run_id != run_id:
            raise KeyError(
                f"Exec session {exec_session_id} does not belong to run {run_id}"
            )
        return record

    async def wait_for_run(
        self,
        *,
        run_id: str,
        exec_session_id: str,
        wait_ms: int,
    ) -> tuple[ExecSessionRecord, bool]:
        record = self.get_for_run(run_id=run_id, exec_session_id=exec_session_id)
        runtime = self._runtimes.get(exec_session_id)
        if runtime is None or not record.is_active:
            return record, True
        if wait_ms < 1:
            raise ValueError("wait_ms must be >= 1")
        try:
            await asyncio.wait_for(runtime.completed.wait(), timeout=wait_ms / 1000.0)
            return self._get_record(exec_session_id), True
        except asyncio.TimeoutError:
            return self._get_record(exec_session_id), False

    async def interact_for_run(
        self,
        *,
        run_id: str,
        exec_session_id: str,
        chars: str,
        yield_time_ms: int | None,
        is_initial_poll: bool = False,
    ) -> tuple[ExecSessionRecord, bool]:
        record = self.get_for_run(run_id=run_id, exec_session_id=exec_session_id)
        runtime = self._runtimes.get(exec_session_id)
        if runtime is None or not record.is_active:
            return record, True
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
        updated = self._get_record(exec_session_id)
        return updated, not updated.is_active

    async def resize_for_run(
        self,
        *,
        run_id: str,
        exec_session_id: str,
        columns: int,
        rows: int,
    ) -> ExecSessionRecord:
        if columns < 1 or rows < 1:
            raise ValueError("columns and rows must both be >= 1")
        record = self.get_for_run(run_id=run_id, exec_session_id=exec_session_id)
        runtime = self._runtimes.get(exec_session_id)
        if runtime is None or not record.is_active:
            return record
        if not runtime.tty or runtime.master_fd is None:
            raise ValueError("resize is only supported for active TTY exec sessions")
        _set_terminal_size(runtime.master_fd, columns=columns, rows=rows)
        return self._get_record(exec_session_id)

    async def stop_for_run(
        self,
        *,
        run_id: str,
        exec_session_id: str,
        reason: str = "stopped_by_user",
    ) -> ExecSessionRecord:
        _ = reason
        record = self.get_for_run(run_id=run_id, exec_session_id=exec_session_id)
        runtime = self._runtimes.get(exec_session_id)
        if runtime is None or not record.is_active:
            return record
        runtime.stop_requested = True
        if runtime.proc.returncode is None:
            await _kill_process_tree(runtime.proc)
        await runtime.completed.wait()
        return self._get_record(exec_session_id)

    async def stop_all_for_run(self, *, run_id: str, reason: str) -> None:
        active_ids = [
            record.exec_session_id
            for record in self._repository.list_by_run(run_id)
            if record.is_active
        ]
        for exec_session_id in active_ids:
            with contextlib.suppress(KeyError):
                _ = await self.stop_for_run(
                    run_id=run_id,
                    exec_session_id=exec_session_id,
                    reason=reason,
                )

    async def close(self) -> None:
        active_ids = list(self._runtimes.keys())
        for exec_session_id in active_ids:
            record = self._repository.get(exec_session_id)
            if record is None:
                continue
            with contextlib.suppress(KeyError):
                _ = await self.stop_for_run(
                    run_id=record.run_id,
                    exec_session_id=exec_session_id,
                    reason="server_shutdown",
                )

    async def _spawn_runtime(
        self,
        *,
        record: ExecSessionRecord,
        cwd: Path,
        env: dict[str, str] | None,
        log_file_path: Path,
    ) -> _ExecSessionRuntime:
        queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        if record.tty:
            if not _pty_supported():
                raise ValueError("TTY exec sessions are only supported on POSIX hosts")
            assert pty is not None
            master_fd, slave_fd = pty.openpty()
            _set_terminal_size(
                master_fd,
                columns=_DEFAULT_PTY_COLUMNS,
                rows=_DEFAULT_PTY_ROWS,
            )
            shell_env = await build_shell_env(env)
            try:
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
            except Exception:
                with contextlib.suppress(OSError):
                    os.close(master_fd)
                with contextlib.suppress(OSError):
                    os.close(slave_fd)
                raise
            with contextlib.suppress(OSError):
                os.close(slave_fd)
            runtime = _ExecSessionRuntime(
                record=record,
                proc=proc,
                log_file_path=log_file_path,
                tty=True,
                queue=queue,
                stream_count=1,
                master_fd=master_fd,
                slave_fd=None,
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
        runtime = _ExecSessionRuntime(
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
            raise RuntimeError("Failed to capture exec session streams")
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

    async def _pump_master_fd(self, runtime: _ExecSessionRuntime) -> None:
        if runtime.master_fd is None:
            await runtime.queue.put(None)
            return
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
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
                text = decoder.decode(chunk, final=False)
                if text:
                    await runtime.queue.put(("stdout", text))
            final_text = decoder.decode(b"", final=True)
            if final_text:
                await runtime.queue.put(("stdout", final_text))
        finally:
            await runtime.queue.put(None)

    @staticmethod
    def _read_master_fd(fd: int) -> bytes:
        return os.read(fd, MAX_DELTA_BYTES)

    async def _supervise(self, runtime: _ExecSessionRuntime) -> None:
        record = runtime.record
        timeout_ms = record.timeout_ms or DEFAULT_EXEC_SESSION_TIMEOUT_MS
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
                    runtime,
                    stream_name=stream_name,
                    chunk=chunk,
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
        runtime: _ExecSessionRuntime,
        *,
        stream_name: str,
        chunk: str,
    ) -> None:
        self._append_log(runtime.log_file_path, stream_name=stream_name, chunk=chunk)
        runtime.recent_output.feed(chunk)
        runtime.output_buffer.append(chunk)
        runtime.record = self._repository.upsert(
            runtime.record.model_copy(
                update={
                    "recent_output": runtime.recent_output.snapshot(),
                    "output_excerpt": runtime.output_buffer.render(),
                    "updated_at": datetime.now(tz=timezone.utc),
                }
            )
        )
        await self._mark_runtime_changed(runtime)
        self._publish_exec_session_event(
            event_type=RunEventType.EXEC_SESSION_UPDATED,
            record=runtime.record,
        )

    async def _finalize_runtime(
        self,
        runtime: _ExecSessionRuntime,
        *,
        timed_out: bool,
    ) -> None:
        runtime.recent_output.finalize()
        exit_code = runtime.proc.returncode
        if timed_out:
            exit_code = 124
        status = self._resolve_exec_session_status(
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
                    "output_excerpt": runtime.output_buffer.render(),
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                }
            )
        )
        self._runtimes.pop(runtime.record.exec_session_id, None)
        if runtime.proc.stdin is not None:
            runtime.proc.stdin.close()
        if runtime.master_fd is not None:
            with contextlib.suppress(OSError):
                os.close(runtime.master_fd)
        if runtime.slave_fd is not None:
            with contextlib.suppress(OSError):
                os.close(runtime.slave_fd)
        runtime.completed.set()
        await self._mark_runtime_changed(runtime)
        self._publish_exec_session_event(
            event_type=(
                RunEventType.EXEC_SESSION_STOPPED
                if status == ExecSessionStatus.STOPPED
                else RunEventType.EXEC_SESSION_COMPLETED
            ),
            record=runtime.record,
        )

    def _resolve_exec_session_status(
        self,
        *,
        runtime: _ExecSessionRuntime,
        exit_code: int | None,
        timed_out: bool,
    ) -> ExecSessionStatus:
        if runtime.stop_requested:
            return ExecSessionStatus.STOPPED
        if timed_out:
            return ExecSessionStatus.FAILED
        if exit_code == 0:
            return ExecSessionStatus.COMPLETED
        return ExecSessionStatus.FAILED

    async def _write_chars(self, runtime: _ExecSessionRuntime, chars: str) -> None:
        if runtime.tty:
            if runtime.master_fd is None:
                raise RuntimeError("TTY exec session is not writable")
            os.write(runtime.master_fd, chars.encode("utf-8", errors="replace"))
            return
        writer = runtime.proc.stdin
        if writer is None:
            raise RuntimeError("Exec session stdin is not available")
        writer.write(chars.encode("utf-8", errors="replace"))
        await writer.drain()

    async def _wait_for_runtime_change(
        self,
        *,
        runtime: _ExecSessionRuntime,
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

    async def _mark_runtime_changed(self, runtime: _ExecSessionRuntime) -> None:
        async with runtime.change_condition:
            runtime.change_version += 1
            runtime.change_condition.notify_all()

    async def _prune_sessions_if_needed(self) -> None:
        records = list(self._repository.list_all())
        if len(records) < MAX_EXEC_SESSIONS:
            return
        protected = {
            record.exec_session_id
            for record in sorted(
                records, key=lambda item: item.updated_at, reverse=True
            )[:PROTECTED_RECENT_EXEC_SESSIONS]
        }
        reclaimable = [
            record
            for record in sorted(records, key=lambda item: item.updated_at)
            if record.exec_session_id not in protected and not record.is_active
        ]
        if reclaimable:
            self._repository.delete(reclaimable[0].exec_session_id)
            return
        oldest_active = next(
            (
                record
                for record in sorted(records, key=lambda item: item.updated_at)
                if record.exec_session_id not in protected
            ),
            None,
        )
        if oldest_active is not None:
            with contextlib.suppress(KeyError):
                _ = await self.stop_for_run(
                    run_id=oldest_active.run_id,
                    exec_session_id=oldest_active.exec_session_id,
                    reason="lru_pruned",
                )

    def _publish_exec_session_event(
        self,
        *,
        event_type: RunEventType,
        record: ExecSessionRecord,
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
                event=f"exec_session.{event_type.value}",
                message="Exec session state updated",
                payload={
                    "exec_session_id": record.exec_session_id,
                    "run_id": record.run_id,
                    "status": record.status.value,
                },
            )

    def _get_record(self, exec_session_id: str) -> ExecSessionRecord:
        record = self._repository.get(exec_session_id)
        if record is None:
            raise KeyError(f"Unknown exec session: {exec_session_id}")
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


def _set_terminal_size(fd: int, *, columns: int, rows: int) -> None:
    if not _pty_supported():
        raise ValueError("TTY exec sessions are not supported on this host")
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
