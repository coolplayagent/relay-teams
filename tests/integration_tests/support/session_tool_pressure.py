from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event
import json
import time

import httpx
from pydantic import BaseModel, ConfigDict, Field

from integration_tests.support.api_helpers import (
    create_run,
    stream_run_until_terminal,
)
from integration_tests.support.environment import IntegrationEnvironment


class RunPressureResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    run_id: str
    duration_ms: int
    terminal_event_type: str
    event_counts: dict[str, int]
    output_text: str


class BackendProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    status_code: int
    duration_ms: int
    error: str = ""


class PressureScenarioResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    runs: tuple[RunPressureResult, ...]
    probes: tuple[BackendProbeResult, ...] = Field(default_factory=tuple)


def run_pressure_scenario(
    *,
    integration_env: IntegrationEnvironment,
    session_ids: list[str],
    intent_template: str,
    timeout_seconds: float,
) -> PressureScenarioResult:
    stop_probes = Event()
    probe_results: list[BackendProbeResult] = []
    started_at = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(session_ids) + 2) as executor:
        probe_future = executor.submit(
            _probe_backend_until_stopped,
            integration_env.api_base_url,
            tuple(session_ids),
            stop_probes,
            probe_results,
        )
        futures = [
            executor.submit(
                _run_single_pressure_session,
                integration_env.api_base_url,
                session_id,
                intent_template.format(index=index + 1),
                timeout_seconds,
            )
            for index, session_id in enumerate(session_ids)
        ]
        run_results: list[RunPressureResult] = []
        try:
            remaining_timeout = max(
                1.0,
                timeout_seconds - (time.monotonic() - started_at),
            )
            for future in as_completed(futures, timeout=remaining_timeout):
                run_results.append(future.result())
        finally:
            stop_probes.set()
            probe_future.result(timeout=10.0)

    if len(run_results) != len(session_ids):
        raise AssertionError(
            f"Expected {len(session_ids)} completed pressure runs, got {len(run_results)}"
        )
    return PressureScenarioResult(
        runs=tuple(run_results),
        probes=tuple(probe_results),
    )


def assert_backend_probes_stayed_responsive(
    probes: tuple[BackendProbeResult, ...],
) -> None:
    assert len(probes) >= 10
    failures = [
        probe
        for probe in probes
        if probe.status_code == 0
        or probe.status_code >= 500
        or probe.status_code == 429
    ]
    assert failures == []
    live_durations = sorted(
        probe.duration_ms for probe in probes if probe.path == "/api/system/live"
    )
    assert live_durations
    assert live_durations[-1] < 1500
    durations = sorted(probe.duration_ms for probe in probes)
    p95_index = max(0, int(len(durations) * 0.95) - 1)
    assert durations[p95_index] < 1500


def _run_single_pressure_session(
    base_url: str,
    session_id: str,
    intent: str,
    timeout_seconds: float,
) -> RunPressureResult:
    with httpx.Client(
        base_url=base_url, timeout=timeout_seconds, trust_env=False
    ) as client:
        started_at = time.perf_counter()
        run_id = create_run(
            client,
            session_id=session_id,
            intent=intent,
            execution_mode="ai",
            yolo=True,
        )
        events = stream_run_until_terminal(
            client,
            run_id=run_id,
            timeout_seconds=timeout_seconds,
        )
        return RunPressureResult(
            session_id=session_id,
            run_id=run_id,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            terminal_event_type=str(events[-1].get("event_type") or ""),
            event_counts=_event_counts(events),
            output_text=_text_output(events),
        )


def _probe_backend_until_stopped(
    base_url: str,
    session_ids: tuple[str, ...],
    stop_event: Event,
    results: list[BackendProbeResult],
) -> None:
    paths = _probe_paths(session_ids)
    index = 0
    with httpx.Client(base_url=base_url, timeout=1.5, trust_env=False) as client:
        while not stop_event.is_set():
            path = paths[index % len(paths)]
            index += 1
            results.append(_send_backend_probe(client, path))
            time.sleep(0.03)


def _probe_paths(session_ids: tuple[str, ...]) -> tuple[str, ...]:
    paths = ["/api/system/live", "/api/system/health", "/api/sessions"]
    for session_id in session_ids[:6]:
        paths.extend(
            [
                f"/api/sessions/{session_id}",
                f"/api/sessions/{session_id}/rounds?limit=4",
                f"/api/sessions/{session_id}/recovery",
                f"/api/sessions/{session_id}/token-usage",
            ]
        )
    return tuple(paths)


def _send_backend_probe(
    client: httpx.Client,
    path: str,
) -> BackendProbeResult:
    started_at = time.perf_counter()
    try:
        response = client.get(path)
        _consume_response(response)
        return BackendProbeResult(
            path=path,
            status_code=response.status_code,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
        )
    except Exception as exc:
        return BackendProbeResult(
            path=path,
            status_code=0,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            error=f"{type(exc).__name__}: {exc}",
        )


def _consume_response(response: httpx.Response) -> None:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        _ = response.json()
        return
    _ = response.text


def _event_counts(events: list[dict[str, object]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for event in events:
        event_type = str(event.get("event_type") or "")
        if event_type:
            counts[event_type] += 1
    return dict(counts)


def _text_output(events: list[dict[str, object]]) -> str:
    parts: list[str] = []
    for event in events:
        if str(event.get("event_type") or "") != "text_delta":
            continue
        payload = json.loads(str(event.get("payload_json") or "{}"))
        parts.append(str(payload.get("text") or ""))
    return "".join(parts)
