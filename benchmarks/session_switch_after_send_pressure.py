from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
    as_completed,
)
from datetime import UTC, datetime
import argparse
import json
from pathlib import Path
import sys
from threading import Event, Lock
import time

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _PYTHONPATH_ENTRY in (_REPO_ROOT, _REPO_ROOT / "src", _REPO_ROOT / "tests"):
    _PYTHONPATH_TEXT = str(_PYTHONPATH_ENTRY)
    if _PYTHONPATH_TEXT not in sys.path:
        sys.path.insert(0, _PYTHONPATH_TEXT)

import httpx  # noqa: E402
from pydantic import BaseModel, ConfigDict  # noqa: E402

from benchmarks.extreme_session_pressure import (  # noqa: E402
    _apply_home_env,
    _build_environment,
    _capture_home_env,
    _load_fake_llm_metrics,
    _parse_backend_log_metrics,
    _percentile,
    _restore_home_env,
    _start_backend,
    _start_fake_llm,
)
from tests.integration_tests.support.api_helpers import (  # noqa: E402
    create_session,
    new_session_id,
)
from tests.integration_tests.support.config_builder import (  # noqa: E402
    assert_integration_model_config_uses_fake_llm,
    write_test_runtime_config,
)
from tests.integration_tests.support.process_control import (  # noqa: E402
    ManagedProcess,
    find_free_ports,
    stop_process,
    wait_for_http_ready,
)

RECOVERABLE_SWITCH_BENCHMARK_ERRORS = (
    AssertionError,
    RuntimeError,
    ValueError,
    FutureTimeoutError,
    httpx.HTTPError,
)

_HTTP_TIMEOUT = httpx.Timeout(8.0, connect=2.0, read=8.0, write=2.0, pool=2.0)
_SEND_TIMEOUT = httpx.Timeout(20.0, connect=3.0, read=20.0, write=3.0, pool=3.0)


class RequestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    method: str
    path: str
    status_code: int
    duration_ms: int
    source_session_id: str = ""
    target_session_id: str = ""
    run_id: str = ""
    error: str = ""


class LatencySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int
    failure_count: int
    p50_ms: int
    p95_ms: int
    p99_ms: int
    max_ms: int
    by_path: dict[str, "LatencySummary"]


class SwitchBenchmarkSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    started_at: str
    duration_ms: int
    parameters: dict[str, int | str]
    send_summary: LatencySummary
    immediate_switch_summary: LatencySummary
    repeated_switch_summary: LatencySummary
    mcp_load_summary: LatencySummary
    slowest_immediate_switches: tuple[RequestResult, ...]
    fake_llm_metrics: dict[str, object]
    backend_metrics: dict[str, dict[str, int]]
    backend_log_file: str
    fake_llm_log_file: str
    error: str = ""


