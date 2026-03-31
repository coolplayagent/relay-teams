from __future__ import annotations

import json
from uuid import uuid4

import httpx

TERMINAL_EVENT_TYPES = {"run_completed", "run_failed", "run_stopped"}


def new_session_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def create_session(client: httpx.Client, *, session_id: str | None = None) -> str:
    payload: dict[str, object] = {"workspace_id": "default"}
    if session_id:
        payload["session_id"] = session_id
    response = client.post("/api/sessions", json=payload)
    response.raise_for_status()
    body = response.json()
    result = body.get("session_id")
    if not isinstance(result, str) or not result:
        raise AssertionError(f"Invalid session response: {body}")
    return result


def create_run(
    client: httpx.Client,
    *,
    session_id: str,
    intent: str,
    execution_mode: str,
    yolo: bool = False,
) -> str:
    response = client.post(
        "/api/runs",
        json={
            "session_id": session_id,
            "input": [{"kind": "text", "text": intent}],
            "execution_mode": execution_mode,
            "yolo": yolo,
        },
    )
    response.raise_for_status()
    body = response.json()
    result = body.get("run_id")
    if not isinstance(result, str) or not result:
        raise AssertionError(f"Invalid run response: {body}")
    return result


def stream_run_until_terminal(
    client: httpx.Client, *, run_id: str, timeout_seconds: float = 40.0
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    with client.stream(
        "GET",
        f"/api/runs/{run_id}/events",
        timeout=timeout_seconds,
    ) as response:
        response.raise_for_status()
        for raw_line in response.iter_lines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            event = json.loads(payload)
            if not isinstance(event, dict):
                continue
            if "error" in event:
                raise AssertionError(f"Run stream returned error: {event['error']}")
            events.append(event)
            event_type = event.get("event_type")
            if isinstance(event_type, str) and event_type in TERMINAL_EVENT_TYPES:
                return events
    raise AssertionError(f"Stream ended without terminal event for run_id={run_id}")


def create_task_batch(
    client: httpx.Client,
    *,
    run_id: str,
    objective: str,
) -> dict[str, object]:
    response = client.post(
        f"/api/tasks/runs/{run_id}",
        json={
            "tasks": [
                {
                    "title": "first_time_query",
                    "objective": f"{objective}: return the current time for the first task.",
                },
                {
                    "title": "second_time_query",
                    "objective": f"{objective}: return the current time for the second task.",
                },
            ],
        },
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise AssertionError(f"Invalid task creation response: {body}")
    return body


def get_coordinator_role_id(client: httpx.Client) -> str:
    response = client.get("/api/roles:options")
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise AssertionError(f"Invalid role options response: {body}")
    role_id = body.get("coordinator_role_id")
    if not isinstance(role_id, str) or not role_id:
        raise AssertionError(f"Missing coordinator_role_id in response: {body}")
    return role_id


def dispatch_task(
    client: httpx.Client,
    *,
    task_id: str,
    role_id: str,
    prompt: str = "",
) -> dict[str, object]:
    response = client.post(
        f"/api/tasks/{task_id}/dispatch",
        json={"role_id": role_id, "prompt": prompt},
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise AssertionError(f"Invalid dispatch response: {body}")
    return body
