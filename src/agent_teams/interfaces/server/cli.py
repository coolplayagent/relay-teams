# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from pathlib import Path
import os
import signal
import subprocess
import sys
import time
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, ValidationError
import typer

from agent_teams.paths import get_project_config_dir

DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 8000
_SERVER_PROCESS_FILE_NAME = "server-process.json"
_SERVER_HEALTH_PATH = "/api/system/health"
_RESTART_TIMEOUT_SECONDS = 60.0
_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 10


class ManagedServerProcess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pid: int
    host: str
    port: int


def get_server_process_file_path(project_root: Path | None = None) -> Path:
    return get_project_config_dir(project_root=project_root) / _SERVER_PROCESS_FILE_NAME


def _health_check_host(host: str) -> str:
    if host == "0.0.0.0":
        return "127.0.0.1"
    if host == "::":
        return "::1"
    return host


def is_server_healthy(base_url: str) -> bool:
    request = Request(
        url=f"{base_url.rstrip('/')}{_SERVER_HEALTH_PATH}",
        method="GET",
        headers={"Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=1.5) as response:
            raw_payload = response.read().decode("utf-8").replace(" ", "")
            return '"status":"ok"' in raw_payload
    except (HTTPError, URLError, OSError):
        return False


def start_server_daemon(host: str, port: int) -> None:
    command = [
        sys.executable,
        "-m",
        "agent_teams",
        "server",
        "start",
        "--host",
        host,
        "--port",
        str(port),
    ]

    if sys.platform.startswith("win"):
        create_new_process_group = int(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        detached_process = int(getattr(subprocess, "DETACHED_PROCESS", 0))
        create_no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        creationflags = create_new_process_group | detached_process | create_no_window
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
        startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0))
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        return

    subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def wait_until_healthy(base_url: str, timeout_seconds: float = 20.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_server_healthy(base_url):
            return True
        time.sleep(0.25)
    return False


_DAEMON_START_TIMEOUT_SECONDS = 20.0


def start(
    host: str = typer.Option(
        DEFAULT_SERVER_HOST, "--host", help="Host to bind the server to"
    ),
    port: int = typer.Option(
        DEFAULT_SERVER_PORT, "--port", help="Port to bind the server to"
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        "-d",
        help="Run the server as a background process.",
    ),
) -> None:
    if daemon:
        _start_daemon(host=host, port=port)
        return

    uvicorn_module = import_module("uvicorn")
    server_module = import_module("agent_teams.interfaces.server.app")
    fastapi_app = getattr(server_module, "app")
    uvicorn_run = cast(Callable[..., None], getattr(uvicorn_module, "run"))
    current_pid = os.getpid()

    _register_managed_server(
        ManagedServerProcess(pid=current_pid, host=host, port=port)
    )

    try:
        typer.echo(f"Starting Agent Teams server on http://{host}:{port}")
        uvicorn_run(
            fastapi_app,
            host=host,
            port=port,
            timeout_graceful_shutdown=_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
        )
    finally:
        _clear_managed_server(expected_pid=current_pid)


def _start_daemon(host: str, port: int) -> None:
    check_host = _health_check_host(host)
    check_url = f"http://{check_host}:{port}"

    existing = _load_managed_server(raise_on_invalid=False)
    if existing is not None and _is_process_running(existing.pid):
        if is_server_healthy(check_url):
            typer.echo(
                f"Agent Teams server is already running on http://{host}:{port} "
                f"(pid {existing.pid})"
            )
            return
        _stop_managed_server(force=False)

    start_server_daemon(host=host, port=port)

    if not wait_until_healthy(
        check_url, timeout_seconds=_DAEMON_START_TIMEOUT_SECONDS
    ) or not _wait_for_managed_server(
        host, port, timeout_seconds=_DAEMON_START_TIMEOUT_SECONDS
    ):
        raise RuntimeError(
            f"Failed to start Agent Teams server at http://{host}:{port}"
        )

    process = _load_managed_server(raise_on_invalid=False)
    pid_info = f" (pid {process.pid})" if process is not None else ""
    typer.echo(f"Agent Teams server started on http://{host}:{port}{pid_info}")


def stop(
    force: bool = typer.Option(
        False,
        "--force",
        help="Force kill the managed server process immediately.",
    ),
) -> None:
    stopped_process = _stop_managed_server(force=force)
    if stopped_process is None:
        typer.echo("No managed Agent Teams server process found.")
        return
    typer.echo(
        f"Stopped Agent Teams server on http://{stopped_process.host}:{stopped_process.port}"
    )


def restart(
    host: str | None = typer.Option(
        None, "--host", help="Host to bind the restarted server to."
    ),
    port: int | None = typer.Option(
        None, "--port", help="Port to bind the restarted server to."
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Force kill the existing managed server before restart.",
    ),
) -> None:
    existing_process = _load_managed_server(raise_on_invalid=False)
    resolved_host = host or (
        existing_process.host if existing_process is not None else DEFAULT_SERVER_HOST
    )
    resolved_port = port or (
        existing_process.port if existing_process is not None else DEFAULT_SERVER_PORT
    )
    check_host = _health_check_host(resolved_host)
    check_url = f"http://{check_host}:{resolved_port}"
    display_url = f"http://{resolved_host}:{resolved_port}"

    stopped_process = _stop_managed_server(force=force)
    if stopped_process is None and is_server_healthy(check_url):
        raise RuntimeError(
            f"Agent Teams server is already responding at {display_url}, "
            "but it is not managed by this CLI."
        )

    start_server_daemon(host=resolved_host, port=resolved_port)

    if not wait_until_healthy(
        check_url,
        timeout_seconds=_RESTART_TIMEOUT_SECONDS,
    ) or not _wait_for_managed_server(
        resolved_host,
        resolved_port,
        timeout_seconds=_RESTART_TIMEOUT_SECONDS,
    ):
        raise RuntimeError(f"Failed to restart Agent Teams server at {display_url}")

    typer.echo(f"Restarted Agent Teams server on {display_url}")


def build_server_app() -> typer.Typer:
    server_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
    server_app.command("start")(start)
    server_app.command("stop")(stop)
    server_app.command("restart")(restart)
    return server_app


def _register_managed_server(process: ManagedServerProcess) -> None:
    existing_process = _load_managed_server(raise_on_invalid=False)
    if existing_process is not None and existing_process.pid != process.pid:
        if _is_process_running(existing_process.pid):
            raise RuntimeError(
                "Managed Agent Teams server is already running at "
                f"http://{existing_process.host}:{existing_process.port} "
                f"(pid {existing_process.pid})"
            )
        _clear_managed_server()

    process_file = get_server_process_file_path()
    process_file.parent.mkdir(parents=True, exist_ok=True)
    process_file.write_text(process.model_dump_json(indent=2), encoding="utf-8")


def _load_managed_server(*, raise_on_invalid: bool) -> ManagedServerProcess | None:
    process_file = get_server_process_file_path()
    if not process_file.exists():
        return None

    raw_payload = process_file.read_text(encoding="utf-8")
    try:
        return ManagedServerProcess.model_validate_json(raw_payload)
    except (ValidationError, ValueError) as exc:
        if not raise_on_invalid:
            return None
        raise RuntimeError(
            f"Managed server state file is invalid: {process_file}"
        ) from exc


def _clear_managed_server(expected_pid: int | None = None) -> None:
    process_file = get_server_process_file_path()
    if not process_file.exists():
        return

    if expected_pid is not None:
        process = _load_managed_server(raise_on_invalid=False)
        if process is not None and process.pid != expected_pid:
            return

    process_file.unlink(missing_ok=True)


def _stop_managed_server(
    force: bool,
    timeout_seconds: float = 10.0,
) -> ManagedServerProcess | None:
    process = _load_managed_server(raise_on_invalid=True)
    if process is None:
        return None

    if not _is_process_running(process.pid):
        _clear_managed_server(expected_pid=process.pid)
        return None

    _terminate_process(process.pid, force=force)
    if not _wait_for_process_exit(process.pid, timeout_seconds=timeout_seconds):
        if force:
            raise RuntimeError(
                f"Failed to force stop Agent Teams server process {process.pid}"
            )
        raise RuntimeError(
            "Agent Teams server did not stop within the timeout; retry with --force."
        )

    _clear_managed_server(expected_pid=process.pid)
    return process


def _wait_for_managed_server(
    host: str,
    port: int,
    timeout_seconds: float = 20.0,
) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        process = _load_managed_server(raise_on_invalid=False)
        if (
            process is not None
            and process.host == host
            and process.port == port
            and _is_process_running(process.pid)
        ):
            return True
        time.sleep(0.25)
    return False


def _wait_for_process_exit(pid: int, timeout_seconds: float) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _is_process_running(pid):
            return True
        time.sleep(0.25)
    return not _is_process_running(pid)


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False

    if sys.platform.startswith("win"):
        completed = _run_hidden_windows_command(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"$null = Get-Process -Id {pid} -ErrorAction Stop",
            ]
        )
        return completed.returncode == 0

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _terminate_process(pid: int, force: bool) -> None:
    if sys.platform.startswith("win") and force:
        completed = _run_hidden_windows_command(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"Stop-Process -Id {pid} -Force",
            ]
        )
        if completed.returncode != 0 and _is_process_running(pid):
            raise RuntimeError(f"Failed to stop Agent Teams server process {pid}")
        return

    termination_signal = (
        signal.SIGKILL
        if force and not sys.platform.startswith("win")
        else signal.SIGTERM
    )
    try:
        os.kill(pid, termination_signal)
    except ProcessLookupError:
        return


def _run_hidden_windows_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    create_no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
    startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0))
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        creationflags=create_no_window,
        startupinfo=startupinfo,
    )