def main() -> None:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    run_root = _prepare_run_root(repo_root)
    original_home_env = _capture_home_env()
    fake_llm_process: ManagedProcess | None = None
    backend_process: ManagedProcess | None = None
    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()

    try:
        environment = _build_environment(repo_root, run_root, args)
        _apply_switch_environment(environment, args)
        fake_llm_port, backend_port = find_free_ports(2)
        fake_llm_admin_url = f"http://127.0.0.1:{fake_llm_port}"
        fake_llm_v1_base_url = f"{fake_llm_admin_url}/v1"
        api_base_url = f"http://127.0.0.1:{backend_port}"
        config_dir = run_root / ".relay-teams"
        write_test_runtime_config(
            config_dir=config_dir,
            fake_llm_v1_base_url=fake_llm_v1_base_url,
        )
        assert_integration_model_config_uses_fake_llm(config_dir=config_dir)
        mcp_server_names = _write_slow_mcp_config(
            run_root=run_root,
            config_dir=config_dir,
            count=args.mcp_servers,
        )
        _apply_home_env(run_root)

        fake_llm_log_file = run_root / "fake-llm.log"
        backend_log_file = run_root / "backend.log"
        fake_llm_process = _start_fake_llm(
            repo_root,
            environment,
            fake_llm_port,
            fake_llm_log_file,
        )
        wait_for_http_ready(
            url=f"{fake_llm_admin_url}/health",
            timeout_seconds=20.0,
            process=fake_llm_process,
        )
        backend_process = _start_backend(
            repo_root,
            environment,
            backend_port,
            backend_log_file,
        )
        wait_for_http_ready(
            url=f"{api_base_url}/api/system/health",
            timeout_seconds=90.0,
            process=backend_process,
        )

        summary = _run_benchmark(
            args=args,
            api_base_url=api_base_url,
            fake_llm_admin_url=fake_llm_admin_url,
            mcp_server_names=mcp_server_names,
            started_at=started_at,
            started=started,
            backend_log_file=backend_log_file,
            fake_llm_log_file=fake_llm_log_file,
        )
        summary_path = run_root / "summary.json"
        summary_path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
        print(summary.model_dump_json(indent=2))
        print(f"\nsummary_path={summary_path}")
        _raise_if_threshold_failed(summary, args)
    finally:
        if backend_process is not None:
            stop_process(backend_process)
        if fake_llm_process is not None:
            stop_process(fake_llm_process)
        _restore_home_env(original_home_env)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run send-then-switch session latency pressure benchmark.",
    )
    parser.add_argument("--sessions", type=int, default=30)
    parser.add_argument("--send-count", type=int, default=30)
    parser.add_argument("--send-workers", type=int, default=4)
    parser.add_argument("--switch-workers", type=int, default=16)
    parser.add_argument("--switch-rounds", type=int, default=600)
    parser.add_argument("--switch-interval-ms", type=int, default=50)
    parser.add_argument("--slow-llm-ms", type=int, default=12000)
    parser.add_argument("--mcp-servers", type=int, default=40)
    parser.add_argument("--mcp-load-workers", type=int, default=8)
    parser.add_argument("--mcp-load-interval-ms", type=int, default=50)
    parser.add_argument("--mcp-busy-backoff-ms", type=int, default=1000)
    parser.add_argument("--post-send-switch-seconds", type=float, default=20.0)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--llm-http-max-concurrency", type=int, default=4)
    parser.add_argument("--run-worker-active-limit", type=int, default=2)
    parser.add_argument("--expected-immediate-switch-p95-ms", type=int, default=200)
    parser.add_argument("--expected-immediate-switch-max-ms", type=int, default=1000)
    parser.add_argument("--expected-repeated-switch-p95-ms", type=int, default=200)
    parser.add_argument(
        "--expected-repeated-switch-endpoint-p95-ms",
        type=int,
        default=200,
    )
    parser.add_argument("--expected-repeated-switch-max-ms", type=int, default=1000)
    return parser.parse_args()


