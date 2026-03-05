# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
import json
import os
import subprocess
import sys
import time
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import typer

from agent_teams.core.enums import RunEventType
from agent_teams.env.env_cli import env_app

app = typer.Typer(no_args_is_help=False, pretty_exceptions_enable=False)
server_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
roles_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
approvals_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, object] | list[object]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

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
    command = [
        sys.executable,
        "-m",
        "agent_teams",
        "server",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
    ]

    if sys.platform.startswith("win"):
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
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


def _stream_events(base_url: str, run_id: str, debug: bool) -> None:
    request = Request(
        url=f"{base_url.rstrip('/')}/api/runs/{run_id}/events",
        method="GET",
        headers={"Accept": "text/event-stream"},
    )

    with urlopen(request, timeout=600.0) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue

            event = json.loads(payload)
            if "error" in event:
                raise RuntimeError(event["error"])

            if debug:
                typer.echo(json.dumps(event, ensure_ascii=False))
                continue

            event_type = event.get("event_type")
            if event_type == RunEventType.TEXT_DELTA.value:
                event_payload = json.loads(event.get("payload_json", "{}"))
                typer.echo(
                    event_payload.get("text", event_payload.get("content", "")),
                    nl=False,
                )
            if event_type in {
                RunEventType.RUN_COMPLETED.value,
                RunEventType.RUN_FAILED.value,
            }:
                break


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
    if message is not None:
        if ctx.invoked_subcommand is not None:
            raise typer.BadParameter("Cannot combine --message with subcommands")
        _run_single_prompt(message=message)
        return

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


def _run_single_prompt(message: str) -> None:
    normalized_message = message.strip()
    if not normalized_message:
        raise typer.BadParameter("message must not be empty")
    _execute_prompt(
        message=normalized_message,
        session_id=None,
        base_url=DEFAULT_BASE_URL,
        execution_mode="ai",
        autostart=True,
        debug=False,
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
    _auto_start_if_needed(base_url, autostart=autostart)

    if not session_id:
        created_response = _request_json(base_url, "POST", "/api/sessions", {})
        created = _require_object_response(created_response, "/api/sessions")
        session_id = _require_str_field(created, "session_id")

    run_response = _request_json(
        base_url,
        "POST",
        "/api/runs",
        {
            "session_id": session_id,
            "intent": message,
            "execution_mode": execution_mode,
        },
    )
    run = _require_object_response(run_response, "/api/runs")
    run_id = _require_str_field(run, "run_id")

    _stream_events(base_url, run_id, debug=debug)
    if not debug:
        typer.echo()


@server_app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind the server to"),
    port: int = typer.Option(8000, "--port", help="Port to bind the server to"),
    config_dir: str | None = typer.Option(
        None,
        "--config-dir",
        help="Override runtime config directory (default: ./.agent_teams)",
    ),
) -> None:
    if config_dir:
        os.environ["AGENT_TEAMS_CONFIG_DIR"] = config_dir

    uvicorn_module = import_module("uvicorn")
    server_module = import_module("agent_teams.interfaces.server.app")
    fastapi_app = getattr(server_module, "app")
    uvicorn_run = cast(
        Callable[[object, str, int], None], getattr(uvicorn_module, "run")
    )

    typer.echo(f"Starting Agent Teams server on http://{host}:{port}")
    uvicorn_run(fastapi_app, host, port)


@roles_app.command("validate")
def roles_validate(
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
    autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
) -> None:
    _auto_start_if_needed(base_url, autostart=autostart)
    result = _request_json(base_url, "POST", "/api/roles:validate", {})
    typer.echo(json.dumps(result, ensure_ascii=False))


@approvals_app.command("list")
def tool_approvals_list(
    run_id: str = typer.Option(..., "--run-id"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
    autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
) -> None:
    _auto_start_if_needed(base_url, autostart=autostart)
    result = _request_json(base_url, "GET", f"/api/runs/{run_id}/tool-approvals")
    approvals = result if isinstance(result, list) else result.get("data", [])
    typer.echo(json.dumps(approvals, ensure_ascii=False))


@approvals_app.command("resolve")
def tool_approvals_resolve(
    run_id: str = typer.Option(..., "--run-id"),
    tool_call_id: str = typer.Option(..., "--tool-call-id"),
    action: str = typer.Option(..., "--action", help="approve or deny"),
    feedback: str = typer.Option("", "--feedback"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
    autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
) -> None:
    _auto_start_if_needed(base_url, autostart=autostart)
    if action not in {"approve", "deny"}:
        raise typer.BadParameter("action must be approve or deny")
    result = _request_json(
        base_url,
        "POST",
        f"/api/runs/{run_id}/tool-approvals/{tool_call_id}/resolve",
        {"action": action, "feedback": feedback},
    )
    typer.echo(json.dumps(result, ensure_ascii=False))


app.add_typer(server_app, name="server")
app.add_typer(roles_app, name="roles")
app.add_typer(approvals_app, name="approvals")
app.add_typer(env_app, name="env")


def main() -> None:
    app()


def _require_str_field(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str):
        return value
    raise RuntimeError(f"Field '{key}' must be a string")


def _require_object_response(
    payload: dict[str, object] | list[object], path: str
) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    raise RuntimeError(f"Expected JSON object from {path}")
