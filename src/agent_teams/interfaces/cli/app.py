# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import typer

from agent_teams.core.enums import RunEventType
from agent_teams.paths import get_project_root

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)


DEFAULT_CONFIG_DIR = get_project_root() / ".agent_teams"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, object]:
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
            return {"data": data}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code} {method} {path}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to connect to {base_url}: {exc}") from exc


def _is_server_healthy(base_url: str) -> bool:
    try:
        health = _request_json(
            base_url, "GET", "/api/system/health", timeout_seconds=1.5
        )
        return health.get("status") == "ok"
    except Exception:
        return False


def _start_server_daemon(host: str, port: int) -> None:
    command = [
        sys.executable,
        "-m",
        "agent_teams",
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


@app.command("prompt")
def prompt(
    message: str = typer.Option(..., "-m", "--message"),
    session_id: str | None = typer.Option(None, "--session-id"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
    execution_mode: str = typer.Option("ai", "--mode"),
    autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    _auto_start_if_needed(base_url, autostart=autostart)

    if not session_id:
        created = _request_json(base_url, "POST", "/api/sessions", {})
        session_id = _require_str_field(created, "session_id")

    run = _request_json(
        base_url,
        "POST",
        "/api/runs",
        {
            "session_id": session_id,
            "intent": message,
            "execution_mode": execution_mode,
        },
    )
    run_id = _require_str_field(run, "run_id")

    _stream_events(base_url, run_id, debug=debug)
    if not debug:
        typer.echo()


@app.command("run-intent")
def run_intent(
    intent: str = typer.Option(..., "--intent"),
    session_id: str | None = typer.Option(None, "--session-id"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
    autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    prompt(
        message=intent,
        session_id=session_id,
        base_url=base_url,
        execution_mode="ai",
        autostart=autostart,
        debug=debug,
    )


@app.command("run-intent-stream")
def run_intent_stream(
    intent: str = typer.Option(..., "--intent"),
    session_id: str | None = typer.Option(None, "--session-id"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
    autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    prompt(
        message=intent,
        session_id=session_id,
        base_url=base_url,
        execution_mode="ai",
        autostart=autostart,
        debug=debug,
    )


@app.command("chat")
def chat(
    session_id: str | None = typer.Option(None, "--session-id"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
    autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    _auto_start_if_needed(base_url, autostart=autostart)
    if not session_id:
        created = _request_json(base_url, "POST", "/api/sessions", {})
        session_id = _require_str_field(created, "session_id")

    typer.echo(
        f"Starting interactive chat (Session: {session_id}). Type 'exit' or 'quit' to stop."
    )

    while True:
        try:
            text = typer.prompt("Prompt")
        except typer.Abort:
            typer.echo()
            break

        if text.strip().lower() in {"exit", "quit"}:
            break
        if not text.strip():
            continue

        prompt(
            message=text,
            session_id=session_id,
            base_url=base_url,
            execution_mode="ai",
            autostart=autostart,
            debug=debug,
        )


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind the server to"),
    port: int = typer.Option(8000, "--port", help="Port to bind the server to"),
    config_dir: str | None = typer.Option(
        None,
        "--config-dir",
        help="Override runtime config directory (default: ./.agent_teams)",
    ),
) -> None:
    import uvicorn

    if config_dir:
        os.environ["AGENT_TEAMS_CONFIG_DIR"] = config_dir
    from agent_teams.interfaces.server.app import app as fastapi_app

    typer.echo(f"Starting Agent Teams server on http://{host}:{port}")
    uvicorn.run(fastapi_app, host=host, port=port)


@app.command("roles-validate")
def roles_validate(
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
    autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
) -> None:
    _auto_start_if_needed(base_url, autostart=autostart)
    result = _request_json(base_url, "POST", "/api/roles:validate", {})
    typer.echo(json.dumps(result, ensure_ascii=False))


@app.command("tool-approvals-list")
def tool_approvals_list(
    run_id: str = typer.Option(..., "--run-id"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
    autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
) -> None:
    _auto_start_if_needed(base_url, autostart=autostart)
    result = _request_json(base_url, "GET", f"/api/runs/{run_id}/tool-approvals")
    approvals = result if isinstance(result, list) else result.get("data", [])
    typer.echo(json.dumps(approvals, ensure_ascii=False))


@app.command("tool-approvals-resolve")
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


def main() -> None:
    app()


def _require_str_field(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str):
        return value
    raise RuntimeError(f"Field '{key}' must be a string")
