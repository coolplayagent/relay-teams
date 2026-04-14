# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import IO, Callable, Iterator, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from relay_teams.logger import get_logger
from relay_teams.sessions.runs.background_tasks.command_runtime import (
    kill_process_tree_by_pid,
)

LOGGER = get_logger(__name__)
_LOCALHOST_RUN_PROVIDER = "localhost.run"
_DEFAULT_LOCAL_HOST = "127.0.0.1"
_DEFAULT_LOCAL_PORT = 8000
_DEFAULT_WAIT_TIMEOUT_MS = 15000
_PUBLIC_URL_PATTERN = re.compile(r"https://([A-Za-z0-9.-]+)")
TunnelStatusValue = Literal["idle", "starting", "active", "stopped", "failed"]


class LocalhostRunTunnelStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_host: str | None = None
    local_port: int | None = None
    wait_timeout_ms: int = _DEFAULT_WAIT_TIMEOUT_MS
    auto_save_webhook_base_url: bool = True

    @field_validator("local_host")
    @classmethod
    def _normalize_local_host(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized

    @field_validator("local_port")
    @classmethod
    def _validate_local_port(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 1 or value > 65535:
            raise ValueError("local_port must be between 1 and 65535")
        return value

    @field_validator("wait_timeout_ms")
    @classmethod
    def _validate_wait_timeout_ms(cls, value: int) -> int:
        if value < 0:
            raise ValueError("wait_timeout_ms must be non-negative")
        return value


class LocalhostRunTunnelStopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clear_webhook_base_url_if_matching: bool = True


class LocalhostRunTunnelStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = _LOCALHOST_RUN_PROVIDER
    status: TunnelStatusValue = "idle"
    public_url: str | None = None
    address: str | None = None
    connection_id: str | None = None
    local_host: str | None = None
    local_port: int | None = None
    pid: int | None = None
    started_at: str | None = None
    stopped_at: str | None = None
    last_event: str | None = None
    last_message: str | None = None
    error_message: str | None = None


class LocalhostRunTunnelEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: str | None = None
    event: str | None = None
    message: str | None = None
    address: str | None = None
    public_url: str | None = None
    status: str | None = None


def parse_localhost_run_event_line(
    raw_line: str,
) -> LocalhostRunTunnelEvent | None:
    line = raw_line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        if "tunneled" not in line.lower():
            return LocalhostRunTunnelEvent(message=line)
        match = _PUBLIC_URL_PATTERN.search(line)
        if not match:
            return LocalhostRunTunnelEvent(message=line)
        address = match.group(1)
        return LocalhostRunTunnelEvent(
            message=line,
            address=address,
            public_url=f"https://{address}",
        )

    if not isinstance(payload, dict):
        return None

    message = payload.get("message")
    address = payload.get("address")
    if not isinstance(address, str) or not address.strip():
        address = None

    public_url = None
    if address is not None:
        public_url = f"https://{address}"
    elif isinstance(message, str):
        match = _PUBLIC_URL_PATTERN.search(message)
        if match:
            address = match.group(1)
            public_url = f"https://{address}"

    connection_id = payload.get("connection_id")
    event = payload.get("event") or payload.get("type")
    status = payload.get("status")
    return LocalhostRunTunnelEvent(
        connection_id=connection_id if isinstance(connection_id, str) else None,
        event=event if isinstance(event, str) else None,
        message=message if isinstance(message, str) else None,
        address=address,
        public_url=public_url,
        status=status if isinstance(status, str) else None,
    )


class LocalhostRunTunnelService:
    def __init__(
        self,
        *,
        ssh_path_lookup: Callable[[str], str | None] | None = None,
        process_factory: Callable[..., subprocess.Popen[str]] | None = None,
        kill_process: Callable[[int], bool] | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._ssh_path_lookup = (
            shutil.which if ssh_path_lookup is None else ssh_path_lookup
        )
        self._process_factory = (
            subprocess.Popen if process_factory is None else process_factory
        )
        self._kill_process = (
            kill_process_tree_by_pid if kill_process is None else kill_process
        )
        if now is None:
            self._now = lambda: datetime.now(timezone.utc)
        else:
            self._now = now
        self._sleep = time.sleep if sleep is None else sleep
        self._lock = threading.Lock()
        self._state = LocalhostRunTunnelStatus()
        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_requested = False

    def get_status(self) -> LocalhostRunTunnelStatus:
        with self._lock:
            self._refresh_process_state_locked()
            return self._state.model_copy(deep=True)

    def start(
        self,
        request: LocalhostRunTunnelStartRequest,
    ) -> LocalhostRunTunnelStatus:
        local_host = request.local_host or _DEFAULT_LOCAL_HOST
        local_port = request.local_port or _DEFAULT_LOCAL_PORT

        with self._lock:
            self._refresh_process_state_locked()
            if self._state.status in {"starting", "active"}:
                return self._state.model_copy(deep=True)

            ssh_path = self._ssh_path_lookup("ssh")
            if ssh_path is None:
                raise RuntimeError(
                    "ssh is not installed; cannot create localhost.run tunnel"
                )

            command = self._build_command(
                ssh_path=ssh_path,
                local_host=local_host,
                local_port=local_port,
            )
            LOGGER.info("Starting localhost.run tunnel", extra={"command": command})
            try:
                process = self._process_factory(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
            except OSError as exc:
                raise RuntimeError(
                    f"Failed to start localhost.run tunnel: {exc}"
                ) from exc

            self._process = process
            self._stop_requested = False
            self._state = LocalhostRunTunnelStatus(
                status="starting",
                local_host=local_host,
                local_port=local_port,
                pid=process.pid,
                started_at=self._iso_now(),
                stopped_at=None,
                last_event="spawned",
                last_message="Starting localhost.run tunnel...",
                error_message=None,
            )
            self._reader_thread = threading.Thread(
                target=self._consume_process_output,
                args=(process,),
                daemon=True,
                name="localhost-run-tunnel-reader",
            )
            self._reader_thread.start()

        return self._wait_for_status(request.wait_timeout_ms)

    def stop(self) -> LocalhostRunTunnelStatus:
        process: subprocess.Popen[str] | None
        reader_thread: threading.Thread | None
        with self._lock:
            self._refresh_process_state_locked()
            process = self._process
            reader_thread = self._reader_thread
            public_url = self._state.public_url
            address = self._state.address
            connection_id = self._state.connection_id
            local_host = self._state.local_host
            local_port = self._state.local_port
            pid = self._state.pid
            last_message = self._state.last_message
            self._stop_requested = True

        if process is not None and process.poll() is None and process.pid is not None:
            try:
                self._kill_process(process.pid)
            except Exception:
                LOGGER.exception("Failed to stop localhost.run tunnel process")

        if reader_thread is not None:
            reader_thread.join(timeout=2.0)

        with self._lock:
            self._process = None
            self._reader_thread = None
            self._state = LocalhostRunTunnelStatus(
                status="stopped",
                public_url=public_url,
                address=address,
                connection_id=connection_id,
                local_host=local_host,
                local_port=local_port,
                pid=pid,
                started_at=self._state.started_at,
                stopped_at=self._iso_now(),
                last_event="stopped",
                last_message=last_message or "localhost.run tunnel stopped.",
                error_message=None,
            )
            return self._state.model_copy(deep=True)

    def _build_command(
        self,
        *,
        ssh_path: str,
        local_host: str,
        local_port: int,
    ) -> list[str]:
        return [
            ssh_path,
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ExitOnForwardFailure=yes",
            "-T",
            "-R",
            f"80:{local_host}:{local_port}",
            _LOCALHOST_RUN_PROVIDER,
            "--",
            "--output",
            "json",
        ]

    def _wait_for_status(self, wait_timeout_ms: int) -> LocalhostRunTunnelStatus:
        if wait_timeout_ms <= 0:
            return self.get_status()
        deadline = time.monotonic() + (wait_timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            status = self.get_status()
            if status.status in {"active", "failed"}:
                return status
            self._sleep(0.1)
        return self.get_status()

    def _consume_process_output(self, process: subprocess.Popen[str]) -> None:
        stdout = process.stdout
        if stdout is None:
            with self._lock:
                self._mark_failed_locked("localhost.run tunnel did not provide stdout")
            return

        try:
            for raw_line in self._iter_output_lines(stdout):
                event = parse_localhost_run_event_line(raw_line)
                if event is None:
                    continue
                with self._lock:
                    if process is not self._process:
                        return
                    self._apply_event_locked(event)
        except Exception:
            LOGGER.exception("Failed while consuming localhost.run tunnel output")
            with self._lock:
                if process is self._process and not self._stop_requested:
                    self._mark_failed_locked(
                        "Failed while reading localhost.run tunnel output"
                    )
        finally:
            return_code: int | None = None
            try:
                return_code = process.wait(timeout=0.2)
            except Exception:
                return_code = process.poll()
            with self._lock:
                if process is self._process:
                    self._refresh_process_state_locked(return_code=return_code)

    def _iter_output_lines(self, stdout: IO[str]) -> Iterator[str]:
        for line in stdout:
            yield line.rstrip("\n")

    def _apply_event_locked(self, event: LocalhostRunTunnelEvent) -> None:
        next_status = self._state.status
        error_message = self._state.error_message
        if event.public_url:
            next_status = "active"
            error_message = None
        self._state = self._state.model_copy(
            update={
                "status": next_status,
                "public_url": event.public_url or self._state.public_url,
                "address": event.address or self._state.address,
                "connection_id": event.connection_id or self._state.connection_id,
                "last_event": event.event or self._state.last_event,
                "last_message": event.message or self._state.last_message,
                "error_message": error_message,
            }
        )

    def _refresh_process_state_locked(self, *, return_code: int | None = None) -> None:
        process = self._process
        if process is None:
            return
        observed_return_code = process.poll() if return_code is None else return_code
        if observed_return_code is None:
            return

        if self._stop_requested:
            self._state = self._state.model_copy(
                update={
                    "status": "stopped",
                    "stopped_at": self._state.stopped_at or self._iso_now(),
                    "last_event": self._state.last_event or "stopped",
                    "error_message": None,
                }
            )
        elif self._state.public_url:
            self._mark_failed_locked(
                f"localhost.run tunnel exited unexpectedly with code {observed_return_code}"
            )
        else:
            self._mark_failed_locked(
                self._state.last_message
                or f"localhost.run tunnel exited before publishing a public URL (code {observed_return_code})"
            )

        self._process = None
        self._reader_thread = None

    def _mark_failed_locked(self, message: str) -> None:
        self._state = self._state.model_copy(
            update={
                "status": "failed",
                "stopped_at": self._state.stopped_at or self._iso_now(),
                "error_message": message,
                "last_message": self._state.last_message or message,
            }
        )

    def _iso_now(self) -> str:
        return self._now().isoformat().replace("+00:00", "Z")
