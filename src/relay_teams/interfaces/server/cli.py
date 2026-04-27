# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
import os
import signal
import subprocess
import sys
import time
from typing import cast

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError
import typer
from typer.models import OptionInfo

from relay_teams.interfaces.server.runtime_identity import (
    ServerHealthPayload,
    ServerRuntimeIdentity,
    build_server_runtime_identity,
    raise_if_runtime_mismatch,
)
from relay_teams.interfaces.server.control_plane import (
    ControlPlaneServerHandle,
    allocate_control_plane_config,
    clear_control_plane_env,
    publish_control_plane_env,
    start_control_plane_server,
)
from relay_teams.net.clients import create_async_http_client
from relay_teams.paths import get_project_config_dir

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
    control_plane_host: str | None = None
    control_plane_port: int | None = None
    python_executable: str | None = None
    package_root: str | None = None
    builtin_skills_dir: str | None = None


def get_server_process_file_path(project_root: Path | None = None) -> Path:
    return get_project_config_dir(project_root=project_root) / _SERVER_PROCESS_FILE_NAME


def _health_check_host(host: str) -> str:
    if host == "0.0.0.0":
        return "127.0.0.1"
    if host == "::":
        return "::1"
    return host


def _server_bind_base_url(host: str, port: int) -> str:
    url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{url_host}:{port}"


def get_server_health(base_url: str) -> ServerHealthPayload | None:
    return asyncio.run(get_server_health_async(base_url))


async def get_server_health_async(base_url: str) -> ServerHealthPayload | None:
    try:
        async with create_async_http_client(
            timeout_seconds=1.5,
            connect_timeout_seconds=1.5,
        ) as client:
            response = await client.get(
                f"{base_url.rstrip('/')}{_SERVER_HEALTH_PATH}",
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            return ServerHealthPayload.model_validate_json(response.text)
    except (
        httpx.HTTPError,
        OSError,
        ValidationError,
        ValueError,
    ):
        return None


async def _is_server_healthy_async(base_url: str) -> bool:
    health = await get_server_health_async(base_url)
    return health is not None and health.status == "ok"


def is_server_healthy(base_url: str) -> bool:
    health = get_server_health(base_url)
    return health is not None and health.status == "ok"


async def _wait_until_healthy_async(
    base_url: str, timeout_seconds: float = 20.0
) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        if await _is_server_healthy_async(base_url):
            return True
        await asyncio.sleep(0.25)
    return False


def wait_until_healthy(base_url: str, timeout_seconds: float = 20.0) -> bool:
    return asyncio.run(
        _wait_until_healthy_async(base_url, timeout_seconds=timeout_seconds)
    )


def start_server_daemon(host: str, port: int) -> None:
    command = [
        sys.executable,
        "-m",
        "relay_teams",
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


_DAEMON_START_TIMEOUT_SECONDS = 20.0


def _unwrap_option_default[T](value: T | OptionInfo) -> T:
    if isinstance(value, OptionInfo):
        return cast(T, value.default)
    return value


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
    host = _unwrap_option_default(host)
    port = _unwrap_option_default(port)
    daemon = _unwrap_option_default(daemon)

    if daemon:
        _start_daemon(host=host, port=port)
        return

    uvicorn_module = import_module("uvicorn")
    server_module = import_module("relay_teams.interfaces.server.app")
    fastapi_app = getattr(server_module, "app")
    uvicorn_run = cast(Callable[..., None], getattr(uvicorn_module, "run"))
    current_pid = os.getpid()
    control_plane: ControlPlaneServerHandle | None = None

    try:
        control_plane = _start_control_plane(host=host, port=port)
        _register_managed_server(
            _build_managed_server_process(
                pid=current_pid,
                host=host,
                port=port,
                control_plane=control_plane,
            )
        )
        typer.echo(f"Starting Agent Teams server on http://{host}:{port}")
        typer.echo(f"Control-plane liveness on {control_plane.config.live_url}")
        uvicorn_run(
            fastapi_app,
            host=host,
            port=port,
            ws="websockets-sansio",
            timeout_graceful_shutdown=_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
        )
    finally:
        if control_plane is not None:
            control_plane.stop()
        clear_control_plane_env()
        _clear_managed_server(expected_pid=current_pid)


def _start_daemon(host: str, port: int) -> None:
    check_host = _health_check_host(host)
    check_url = f"http://{check_host}:{port}"
    display_url = f"http://{host}:{port}"

    existing = _load_managed_server(raise_on_invalid=False)
    if existing is not None and _is_process_running(existing.pid):
        health = get_server_health(check_url)
        if health is not None and health.status == "ok":
            raise_if_runtime_mismatch(
                health=health,
                current=_get_current_runtime_identity(),
                display_url=display_url,
            )
            typer.echo(
                f"Agent Teams server is already running on http://{host}:{port} "
                f"(pid {existing.pid})"
            )
            return
        _stop_managed_server(force=False)

    live_health = get_server_health(check_url)
    if live_health is not None and live_health.status == "ok":
        raise_if_runtime_mismatch(
            health=live_health,
            current=_get_current_runtime_identity(),
            display_url=display_url,
        )
        typer.echo(f"Agent Teams server is already running on {display_url}")
        return

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
    force = _unwrap_option_default(force)
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
    host = _unwrap_option_default(host)
    port = _unwrap_option_default(port)
    force = _unwrap_option_default(force)

    stopped_process = _stop_managed_server(force=force)
    resolved_host = host or (
        stopped_process.host if stopped_process is not None else DEFAULT_SERVER_HOST
    )
    resolved_port = port or (
        stopped_process.port if stopped_process is not None else DEFAULT_SERVER_PORT
    )
    check_host = _health_check_host(resolved_host)
    check_url = f"http://{check_host}:{resolved_port}"
    display_url = f"http://{resolved_host}:{resolved_port}"

    live_health = get_server_health(check_url)
    if live_health is not None and live_health.status == "ok":
        raise_if_runtime_mismatch(
            health=live_health,
            current=_get_current_runtime_identity(),
            display_url=display_url,
        )
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


def _build_managed_server_process(
    *,
    pid: int,
    host: str,
    port: int,
    control_plane: ControlPlaneServerHandle | None = None,
) -> ManagedServerProcess:
    runtime_identity = _get_current_runtime_identity()
    return ManagedServerProcess(
        pid=pid,
        host=host,
        port=port,
        control_plane_host=control_plane.config.host if control_plane else None,
        control_plane_port=control_plane.config.port if control_plane else None,
        python_executable=runtime_identity.python_executable,
        package_root=runtime_identity.package_root,
        builtin_skills_dir=runtime_identity.builtin_skills_dir,
    )


def _start_control_plane(*, host: str, port: int) -> ControlPlaneServerHandle:
    config = allocate_control_plane_config(
        host=host,
        port=port,
        main_base_url=_server_bind_base_url(host, port),
    )
    handle = start_control_plane_server(config)
    publish_control_plane_env(config)
    return handle


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


def _get_current_runtime_identity() -> ServerRuntimeIdentity:
    return build_server_runtime_identity(config_dir=get_project_config_dir())
