# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from pathlib import Path

import httpx
import typer

from relay_teams.env import load_proxy_env_config
from relay_teams.net.clients import create_async_http_client
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.session_models import SessionMode

type RequestJsonCallable = Callable[
    [str, str, str, dict[str, object] | None], dict[str, object] | list[object]
]
type AutoStartCallable = Callable[[str, bool], None]
type StreamEventsCallable = Callable[[str, str, bool], None]
type RunSinglePromptCallable = Callable[
    [str, bool, SessionMode, str | None, str | None, Path | None], None
]
type ExecutePromptCallable = Callable[..., None]

QUICK_PROMPT_OPTIONS_HINT = (
    "Available quick prompt options: --message <text>, "
    "--mode <normal|orchestration>, --role <role_id>, "
    "--orchestration <id>, --workspace <path> (defaults to current directory), "
    "--yolo/--no-yolo."
)


def root_command(
    ctx: typer.Context,
    message: str | None,
    yolo: bool,
    mode: SessionMode,
    role: str | None,
    orchestration: str | None,
    workspace: Path | None,
    *,
    run_single_prompt: RunSinglePromptCallable,
) -> None:
    if message is not None:
        if ctx.invoked_subcommand is not None:
            raise typer.BadParameter("Cannot combine --message with subcommands")
        run_single_prompt(message, yolo, mode, role, orchestration, workspace)
        return

    if (
        mode != SessionMode.NORMAL
        or role is not None
        or orchestration is not None
        or workspace is not None
    ):
        raise typer.BadParameter(
            "--mode, --role, --orchestration, and --workspace require --message. "
            f"{QUICK_PROMPT_OPTIONS_HINT}"
        )

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


