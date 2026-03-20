# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
import json
from urllib.request import Request, urlopen

import typer

from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.session_models import SessionMode

type RequestJsonCallable = Callable[
    [str, str, str, dict[str, object] | None], dict[str, object] | list[object]
]
type AutoStartCallable = Callable[[str, bool], None]
type StreamEventsCallable = Callable[[str, str, bool], None]
type RunSinglePromptCallable = Callable[[str, bool, SessionMode, str | None], None]
type ExecutePromptCallable = Callable[..., None]

QUICK_PROMPT_OPTIONS_HINT = (
    "Available quick prompt options: --message <text>, "
    "--mode <normal|orchestration>, --orchestration <id>, --yolo/--no-yolo."
)


def root_command(
    ctx: typer.Context,
    message: str | None,
    yolo: bool,
    mode: SessionMode,
    orchestration: str | None,
    *,
    run_single_prompt: RunSinglePromptCallable,
) -> None:
    if message is not None:
        if ctx.invoked_subcommand is not None:
            raise typer.BadParameter("Cannot combine --message with subcommands")
        run_single_prompt(message, yolo, mode, orchestration)
        return

    if mode != SessionMode.NORMAL or orchestration is not None:
        raise typer.BadParameter(
            f"--mode and --orchestration require --message. {QUICK_PROMPT_OPTIONS_HINT}"
        )

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


def run_single_prompt(
    message: str,
    yolo: bool,
    session_mode: SessionMode,
    orchestration_id: str | None,
    *,
    default_base_url: str,
    execute_prompt: ExecutePromptCallable,
) -> None:
    normalized_message = message.strip()
    if not normalized_message:
        raise typer.BadParameter("message must not be empty")
    normalized_orchestration_id = (
        orchestration_id.strip() if orchestration_id is not None else None
    )
    if orchestration_id is not None and not normalized_orchestration_id:
        raise typer.BadParameter(
            f"--orchestration must not be empty. {QUICK_PROMPT_OPTIONS_HINT}"
        )
    if (
        session_mode != SessionMode.ORCHESTRATION
        and normalized_orchestration_id is not None
    ):
        raise typer.BadParameter(
            "--orchestration can only be used with --mode orchestration. "
            + QUICK_PROMPT_OPTIONS_HINT
        )
    execute_prompt(
        message=normalized_message,
        session_id=None,
        base_url=default_base_url,
        execution_mode="ai",
        yolo=yolo,
        session_mode=session_mode,
        orchestration_id=normalized_orchestration_id,
        autostart=True,
        debug=False,
    )


def execute_prompt(
    message: str,
    session_id: str | None,
    base_url: str,
    execution_mode: str,
    yolo: bool,
    session_mode: SessionMode,
    orchestration_id: str | None,
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
        created_response = request_json(
            base_url,
            "POST",
            "/api/sessions",
            {"workspace_id": "default"},
        )
        created = _require_object_response(created_response, "/api/sessions")
        resolved_session_id = _require_str_field(created, "session_id")

    if session_mode == SessionMode.ORCHESTRATION:
        _configure_orchestration_mode(
            base_url=base_url,
            session_id=resolved_session_id,
            orchestration_id=orchestration_id,
            request_json=request_json,
        )

    run_response = request_json(
        base_url,
        "POST",
        "/api/runs",
        {
            "session_id": resolved_session_id,
            "intent": message,
            "execution_mode": execution_mode,
            "yolo": yolo,
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


def _configure_orchestration_mode(
    *,
    base_url: str,
    session_id: str,
    orchestration_id: str | None,
    request_json: RequestJsonCallable,
) -> None:
    try:
        _ = request_json(
            base_url,
            "PATCH",
            f"/api/sessions/{session_id}/topology",
            {
                "session_mode": SessionMode.ORCHESTRATION.value,
                "orchestration_preset_id": orchestration_id,
            },
        )
    except RuntimeError as exc:
        raise _translate_orchestration_error(
            base_url=base_url,
            orchestration_id=orchestration_id,
            request_json=request_json,
            error=exc,
        ) from exc


def _translate_orchestration_error(
    *,
    base_url: str,
    orchestration_id: str | None,
    request_json: RequestJsonCallable,
    error: RuntimeError,
) -> Exception:
    message = str(error)
    if "Unknown orchestration preset:" in message:
        requested_id = orchestration_id or "<default>"
        return typer.BadParameter(
            f"Invalid --orchestration '{requested_id}'. "
            f"{_available_orchestration_ids_hint(base_url, request_json)}"
        )
    if "No orchestration preset configured" in message:
        return typer.BadParameter(
            "--mode orchestration requires an available orchestration id. "
            f"{_available_orchestration_ids_hint(base_url, request_json)}"
        )
    if "Session mode can no longer be changed" in message:
        return typer.BadParameter(
            "The target session has already started and its mode can no longer be changed."
        )
    return RuntimeError(
        "Failed to configure orchestration mode before starting the run. " + message
    )


def _available_orchestration_ids_hint(
    base_url: str,
    request_json: RequestJsonCallable,
) -> str:
    try:
        response = request_json(
            base_url,
            "GET",
            "/api/system/configs/orchestration",
            None,
        )
    except RuntimeError as exc:
        return (
            "Could not list available orchestration ids from "
            f"/api/system/configs/orchestration: {exc}"
        )
    payload = _require_object_response(response, "/api/system/configs/orchestration")
    presets = payload.get("presets")
    if not isinstance(presets, list):
        return "No orchestration ids are currently configured."
    preset_ids: list[str] = []
    for item in presets:
        if not isinstance(item, dict):
            continue
        preset_id = item.get("preset_id")
        if isinstance(preset_id, str) and preset_id.strip():
            preset_ids.append(preset_id.strip())
    if not preset_ids:
        return "No orchestration ids are currently configured."
    return "Available orchestration ids: " + ", ".join(preset_ids) + "."
