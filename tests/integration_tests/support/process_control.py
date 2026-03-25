from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import socket
import subprocess
import time

import httpx


@dataclass(frozen=True)
class ManagedProcess:
    name: str
    process: subprocess.Popen[str]
    log_file: Path


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def start_process(
    *,
    name: str,
    command: tuple[str, ...],
    cwd: Path,
    env: dict[str, str],
    log_file: Path,
) -> ManagedProcess:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_file.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
    finally:
        log_handle.close()
    return ManagedProcess(name=name, process=process, log_file=log_file)


def wait_for_http_ready(
    *,
    url: str,
    timeout_seconds: float,
    process: ManagedProcess,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    with httpx.Client(timeout=1.0, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.process.poll() is not None:
                raise RuntimeError(
                    f"{process.name} exited before ready.\n{read_log_tail(process.log_file)}"
                )
            try:
                response = client.get(url)
                if response.status_code < 500:
                    return
            except Exception:
                pass
            time.sleep(0.2)

    raise RuntimeError(
        f"Timed out waiting for {process.name} to become ready at {url}.\n"
        f"{read_log_tail(process.log_file)}"
    )


def stop_process(process: ManagedProcess) -> None:
    if process.process.poll() is not None:
        return
    process.process.terminate()
    try:
        process.process.wait(timeout=8.0)
    except subprocess.TimeoutExpired:
        process.process.kill()
        process.process.wait(timeout=5.0)


def read_log_tail(log_file: Path, max_lines: int = 80) -> str:
    if not log_file.exists():
        return "(no log file)"
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = lines[-max_lines:]
    return "\n".join(tail)
