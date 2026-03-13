# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
import json
from urllib.request import Request, urlopen

import typer

from agent_teams.sessions.runs.enums import RunEventType

type RequestJsonCallable = Callable[
    [str, str, str, dict[str, object] | None], dict[str, object] | list[object]
]
type AutoStartCallable = Callable[[str, bool], None]
type StreamEventsCallable = Callable[[str, str, bool], None]
type RunSinglePromptCallable = Callable[[str], None]
type ExecutePromptCallable = Callable[..., None]


def root_command(
    ctx: typer.Context,
    message: str | None,
    *,
    run_single_prompt: RunSinglePromptCallable,
) -> None:
    if message is not None:
        if ctx.invoked_subcommand is not None:
            raise typer.BadParameter("Cannot combine --message with subcommands")
        run_single_prompt(message)
        return

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


def run_single_prompt(
    message: str,
    *,
    default_base_url: str,
    execute_prompt: ExecutePromptCallable,
) -> None:
    normalized_message = message.strip()
    if not normalized_message:
        raise typer.BadParameter("message must not be empty")
    execute_prompt(
        message=normalized_message,
        session_id=None,
        base_url=default_base_url,
        execution_mode="ai",
        autostart=True,
        debug=False,
    )


def execute_prompt(
    message: str,
    session_id: str | None,
    base_url: str,
    execution_mode: str,
    autostart: bool,
    debug: bool,
    *,
    auto_start_if_needed: AutoStartCallable,
    request_json: RequestJsonCallable,
    stream_events: StreamEventsCallable,
) -> None:
    auto_start_if_needed(base_url, autostart)

    resolved_session_id = session_id
    if not resolved_session_id:
        created_response = request_json(base_url, "POST", "/api/sessions", {})
        created = _require_object_response(created_response, "/api/sessions")
        resolved_session_id = _require_str_field(created, "session_id")

    run_response = request_json(
        base_url,
        "POST",
        "/api/runs",
        {
            "session_id": resolved_session_id,
            "intent": message,
            "execution_mode": execution_mode,
        },
    )
    run = _require_object_response(run_response, "/api/runs")
    run_id = _require_str_field(run, "run_id")

    stream_events(base_url, run_id, debug)
    if not debug:
        typer.echo()


def stream_events(base_url: str, run_id: str, debug: bool) -> None:
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
                raise RuntimeError(str(event["error"]))

            if debug:
                typer.echo(json.dumps(event, ensure_ascii=False))
                continue

            event_type = event.get("event_type")
            if event_type == RunEventType.TEXT_DELTA.value:
                event_payload = json.loads(str(event.get("payload_json", "{}")))
                if not isinstance(event_payload, dict):
                    event_payload = {}
                text = event_payload.get("text", event_payload.get("content", ""))
                typer.echo(str(text), nl=False)
            if event_type in {
                RunEventType.RUN_COMPLETED.value,
                RunEventType.RUN_FAILED.value,
            }:
                break


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
