# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
from typing import Dict, List, Optional, Union
from urllib.parse import urlparse

import httpx
import typer

from relay_teams.env import load_proxy_env_config, sync_proxy_env_to_process_env
from relay_teams.env.env_cli import env_app
from relay_teams.external_agents.agent_cli import build_external_agents_app
from relay_teams.interfaces.cli.gateway_cli import build_gateway_app
from relay_teams.interfaces.cli.approvals_cli import build_approvals_app
from relay_teams.interfaces.cli.hooks_cli import build_hooks_app
from relay_teams.interfaces.cli.questions_cli import build_questions_app
from relay_teams.interfaces.cli.metrics_cli import build_metrics_app
from relay_teams.interfaces.cli.runs_cli import build_runs_app
from relay_teams.interfaces.cli.run_prompt_cli import (
    execute_prompt as _execute_prompt_impl,
    root_command as _root_command_impl,
    run_single_prompt as _run_single_prompt_impl,
    stream_events as _stream_events_impl,
)
from relay_teams.interfaces.server.cli import build_server_app
from relay_teams.interfaces.server.runtime_identity import (
    ServerHealthPayload,
    build_server_runtime_identity,
    raise_if_runtime_mismatch,
)
from relay_teams.net.clients import create_async_http_client
from relay_teams.mcp.mcp_cli import mcp_app
from relay_teams.paths import get_project_config_dir
from relay_teams.roles.role_cli import build_roles_app
from relay_teams.sessions.session_models import SessionMode
from relay_teams.skills.clawhub_cli import build_clawhub_app
from relay_teams.skills.skill_cli import skills_app

app = typer.Typer(no_args_is_help=False, pretty_exceptions_enable=False)

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
_LOCAL_SERVER_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0", "::"}


