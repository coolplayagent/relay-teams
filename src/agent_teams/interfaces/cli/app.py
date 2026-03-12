# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import typer

from agent_teams.env import load_proxy_env_config, sync_proxy_env_to_process_env
from agent_teams.env.env_cli import env_app
from agent_teams.interfaces.cli.approvals import build_approvals_app
from agent_teams.interfaces.cli.prompt_cli import (
    execute_prompt as _execute_prompt_impl,
    root_command as _root_command_impl,
    run_single_prompt as _run_single_prompt_impl,
    stream_events as _stream_events_impl,
)
from agent_teams.interfaces.server.cli import build_server_app
from agent_teams.mcp.mcp_cli import mcp_app
from agent_teams.prompting.cli import build_prompts_app
from agent_teams.reflection import build_reflection_app
from agent_teams.roles.cli import build_roles_app
from agent_teams.skills.cli import skills_app
from agent_teams.triggers.cli import build_triggers_app

app = typer.Typer(no_args_is_help=False, pretty_exceptions_enable=False)

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, object] | list[object]:
    sync_proxy_env_to_process_env(load_proxy_env_config())
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if extra_headers is not None:
        headers.update(extra_headers)

    request = Request(
        url=f"{base_url.rstrip('/')}{path}",
        method=method,
        data=body,
        headers=headers,
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                return {}
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                return data
            return {"data": data}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code} {method} {path}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to connect to {base_url}: {exc}") from exc


def _is_server_healthy(base_url: str) -> bool:
    try:
        health_response = _request_json(
            base_url, "GET", "/api/system/health", timeout_seconds=1.5
        )
        health = _require_object_response(health_response, "/api/system/health")
        return health.get("status") == "ok"
    except Exception:
        return False


def _start_server_daemon(host: str, port: int) -> None:
    sync_proxy_env_to_process_env(load_proxy_env_config())
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
    else:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )


def _wait_until_healthy(base_url: str, timeout_seconds: float = 20.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _is_server_healthy(base_url):
            return True
        time.sleep(0.25)
    return False


def _auto_start_if_needed(base_url: str, autostart: bool) -> None:
    if _is_server_healthy(base_url):
        return

    if not autostart:
        raise RuntimeError(
            "Agent Teams server is not running and --no-autostart was provided"
        )

    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError(
            f"Refusing to autostart server for non-local base URL: {base_url}"
        )

    _start_server_daemon(host=host, port=port)
    if not _wait_until_healthy(base_url):
        raise RuntimeError("Failed to start local Agent Teams server")


def _trigger_request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, object] | list[object]:
    return _request_json(
        base_url=base_url,
        method=method,
        path=path,
        payload=payload,
        extra_headers=extra_headers,
        timeout_seconds=timeout_seconds,
    )


def _trigger_auto_start(base_url: str, autostart: bool) -> None:
    _auto_start_if_needed(base_url, autostart=autostart)


def _module_request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object] | list[object]:
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
approvals_app = build_approvals_app(
    request_json=_module_request_json,
    auto_start_if_needed=_module_auto_start,
    default_base_url=DEFAULT_BASE_URL,
)
triggers_app = build_triggers_app(
    request_json=_trigger_request_json,
    auto_start_if_needed=_trigger_auto_start,
    default_base_url=DEFAULT_BASE_URL,
)
prompts_app = build_prompts_app(
    request_json=_module_request_json,
    auto_start_if_needed=_module_auto_start,
    default_base_url=DEFAULT_BASE_URL,
)
reflection_app = build_reflection_app(
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
        help="Run a single prompt with default settings.",
    ),
) -> None:
    _root_command_impl(ctx, message, run_single_prompt=_run_single_prompt)


def _run_single_prompt(message: str) -> None:
    _run_single_prompt_impl(
        message,
        default_base_url=DEFAULT_BASE_URL,
        execute_prompt=_execute_prompt,
    )


def _execute_prompt(
    *,
    message: str,
    session_id: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    execution_mode: str = "ai",
    autostart: bool = True,
    debug: bool = False,
) -> None:
    _execute_prompt_impl(
        message,
        session_id,
        base_url,
        execution_mode,
        autostart,
        debug,
        auto_start_if_needed=_module_auto_start,
        request_json=_module_request_json,
        stream_events=_stream_events,
    )


app.add_typer(server_app, name="server")
app.add_typer(roles_app, name="roles")
app.add_typer(approvals_app, name="approvals")
app.add_typer(env_app, name="env")
app.add_typer(mcp_app, name="mcp")
app.add_typer(triggers_app, name="triggers")
app.add_typer(prompts_app, name="prompts")
app.add_typer(reflection_app, name="reflection")
app.add_typer(skills_app, name="skills")


def main() -> None:
    app()


def _require_object_response(
    payload: dict[str, object] | list[object],
    path: str,
) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    raise RuntimeError(f"Expected JSON object from {path}")
