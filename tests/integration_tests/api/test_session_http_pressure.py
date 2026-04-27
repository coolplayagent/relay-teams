from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import time
from typing import Literal, TypedDict

import httpx
from relay_teams.sessions.runs.enums import RunEventType

from integration_tests.support.api_helpers import (
    create_run,
    create_session,
    new_session_id,
    stream_run_until_terminal,
)
from integration_tests.support.environment import IntegrationEnvironment


HttpMethod = Literal["GET", "POST"]
RUN_EVENT_TYPE_VALUES = {event_type.value for event_type in RunEventType}


class PressureResult(TypedDict):
    method: str
    path: str
    status_code: int
    duration_ms: int


def test_session_switch_pressure_does_not_starve_backend(
    api_client: httpx.Client,
    integration_env: IntegrationEnvironment,
) -> None:
    session_ids = [
        create_session(
            api_client,
            session_id=new_session_id(f"pressure-session-{index:02d}"),
        )
        for index in range(24)
    ]
    subagent_session_ids: list[str] = []
    for index, session_id in enumerate(session_ids[1:4], start=1):
        run_id = create_run(
            api_client,
            session_id=session_id,
            intent=(
                "[hook-subagent-lifecycle] spawn one synchronous subagent and "
                f"finish pressure seed {index}"
            ),
            execution_mode="ai",
        )
        events = stream_run_until_terminal(
            api_client,
            run_id=run_id,
            timeout_seconds=80.0,
        )
        assert events[-1]["event_type"] == "run_completed"
        subagent_session_ids.append(session_id)

    create_started = time.perf_counter()
    slow_run_id = create_run(
        api_client,
        session_id=session_ids[0],
        intent="[slow-stream] keep one run active while session reads are stressed.",
        execution_mode="ai",
    )
    create_elapsed_ms = int((time.perf_counter() - create_started) * 1000)
    assert create_elapsed_ms < 3000

    request_plan = _build_pressure_request_plan(session_ids)
    results = _run_pressure_requests(
        integration_env.api_base_url,
        request_plan,
        workers=16,
    )
    failures = [
        result
        for result in results
        if result["status_code"] >= 500 or result["status_code"] == 429
    ]
    assert failures == []

    durations = sorted(result["duration_ms"] for result in results)
    p95_duration = durations[int(len(durations) * 0.95) - 1]
    assert p95_duration < 6000
    assert durations[-1] < 10000

    health_response = api_client.get("/api/system/health")
    health_response.raise_for_status()
    assert health_response.json()["status"] == "ok"

    slow_events = stream_run_until_terminal(
        api_client,
        run_id=slow_run_id,
        timeout_seconds=40.0,
    )
    assert slow_events[-1]["event_type"] == "run_completed"

    replay_session_id = subagent_session_ids[0]
    expected_replay_count = _subagent_event_count(api_client, replay_session_id)
    assert expected_replay_count > 0
    replayed_events = _stream_subagent_event_replay(
        api_client,
        session_id=replay_session_id,
        expected_count=expected_replay_count,
    )
    assert len(replayed_events) == min(expected_replay_count, 12)
    assert all(
        str(event.get("run_id") or "").startswith("subagent_run_")
        for event in replayed_events
    )


def _build_pressure_request_plan(
    session_ids: list[str],
) -> list[tuple[HttpMethod, str]]:
    request_plan: list[tuple[HttpMethod, str]] = [("GET", "/api/sessions")]
    for session_id in session_ids:
        request_plan.extend(
            [
                ("GET", f"/api/sessions/{session_id}"),
                ("GET", f"/api/sessions/{session_id}/rounds?limit=8"),
                ("GET", f"/api/sessions/{session_id}/recovery"),
                ("GET", f"/api/sessions/{session_id}/token-usage"),
                ("GET", f"/api/sessions/{session_id}/agents"),
                ("GET", f"/api/sessions/{session_id}/tasks"),
                ("GET", f"/api/sessions/{session_id}/subagents"),
                ("POST", f"/api/sessions/{session_id}/terminal-view"),
            ]
        )
    return request_plan


def _run_pressure_requests(
    base_url: str,
    request_plan: list[tuple[HttpMethod, str]],
    *,
    workers: int,
) -> list[PressureResult]:
    results: list[PressureResult] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(_send_pressure_request, base_url, method, path)
            for method, path in request_plan
        ]
        for future in as_completed(futures, timeout=60.0):
            results.append(future.result())
    assert len(results) == len(request_plan)
    return results


def _send_pressure_request(
    base_url: str,
    method: HttpMethod,
    path: str,
) -> PressureResult:
    with httpx.Client(base_url=base_url, timeout=15.0, trust_env=False) as client:
        started = time.perf_counter()
        if method == "POST":
            response = client.post(path)
        else:
            response = client.get(path)
        _consume_response(response)
        return {
            "method": method,
            "path": path,
            "status_code": response.status_code,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }


def _consume_response(response: httpx.Response) -> None:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        _ = response.json()
        return
    _ = response.text


def _subagent_event_count(client: httpx.Client, session_id: str) -> int:
    response = client.get(f"/api/sessions/{session_id}/events")
    response.raise_for_status()
    events = response.json()
    assert isinstance(events, list)
    return sum(
        1
        for event in events
        if isinstance(event, dict)
        and str(event.get("trace_id") or "").startswith("subagent_run_")
        and str(event.get("event_type") or "") in RUN_EVENT_TYPE_VALUES
    )


def _stream_subagent_event_replay(
    client: httpx.Client,
    *,
    session_id: str,
    expected_count: int,
) -> list[dict[str, object]]:
    target_count = min(expected_count, 12)
    events: list[dict[str, object]] = []
    with client.stream(
        "GET",
        f"/api/sessions/{session_id}/subagents/events?after_event_id=0",
        timeout=10.0,
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
            assert isinstance(event, dict)
            assert "error" not in event
            events.append(event)
            if len(events) >= target_count:
                break
    return events