def run_single_prompt(
    message: str,
    yolo: bool,
    session_mode: SessionMode,
    role_id: str | None,
    orchestration_id: str | None,
    workspace: Path | None,
    *,
    default_base_url: str,
    execute_prompt: ExecutePromptCallable,
) -> None:
    normalized_message = message.strip()
    if not normalized_message:
        raise typer.BadParameter("message must not be empty")
    normalized_role_id = role_id.strip() if role_id is not None else None
    normalized_orchestration_id = (
        orchestration_id.strip() if orchestration_id is not None else None
    )
    if role_id is not None and not normalized_role_id:
        raise typer.BadParameter(
            f"--role must not be empty. {QUICK_PROMPT_OPTIONS_HINT}"
        )
    if orchestration_id is not None and not normalized_orchestration_id:
        raise typer.BadParameter(
            f"--orchestration must not be empty. {QUICK_PROMPT_OPTIONS_HINT}"
        )
    if session_mode == SessionMode.ORCHESTRATION and normalized_role_id is not None:
        raise typer.BadParameter(
            "--role can only be used with --mode normal. " + QUICK_PROMPT_OPTIONS_HINT
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
        normal_root_role_id=normalized_role_id,
        orchestration_id=normalized_orchestration_id,
        workspace=workspace,
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
    normal_root_role_id: str | None,
    orchestration_id: str | None,
    workspace: Path | None,
    autostart: bool,
    debug: bool,
    *,
    auto_start_if_needed: AutoStartCallable,
    request_json: RequestJsonCallable,
    stream_events: StreamEventsCallable,
) -> None:
    auto_start_if_needed(base_url, autostart)

    workspace_id = _resolve_workspace_id(
        base_url=base_url,
        workspace=workspace,
        request_json=request_json,
    )
    resolved_session_id = session_id
    if not resolved_session_id:
        created_response = request_json(
            base_url,
            "POST",
            "/api/sessions",
            {"workspace_id": workspace_id},
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
    elif normal_root_role_id is not None:
        _configure_normal_mode_role(
            base_url=base_url,
            session_id=resolved_session_id,
            role_id=normal_root_role_id,
            request_json=request_json,
        )

    run_response = request_json(
        base_url,
        "POST",
        "/api/runs",
        {
            "session_id": resolved_session_id,
            "input": [{"kind": "text", "text": message}],
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
    asyncio.run(
        stream_events_async(
            base_url=base_url,
            run_id=run_id,
            debug=debug,
        )
    )


async def stream_events_async(base_url: str, run_id: str, debug: bool) -> None:
    try:
        async with create_async_http_client(
            proxy_config=load_proxy_env_config(),
            timeout_seconds=600.0,
        ) as client:
            async with client.stream(
                "GET",
                f"{base_url.rstrip('/')}/api/runs/{run_id}/events",
                headers={"Accept": "text/event-stream"},
            ) as response:
                response.raise_for_status()
                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if _handle_stream_line(line, debug=debug):
                        return
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"HTTP {exc.response.status_code} while streaming run {run_id}: "
            f"{exc.response.text}"
        ) from exc
    except httpx.RequestError as exc:
        raise RuntimeError(f"Failed to stream run {run_id}: {exc}") from exc


def _handle_stream_line(line: str, *, debug: bool) -> bool:
    if not line or not line.startswith("data:"):
        return False
    payload = line[5:].strip()
    if not payload:
        return False

    event = json.loads(payload)
    if "error" in event:
        raise RuntimeError(str(event["error"]))

    if debug:
        typer.echo(json.dumps(event, ensure_ascii=False))
        return False

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
        return True
    return False


def _resolve_workspace_id(
    *,
    base_url: str,
    workspace: Path | None,
    request_json: RequestJsonCallable,
) -> str:
    resolved_workspace = (
        Path.cwd().resolve() if workspace is None else workspace.expanduser().resolve()
    )
    response = request_json(
        base_url,
        "POST",
        "/api/workspaces/pick",
        {"root_path": str(resolved_workspace)},
    )
    payload = _require_object_response(response, "/api/workspaces/pick")
    workspace_payload = payload.get("workspace")
    if not isinstance(workspace_payload, dict):
        raise RuntimeError("Expected workspace details from /api/workspaces/pick")
    return _require_str_field(workspace_payload, "workspace_id")


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


def _configure_normal_mode_role(
    *,
    base_url: str,
    session_id: str,
    role_id: str,
    request_json: RequestJsonCallable,
) -> None:
    try:
        _ = request_json(
            base_url,
            "PATCH",
            f"/api/sessions/{session_id}/topology",
            {
                "session_mode": SessionMode.NORMAL.value,
                "normal_root_role_id": role_id,
                "orchestration_preset_id": None,
            },
        )
    except RuntimeError as exc:
        raise _translate_normal_mode_role_error(
            base_url=base_url,
            role_id=role_id,
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


def _translate_normal_mode_role_error(
    *,
    base_url: str,
    role_id: str,
    request_json: RequestJsonCallable,
    error: RuntimeError,
) -> Exception:
    message = str(error)
    if (
        "Unknown normal mode role:" in message
        or "Coordinator role cannot be used in normal mode:" in message
        or "Reserved system role cannot be used in normal mode:" in message
    ):
        return typer.BadParameter(
            f"Invalid --role '{role_id}'. "
            f"{_available_normal_role_ids_hint(base_url, request_json)}"
        )
    if "Session mode can no longer be changed" in message:
        return typer.BadParameter(
            "The target session has already started and its topology can no longer be changed."
        )
    return RuntimeError(
        "Failed to configure normal mode role before starting the run. " + message
    )


def _available_normal_role_ids_hint(
    base_url: str,
    request_json: RequestJsonCallable,
) -> str:
    try:
        response = request_json(
            base_url,
            "GET",
            "/api/roles:options",
            None,
        )
    except RuntimeError as exc:
        return f"Could not list available role ids from /api/roles:options: {exc}"
    payload = _require_object_response(response, "/api/roles:options")
    role_entries = payload.get("normal_mode_roles")
    if not isinstance(role_entries, list):
        return "No normal mode roles are currently configured."
    role_ids: list[str] = []
    for item in role_entries:
        if not isinstance(item, dict):
            continue
        candidate = item.get("role_id")
        if isinstance(candidate, str) and candidate.strip():
            role_ids.append(candidate.strip())
    if not role_ids:
        return "No normal mode roles are currently configured."
    return "Available normal mode roles: " + ", ".join(role_ids) + "."
