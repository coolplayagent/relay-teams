from __future__ import annotations

import json
import time

import httpx
from pydantic import BaseModel, ConfigDict
import pytest

from integration_tests.support.api_helpers import (
    create_run,
    create_session,
    new_session_id,
    stream_run_until_terminal,
)


class _BenchmarkResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    duration_seconds: float
    terminal_event_type: str
    completed_tasks: int
    output_text: str


def test_orchestration_parallel_same_role_clone_completes_via_api(
    api_client: httpx.Client,
) -> None:
    result = _run_orchestration_clone_benchmark(
        api_client,
        mode="parallel",
        task_count=3,
        delay_ms=50,
    )

    assert result.terminal_event_type == "run_completed"
    assert result.completed_tasks == 3
    assert "[fake-llm] orchestration clone benchmark completed" in str(
        result.output_text
    )


@pytest.mark.timeout(90)
def test_orchestration_parallel_clone_endpoint_is_faster_than_serial(
    api_client: httpx.Client,
) -> None:
    serial = _run_orchestration_clone_benchmark(
        api_client,
        mode="serial",
        task_count=5,
        delay_ms=180,
    )
    parallel = _run_orchestration_clone_benchmark(
        api_client,
        mode="parallel",
        task_count=5,
        delay_ms=180,
    )
    summary = json.dumps(
        {
            "serial_seconds": serial.duration_seconds,
            "parallel_seconds": parallel.duration_seconds,
        },
        sort_keys=True,
    )

    assert serial.terminal_event_type == "run_completed"
    assert parallel.terminal_event_type == "run_completed"
    assert serial.completed_tasks == 5
    assert parallel.completed_tasks == 5
    assert parallel.duration_seconds <= serial.duration_seconds * 0.85, summary


def _run_orchestration_clone_benchmark(
    client: httpx.Client,
    *,
    mode: str,
    task_count: int,
    delay_ms: int,
) -> _BenchmarkResult:
    session_id = create_session(
        client,
        session_id=new_session_id(f"orch-clone-{mode}"),
    )
    topology_response = client.patch(
        f"/api/sessions/{session_id}/topology",
        json={"session_mode": "orchestration"},
    )
    topology_response.raise_for_status()
    run_id = create_run(
        client,
        session_id=session_id,
        execution_mode="ai",
        yolo=True,
        intent=(
            f"[orch-clone-bench {mode} count={task_count} delay={delay_ms}] "
            "Create and dispatch same-role benchmark tasks."
        ),
    )
    started_at = time.perf_counter()
    events = stream_run_until_terminal(client, run_id=run_id, timeout_seconds=120.0)
    duration_seconds = time.perf_counter() - started_at
    terminal_event = events[-1]
    tasks_response = client.get(f"/api/tasks/runs/{run_id}")
    tasks_response.raise_for_status()
    task_items = tasks_response.json().get("tasks")
    if not isinstance(task_items, list):
        raise AssertionError(f"Invalid task list response: {tasks_response.json()}")
    completed_tasks = [
        task
        for task in task_items
        if isinstance(task, dict) and task.get("status") == "completed"
    ]
    return _BenchmarkResult(
        duration_seconds=duration_seconds,
        terminal_event_type=str(terminal_event.get("event_type") or ""),
        completed_tasks=len(completed_tasks),
        output_text=_text_output(events),
    )


def _text_output(events: list[dict[str, object]]) -> str:
    parts: list[str] = []
    for event in events:
        if str(event.get("event_type") or "") != "text_delta":
            continue
        payload = json.loads(str(event.get("payload_json") or "{}"))
        parts.append(str(payload.get("text") or ""))
    return "".join(parts)