def _prepare_run_root(repo_root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_root = repo_root / ".tmp" / f"session-switch-after-send-{stamp}"
    run_root.mkdir(parents=True, exist_ok=True)
    return run_root


def _apply_switch_environment(
    environment: dict[str, str],
    args: argparse.Namespace,
) -> None:
    environment["RELAY_TEAMS_LLM_HTTP_MAX_CONCURRENCY"] = str(
        args.llm_http_max_concurrency
    )
    environment["RELAY_TEAMS_SESSION_SNAPSHOT_CACHE_MS"] = "1000"
    environment["RELAY_TEAMS_SESSION_SNAPSHOT_REFRESH_MIN_INTERVAL_MS"] = "500"
    environment["RELAY_TEAMS_LIST_SESSIONS_CACHE_MS"] = "1000"
    environment["RELAY_TEAMS_SESSION_FAST_READ_WORKERS"] = "64"
    environment["RELAY_TEAMS_SESSION_PROJECTION_REFRESH_WORKERS"] = "2"
    environment["RELAY_TEAMS_MCP_DISCOVERY_CONCURRENCY"] = "0"
    environment["RELAY_TEAMS_RUNTIME_MCP_SCHEMA_LOAD_BUDGET_MS"] = "120"
    environment["RELAY_TEAMS_RUNTIME_MCP_SCHEMA_SERVER_TIMEOUT_MS"] = "40"
    environment["RELAY_TEAMS_RUNTIME_MCP_SCHEMA_FAILED_TTL_MS"] = "60000"
    environment["RELAY_TEAMS_RUNTIME_MCP_SCHEMA_MAX_UNCACHED_PROBES"] = "0"
    environment["RELAY_TEAMS_MCP_TOOL_LOAD_CONCURRENCY"] = "0"
    environment["RELAY_TEAMS_MCP_TOOL_LOAD_FAILED_TTL_MS"] = "60000"
    environment["RELAY_TEAMS_MCP_TOOL_LOAD_GLOBAL_FAILURE_TTL_MS"] = "60000"
    environment["RELAY_TEAMS_MCP_TOOLS_ROUTE_MIN_INTERVAL_MS"] = "100"
    environment["RELAY_TEAMS_RUN_WORKER_ACTIVE_LIMIT"] = str(
        args.run_worker_active_limit
    )
    environment["RELAY_TEAMS_RUN_CREATE_STARTUP_WAIT_MS"] = "0"


def _write_slow_mcp_config(
    *,
    run_root: Path,
    config_dir: Path,
    count: int,
) -> tuple[str, ...]:
    if count <= 0:
        return ()
    script_path = run_root / "slow_mcp_stdio.py"
    script_path.write_text(
        "from __future__ import annotations\nimport time\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    servers: dict[str, dict[str, object]] = {}
    names: list[str] = []
    for index in range(count):
        name = f"slow_mcp_{index:03d}"
        names.append(name)
        servers[name] = {
            "command": sys.executable,
            "args": [str(script_path)],
            "timeout": 0.2,
            "read_timeout": 0.2,
            "enabled": True,
        }
    (config_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": servers}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return tuple(names)


def _run_benchmark(
    *,
    args: argparse.Namespace,
    api_base_url: str,
    fake_llm_admin_url: str,
    mcp_server_names: tuple[str, ...],
    started_at: str,
    started: float,
    backend_log_file: Path,
    fake_llm_log_file: Path,
) -> SwitchBenchmarkSummary:
    with httpx.Client(
        base_url=api_base_url, timeout=_HTTP_TIMEOUT, trust_env=False
    ) as client:
        session_ids = tuple(
            create_session(
                client,
                session_id=new_session_id(f"switch-pressure-{index:02d}"),
            )
            for index in range(args.sessions)
        )
        _warm_session_reads(client, session_ids)

    stop_background = Event()
    repeated_results: list[RequestResult] = []
    mcp_results: list[RequestResult] = []
    result_lock = Lock()
    with ThreadPoolExecutor(
        max_workers=args.switch_workers + args.mcp_load_workers + args.send_workers
    ) as executor:
        background_futures = _start_background_load(
            executor=executor,
            args=args,
            api_base_url=api_base_url,
            session_ids=session_ids,
            mcp_server_names=mcp_server_names,
            stop_event=stop_background,
            repeated_results=repeated_results,
            mcp_results=mcp_results,
            result_lock=result_lock,
        )
        send_futures = [
            executor.submit(
                _send_and_switch_once,
                api_base_url,
                session_ids[index % len(session_ids)],
                session_ids[(index + 1) % len(session_ids)],
                args.slow_llm_ms,
                index,
            )
            for index in range(args.send_count)
        ]
        send_results: list[RequestResult] = []
        immediate_switch_results: list[RequestResult] = []
        error = ""
        try:
            for future in as_completed(send_futures, timeout=args.timeout_seconds):
                send_result, switch_result = future.result()
                send_results.append(send_result)
                immediate_switch_results.append(switch_result)
            if args.post_send_switch_seconds > 0:
                stop_background.wait(args.post_send_switch_seconds)
        except RECOVERABLE_SWITCH_BENCHMARK_ERRORS as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            stop_background.set()
            for future in background_futures:
                _background_result(future)

    fake_llm_metrics = _load_fake_llm_metrics(fake_llm_admin_url)
    backend_metrics = _parse_backend_log_metrics(backend_log_file)
    slowest_immediate = tuple(
        sorted(
            immediate_switch_results,
            key=lambda result: result.duration_ms,
            reverse=True,
        )[:10]
    )
    return SwitchBenchmarkSummary(
        started_at=started_at,
        duration_ms=int((time.perf_counter() - started) * 1000),
        parameters={
            "sessions": args.sessions,
            "send_count": args.send_count,
            "send_workers": args.send_workers,
            "switch_workers": args.switch_workers,
            "switch_rounds": args.switch_rounds,
            "switch_interval_ms": args.switch_interval_ms,
            "slow_llm_ms": args.slow_llm_ms,
            "mcp_servers": args.mcp_servers,
            "mcp_load_workers": args.mcp_load_workers,
            "mcp_load_interval_ms": args.mcp_load_interval_ms,
            "mcp_busy_backoff_ms": args.mcp_busy_backoff_ms,
            "post_send_switch_seconds": args.post_send_switch_seconds,
            "llm_http_max_concurrency": args.llm_http_max_concurrency,
            "run_worker_active_limit": args.run_worker_active_limit,
            "expected_immediate_switch_p95_ms": args.expected_immediate_switch_p95_ms,
            "expected_immediate_switch_max_ms": args.expected_immediate_switch_max_ms,
            "expected_repeated_switch_p95_ms": args.expected_repeated_switch_p95_ms,
            "expected_repeated_switch_endpoint_p95_ms": (
                args.expected_repeated_switch_endpoint_p95_ms
            ),
            "expected_repeated_switch_max_ms": args.expected_repeated_switch_max_ms,
        },
        send_summary=_summarize_results(tuple(send_results)),
        immediate_switch_summary=_summarize_results(tuple(immediate_switch_results)),
        repeated_switch_summary=_summarize_results(tuple(repeated_results)),
        mcp_load_summary=_summarize_results(tuple(mcp_results)),
        slowest_immediate_switches=slowest_immediate,
        fake_llm_metrics=fake_llm_metrics,
        backend_metrics=backend_metrics,
        backend_log_file=str(backend_log_file),
        fake_llm_log_file=str(fake_llm_log_file),
        error=error,
    )


def _warm_session_reads(client: httpx.Client, session_ids: Sequence[str]) -> None:
    for session_id in session_ids:
        for path in _switch_paths(session_id):
            response = client.get(path)
            _consume_response(response)


def _start_background_load(
    *,
    executor: ThreadPoolExecutor,
    args: argparse.Namespace,
    api_base_url: str,
    session_ids: tuple[str, ...],
    mcp_server_names: tuple[str, ...],
    stop_event: Event,
    repeated_results: list[RequestResult],
    mcp_results: list[RequestResult],
    result_lock: Lock,
) -> tuple[Future[None], ...]:
    futures: list[Future[None]] = []
    for worker_index in range(args.switch_workers):
        futures.append(
            executor.submit(
                _repeat_switches,
                api_base_url,
                session_ids,
                args.switch_rounds,
                worker_index,
                args.switch_interval_ms,
                stop_event,
                repeated_results,
                result_lock,
            )
        )
    for worker_index in range(args.mcp_load_workers):
        futures.append(
            executor.submit(
                _load_mcp_tools_until_stopped,
                api_base_url,
                mcp_server_names,
                worker_index,
                args.mcp_load_interval_ms,
                args.mcp_busy_backoff_ms,
                stop_event,
                mcp_results,
                result_lock,
            )
        )
    return tuple(futures)


def _send_and_switch_once(
    api_base_url: str,
    source_session_id: str,
    target_session_id: str,
    slow_llm_ms: int,
    index: int,
) -> tuple[RequestResult, RequestResult]:
    with (
        httpx.Client(
            base_url=api_base_url,
            timeout=_SEND_TIMEOUT,
            trust_env=False,
        ) as send_client,
        httpx.Client(
            base_url=api_base_url, timeout=_HTTP_TIMEOUT, trust_env=False
        ) as switch_client,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        send_future = executor.submit(
            _send_run,
            send_client,
            source_session_id=source_session_id,
            slow_llm_ms=slow_llm_ms,
            index=index,
        )
        switch_result = _send_request(
            switch_client,
            kind="immediate_switch",
            method="GET",
            path=f"/api/sessions/{target_session_id}/recovery",
            source_session_id=source_session_id,
            target_session_id=target_session_id,
        )
        send_result = send_future.result()
        return send_result, switch_result


def _send_run(
    client: httpx.Client,
    *,
    source_session_id: str,
    slow_llm_ms: int,
    index: int,
) -> RequestResult:
    path = "/api/runs"
    payload = {
        "session_id": source_session_id,
        "input": [
            {
                "kind": "text",
                "text": (
                    f"[slow-stream-hold ms={slow_llm_ms}] "
                    f"send then switch pressure {index}"
                ),
            }
        ],
        "execution_mode": "ai",
        "yolo": False,
    }
    started = time.perf_counter()
    try:
        response = client.post(path, json=payload)
        body = _consume_response(response)
        run_id = body.get("run_id") if isinstance(body, dict) else ""
        return RequestResult(
            kind="send",
            method="POST",
            path=path,
            status_code=response.status_code,
            duration_ms=int((time.perf_counter() - started) * 1000),
            source_session_id=source_session_id,
            run_id=run_id if isinstance(run_id, str) else "",
        )
    except RECOVERABLE_SWITCH_BENCHMARK_ERRORS as exc:
        return RequestResult(
            kind="send",
            method="POST",
            path=path,
            status_code=0,
            duration_ms=int((time.perf_counter() - started) * 1000),
            source_session_id=source_session_id,
            error=f"{type(exc).__name__}: {exc}",
        )


def _repeat_switches(
    api_base_url: str,
    session_ids: tuple[str, ...],
    rounds: int,
    worker_index: int,
    interval_ms: int,
    stop_event: Event,
    results: list[RequestResult],
    lock: Lock,
) -> None:
    if not session_ids:
        return
    if interval_ms > 0:
        initial_delay_seconds = (worker_index % 16) * (interval_ms / 1000.0) / 16.0
        if stop_event.wait(initial_delay_seconds):
            return
    with httpx.Client(
        base_url=api_base_url, timeout=_HTTP_TIMEOUT, trust_env=False
    ) as client:
        index = worker_index
        for _ in range(rounds):
            if stop_event.is_set():
                break
            session_id = session_ids[index % len(session_ids)]
            paths = _switch_paths(session_id)
            result = _send_request(
                client,
                kind="repeated_switch",
                method="GET",
                path=paths[index % len(paths)],
                target_session_id=session_id,
            )
            with lock:
                results.append(result)
            index += 1
            if interval_ms > 0:
                jitter_bucket = ((index + worker_index) % 7) - 3
                jitter_ms = int(interval_ms * jitter_bucket * 0.12)
                delay_ms = max(1, interval_ms + jitter_ms)
                if stop_event.wait(delay_ms / 1000.0):
                    break


def _load_mcp_tools_until_stopped(
    api_base_url: str,
    server_names: tuple[str, ...],
    worker_index: int,
    interval_ms: int,
    busy_backoff_ms: int,
    stop_event: Event,
    results: list[RequestResult],
    lock: Lock,
) -> None:
    if not server_names:
        return
    if interval_ms > 0:
        initial_delay_seconds = (worker_index % 8) * (interval_ms / 1000.0) / 8.0
        if stop_event.wait(initial_delay_seconds):
            return
    with httpx.Client(
        base_url=api_base_url, timeout=_HTTP_TIMEOUT, trust_env=False
    ) as client:
        index = worker_index
        while not stop_event.is_set():
            name = server_names[index % len(server_names)]
            result = _send_request(
                client,
                kind="mcp_load",
                method="GET",
                path=f"/api/mcp/servers/{name}/tools",
            )
            with lock:
                results.append(result)
            index += 1
            wait_ms = interval_ms
            if result.status_code == 429:
                wait_ms = max(wait_ms, busy_backoff_ms)
            jitter_bucket = ((index + worker_index) % 5) - 2
            wait_ms = max(1, wait_ms + int(interval_ms * jitter_bucket * 0.1))
            if wait_ms > 0 and stop_event.wait(wait_ms / 1000.0):
                break


def _switch_paths(session_id: str) -> tuple[str, ...]:
    return (
        f"/api/sessions/{session_id}",
        f"/api/sessions/{session_id}/rounds?summary=true&limit=4",
        f"/api/sessions/{session_id}/recovery",
        f"/api/sessions/{session_id}/token-usage",
        f"/api/sessions/{session_id}/agents",
        f"/api/sessions/{session_id}/tasks",
        f"/api/sessions/{session_id}/subagents",
        "/api/sessions",
    )


def _send_request(
    client: httpx.Client,
    *,
    kind: str,
    method: str,
    path: str,
    source_session_id: str = "",
    target_session_id: str = "",
    run_id: str = "",
) -> RequestResult:
    started = time.perf_counter()
    try:
        response = client.request(method, path)
        _consume_response(response)
        return RequestResult(
            kind=kind,
            method=method,
            path=path,
            status_code=response.status_code,
            duration_ms=int((time.perf_counter() - started) * 1000),
            source_session_id=source_session_id,
            target_session_id=target_session_id,
            run_id=run_id,
        )
    except RECOVERABLE_SWITCH_BENCHMARK_ERRORS as exc:
        return RequestResult(
            kind=kind,
            method=method,
            path=path,
            status_code=0,
            duration_ms=int((time.perf_counter() - started) * 1000),
            source_session_id=source_session_id,
            target_session_id=target_session_id,
            run_id=run_id,
            error=f"{type(exc).__name__}: {exc}",
        )


def _consume_response(response: httpx.Response) -> object:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.json()
    return response.text


def _background_result(future: Future[None]) -> None:
    try:
        future.result(timeout=10.0)
    except RECOVERABLE_SWITCH_BENCHMARK_ERRORS:
        return


def _summarize_results(results: tuple[RequestResult, ...]) -> LatencySummary:
    durations = sorted(result.duration_ms for result in results)
    failures = tuple(
        result
        for result in results
        if result.status_code == 0
        or result.status_code == 429
        or result.status_code >= 500
    )
    grouped: dict[str, list[RequestResult]] = {}
    for result in results:
        grouped.setdefault(_normalized_path(result.path), []).append(result)
    return LatencySummary(
        count=len(results),
        failure_count=len(failures),
        p50_ms=_percentile(durations, 0.50),
        p95_ms=_percentile(durations, 0.95),
        p99_ms=_percentile(durations, 0.99),
        max_ms=durations[-1] if durations else 0,
        by_path={
            path: _summarize_results(tuple(path_results))
            for path, path_results in sorted(grouped.items())
        }
        if len(grouped) > 1
        else {},
    )


def _normalized_path(path: str) -> str:
    if path.startswith("/api/sessions/"):
        parts = path.split("?")[0].split("/")
        if len(parts) >= 4:
            parts[3] = "{session_id}"
        return "/".join(parts)
    if path.startswith("/api/mcp/servers/"):
        return "/api/mcp/servers/{server_name}/tools"
    return path.split("?", 1)[0]


def _raise_if_threshold_failed(
    summary: SwitchBenchmarkSummary,
    args: argparse.Namespace,
) -> None:
    errors: list[str] = []
    _append_threshold_error(
        errors,
        "immediate switch p95",
        summary.immediate_switch_summary.p95_ms,
        args.expected_immediate_switch_p95_ms,
    )
    _append_threshold_error(
        errors,
        "immediate switch max",
        summary.immediate_switch_summary.max_ms,
        args.expected_immediate_switch_max_ms,
    )
    _append_threshold_error(
        errors,
        "repeated switch p95",
        summary.repeated_switch_summary.p95_ms,
        args.expected_repeated_switch_p95_ms,
    )
    _append_threshold_error(
        errors,
        "repeated switch max",
        summary.repeated_switch_summary.max_ms,
        args.expected_repeated_switch_max_ms,
    )
    for path, path_summary in summary.repeated_switch_summary.by_path.items():
        _append_threshold_error(
            errors,
            f"repeated switch {path} p95",
            path_summary.p95_ms,
            args.expected_repeated_switch_endpoint_p95_ms,
        )
    if summary.immediate_switch_summary.failure_count:
        errors.append(
            f"immediate switch failures={summary.immediate_switch_summary.failure_count}"
        )
    if summary.repeated_switch_summary.failure_count:
        errors.append(
            f"repeated switch failures={summary.repeated_switch_summary.failure_count}"
        )
    if errors:
        raise SystemExit("; ".join(errors))


def _append_threshold_error(
    errors: list[str],
    label: str,
    actual: int,
    expected: int,
) -> None:
    if actual > expected:
        errors.append(f"{label} {actual}ms > {expected}ms")


LatencySummary.model_rebuild()


if __name__ == "__main__":
    main()