def _request_json(
    base_url: str,
    method: str,
    path: str,
    payload: Optional[Dict[str, object]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    timeout_seconds: float = 30.0,
) -> Union[Dict[str, object], List[object]]:
    return asyncio.run(
        _request_json_async(
            base_url=base_url,
            method=method,
            path=path,
            payload=payload,
            extra_headers=extra_headers,
            timeout_seconds=timeout_seconds,
        )
    )


async def _request_json_async(
    *,
    base_url: str,
    method: str,
    path: str,
    payload: Optional[Dict[str, object]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    timeout_seconds: float = 30.0,
) -> Union[Dict[str, object], List[object]]:
    headers = {"Accept": "application/json"}
    if extra_headers is not None:
        headers.update(extra_headers)

    try:
        async with create_async_http_client(
            proxy_config=load_proxy_env_config(),
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=timeout_seconds,
        ) as client:
            response = await client.request(
                method,
                f"{base_url.rstrip('/')}{path}",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            if not response.content:
                return {}
            data = response.json()
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                return data
            return {"data": data}
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        raise RuntimeError(
            f"HTTP {exc.response.status_code} {method} {path}: {detail}"
        ) from exc
    except (httpx.RequestError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Failed to connect to {base_url}: {exc}") from exc


def _is_server_healthy(base_url: str) -> bool:
    health = _get_server_health(base_url)
    return health is not None and health.status == "ok"


async def _is_server_healthy_async(base_url: str) -> bool:
    health = await _get_server_health_async(base_url)
    return health is not None and health.status == "ok"


def _get_server_health(base_url: str) -> Optional[ServerHealthPayload]:
    return asyncio.run(_get_server_health_async(base_url))


async def _get_server_health_async(base_url: str) -> Optional[ServerHealthPayload]:
    try:
        health_response = await _request_json_async(
            base_url=base_url,
            method="GET",
            path="/api/system/health",
            timeout_seconds=1.5,
        )
        health = _require_object_response(health_response, "/api/system/health")
        return ServerHealthPayload.model_validate(health)
    except Exception:
        return None


def _start_server_daemon(host: str, port: int) -> None:
    sync_proxy_env_to_process_env(load_proxy_env_config())
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
    else:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )


def _wait_until_healthy(base_url: str, timeout_seconds: float = 20.0) -> bool:
    return asyncio.run(
        _wait_until_healthy_async(base_url, timeout_seconds=timeout_seconds)
    )


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


def _auto_start_if_needed(base_url: str, autostart: bool) -> None:
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    live_health = _get_server_health(base_url)
    if live_health is not None and live_health.status == "ok":
        if host in _LOCAL_SERVER_HOSTS:
            raise_if_runtime_mismatch(
                health=live_health,
                current=build_server_runtime_identity(
                    config_dir=get_project_config_dir()
                ),
                display_url=base_url,
            )
        return

    if not autostart:
        raise RuntimeError(
            "Agent Teams server is not running and --no-autostart was provided"
        )

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError(
            f"Refusing to autostart server for non-local base URL: {base_url}"
        )

    _start_server_daemon(host=host, port=port)
    if not _wait_until_healthy(base_url):
        raise RuntimeError("Failed to start local Agent Teams server")


def _module_request_json(
    base_url: str,
    method: str,
    path: str,
    payload: Optional[Dict[str, object]] = None,
) -> Union[Dict[str, object], List[object]]:
    return _request_json(
        base_url=base_url,
        method=method,
        path=path,
        payload=payload,
    )


def _module_auto_start(base_url: str, autostart: bool) -> None:
    _auto_start_if_needed(base_url, autostart=autostart)


server_app = build_server_app()
roles_app = build_roles_app(
    request_json=_module_request_json,
    auto_start_if_needed=_module_auto_start,
    default_base_url=DEFAULT_BASE_URL,
)
agents_app = build_external_agents_app(
    request_json=_module_request_json,
    auto_start_if_needed=_module_auto_start,
    default_base_url=DEFAULT_BASE_URL,
)
approvals_app = build_approvals_app(
    request_json=_module_request_json,
    auto_start_if_needed=_module_auto_start,
    default_base_url=DEFAULT_BASE_URL,
)
questions_app = build_questions_app(
    request_json=_module_request_json,
    auto_start_if_needed=_module_auto_start,
    default_base_url=DEFAULT_BASE_URL,
)
gateway_app = build_gateway_app(
    request_json=_module_request_json,
    auto_start_if_needed=_module_auto_start,
    default_base_url=DEFAULT_BASE_URL,
)
hooks_app = build_hooks_app(
    request_json=_module_request_json,
    auto_start_if_needed=_module_auto_start,
    default_base_url=DEFAULT_BASE_URL,
)
metrics_app = build_metrics_app(
    request_json=_module_request_json,
    auto_start_if_needed=_module_auto_start,
    default_base_url=DEFAULT_BASE_URL,
)
runs_app = build_runs_app(
    request_json=_module_request_json,
    auto_start_if_needed=_module_auto_start,
    default_base_url=DEFAULT_BASE_URL,
)
clawhub_app = build_clawhub_app(
    request_json=_module_request_json,
    auto_start_if_needed=_module_auto_start,
    default_base_url=DEFAULT_BASE_URL,
)


def _stream_events(base_url: str, run_id: str, debug: bool) -> None:
    sync_proxy_env_to_process_env(load_proxy_env_config())
    _stream_events_impl(base_url, run_id, debug)


@app.callback(invoke_without_command=True)
def root_command(
    ctx: typer.Context,
    message: str | None = typer.Option(
        None,
        "-m",
        "--message",
        help="Run a single prompt in a new session.",
    ),
    mode: SessionMode = typer.Option(
        SessionMode.NORMAL,
        "--mode",
        help=(
            "Run the quick prompt session in normal mode or orchestration mode. "
            "Requires --message."
        ),
    ),
    role: str | None = typer.Option(
        None,
        "--role",
        help=(
            "Select the root role to use with --mode normal. "
            "If omitted, the session default MainAgent is used."
        ),
    ),
    orchestration: str | None = typer.Option(
        None,
        "--orchestration",
        help=(
            "Select the orchestration id to use with --mode orchestration. "
            "If omitted, the current default orchestration is used."
        ),
    ),
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        help=(
            "Create or reuse a workspace for the given workspace root path. "
            "Defaults to the current directory. Requires --message."
        ),
    ),
    yolo: bool = typer.Option(
        True,
        "--yolo/--no-yolo",
        help="Skip tool approvals for the run.",
    ),
) -> None:
    _root_command_impl(
        ctx,
        message,
        yolo,
        mode,
        role,
        orchestration,
        workspace,
        run_single_prompt=_run_single_prompt,
    )


def _run_single_prompt(
    message: str,
    yolo: bool,
    session_mode: SessionMode,
    normal_root_role_id: str | None,
    orchestration_id: str | None,
    workspace: Path | None,
) -> None:
    _run_single_prompt_impl(
        message,
        yolo,
        session_mode,
        normal_root_role_id,
        orchestration_id,
        workspace,
        default_base_url=DEFAULT_BASE_URL,
        execute_prompt=_execute_prompt,
    )


def _execute_prompt(
    *,
    message: str,
    session_id: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    execution_mode: str = "ai",
    yolo: bool = False,
    session_mode: SessionMode = SessionMode.NORMAL,
    normal_root_role_id: str | None = None,
    orchestration_id: str | None = None,
    workspace: Path | None = None,
    autostart: bool = True,
    debug: bool = False,
) -> None:
    _execute_prompt_impl(
        message,
        session_id,
        base_url,
        execution_mode,
        yolo,
        session_mode,
        normal_root_role_id,
        orchestration_id,
        workspace,
        autostart,
        debug,
        auto_start_if_needed=_module_auto_start,
        request_json=_module_request_json,
        stream_events=_stream_events,
    )


app.add_typer(server_app, name="server")
app.add_typer(roles_app, name="roles")
app.add_typer(agents_app, name="agents")
app.add_typer(approvals_app, name="approvals")
app.add_typer(questions_app, name="questions")
app.add_typer(env_app, name="env")
app.add_typer(mcp_app, name="mcp")
app.add_typer(skills_app, name="skills")
app.add_typer(clawhub_app, name="clawhub")
app.add_typer(metrics_app, name="metrics")
app.add_typer(runs_app, name="runs")
app.add_typer(hooks_app, name="hooks")
app.add_typer(gateway_app, name="gateway")


def main() -> None:
    app()


def _require_object_response(
    payload: dict[str, object] | list[object],
    path: str,
) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    raise RuntimeError(f"Expected JSON object from {path}")
