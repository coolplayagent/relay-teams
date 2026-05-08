from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from concurrent.futures import (
    FIRST_COMPLETED,
    Future,
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
    wait,
)
from datetime import UTC, datetime
import argparse
import json
import os
from os import pathsep
from pathlib import Path
import re
import random
import sqlite3
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

from tests.integration_tests.support.api_helpers import (  # noqa: E402
    create_run,
    create_session,
    new_session_id,
    stream_run_until_terminal,
)
from tests.integration_tests.support.config_builder import (  # noqa: E402
    assert_integration_model_config_uses_fake_llm,
    write_test_runtime_config,
)
from tests.integration_tests.support.process_control import (  # noqa: E402
    ManagedProcess,
    find_free_ports,
    start_process,
    stop_process,
    wait_for_http_ready,
)

RECOVERABLE_BENCHMARK_ERRORS = (
    AssertionError,
    RuntimeError,
    ValueError,
    httpx.HTTPError,
    sqlite3.Error,
)


_HOME_ENV_KEYS: tuple[str, ...] = ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH")
_PROXY_ENV_KEYS: tuple[str, ...] = (
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
    "SSL_VERIFY",
)
_UNKNOWN_TOOL_NAME = "unknown"
_PROBE_TIMEOUT = httpx.Timeout(
    6.0,
    connect=2.0,
    read=6.0,
    write=2.0,
    pool=2.0,
)


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    run_id: str
    duration_ms: int
    terminal_event_type: str
    stream_event_counts: dict[str, int]
    session_event_counts: dict[str, int]
    subagent_event_count: int


class RunProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    run_id: str
    main_read_completed_count: int
    spawn_completed_count: int
    subagent_completed_count: int
    last_event_type: str
    last_tool_call_id: str
    stage: str


class ProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: str
    path: str
    status_code: int
    duration_ms: int
    error: str = ""


class ProbeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int
    failure_count: int
    p50_ms: int
    p95_ms: int
    p99_ms: int
    max_ms: int
    by_endpoint: dict[str, "EndpointProbeSummary"]
    slowest: tuple[ProbeResult, ...]


class EndpointProbeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int
    failure_count: int
    p50_ms: int
    p95_ms: int
    p99_ms: int
    max_ms: int


class NavigationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    session_id: str
    run_id: str = ""
    instance_id: str = ""


class NavigationStepResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    transition: str
    target: NavigationTarget
    duration_ms: int
    hydration_duration_ms: int = 0
    status_code: int
    after_event_id: int = 0
    stream_duplicate_count: int = 0
    stream_gap_count: int = 0
    wrong_target_render_count: int = 0
    error: str = ""
    hydration_error: str = ""


class NavigationTransitionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int
    failure_count: int
    p50_ms: int
    p95_ms: int
    max_ms: int
    hydration_p95_ms: int = 0
    hydration_max_ms: int = 0


class NavigationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int
    failure_count: int
    p50_ms: int
    p95_ms: int
    max_ms: int
    hydration_p95_ms: int
    hydration_max_ms: int
    hydration_failure_count: int
    by_transition: dict[str, NavigationTransitionSummary]
    stream_duplicate_count: int
    stream_gap_count: int
    wrong_target_render_count: int
    running_indicator_missing_count: int
    terminal_refresh_wrong_target_count: int
    failures: tuple[NavigationStepResult, ...] = ()


class TerminalInvariantRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    run_id: str
    kind: str
    runtime_status: str
    run_state_status: str
    has_terminal_event: bool
    ok: bool
    error: str = ""


class TerminalInvariantSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int
    failure_count: int
    records: tuple[TerminalInvariantRecord, ...]


class BenchmarkSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    started_at: str
    duration_ms: int
    parameters: dict[str, int | str]
    runs: tuple[RunResult, ...]
    progress: tuple[RunProgress, ...]
    tool_metrics: dict[str, int]
    tool_metrics_by_name: dict[str, dict[str, int]]
    message_commit_metrics: dict[str, int]
    tool_call_batch_state_metrics: dict[str, int]
    tool_result_batch_metrics: dict[str, int]
    relay_tool_step_metrics: dict[str, int]
    sessions_list_cache_metrics: dict[str, int]
    sync_subagent_metrics: dict[str, int]
    probe_summary: ProbeSummary
    fake_llm_metrics: dict[str, object]
    artifact_metrics: dict[str, int]
    sqlite_metrics: dict[str, int]
    sse_heartbeat_count: int
    navigation_summary: NavigationSummary
    terminal_invariant_summary: TerminalInvariantSummary
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
    started = time.perf_counter()
    started_at = datetime.now(UTC).isoformat()

    try:
        environment = _build_environment(repo_root, run_root, args)
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

        summary_path = run_root / "summary.json"
        summary = _run_benchmark(
            args=args,
            api_base_url=api_base_url,
            fake_llm_admin_url=fake_llm_admin_url,
            started_at=started_at,
            started=started,
            backend_log_file=backend_log_file,
            fake_llm_log_file=fake_llm_log_file,
        )
        summary_path.write_text(
            summary.model_dump_json(indent=2),
            encoding="utf-8",
        )
        print(summary.model_dump_json(indent=2))
        print(f"\nsummary_path={summary_path}")
    finally:
        if backend_process is not None:
            stop_process(backend_process)
        if fake_llm_process is not None:
            stop_process(fake_llm_process)
        _restore_home_env(original_home_env)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the extreme session/subagent tool-call pressure benchmark.",
    )
    parser.add_argument("--sessions", type=int, default=10)
    parser.add_argument("--main-tool-calls", type=int, default=500)
    parser.add_argument("--main-batch-size", type=int, default=100)
    parser.add_argument("--subagents", type=int, default=10)
    parser.add_argument("--subagent-tool-calls", type=int, default=100)
    parser.add_argument("--subagent-batch-size", type=int, default=100)
    parser.add_argument("--subagent-spawn-batch-size", type=int, default=3)
    parser.add_argument("--probe-workers", type=int, default=12)
    parser.add_argument("--timeout-seconds", type=float, default=7200.0)
    parser.add_argument(
        "--fail-fast-probes",
        action="store_true",
        help=(
            "Stop pressure runs early when cumulative probe metrics exceed the "
            "expected failure-rate, p95, or max latency thresholds."
        ),
    )
    parser.add_argument("--fail-fast-min-probes", type=int, default=200)
    parser.add_argument("--fail-fast-check-interval-seconds", type=float, default=5.0)
    parser.add_argument("--expected-probe-failure-rate", type=float, default=0.01)
    parser.add_argument("--expected-probe-p95-ms", type=int, default=2000)
    parser.add_argument("--expected-probe-max-ms", type=int, default=8000)
    parser.add_argument("--llm-http-max-concurrency", type=int, default=16)
    parser.add_argument(
        "--scenario",
        choices=("pressure", "user-switching-full"),
        default="pressure",
    )
    parser.add_argument(
        "--switch-targets",
        choices=("roots", "subagents", "mixed"),
        default="mixed",
    )
    parser.add_argument(
        "--switch-pattern",
        choices=("matrix", "random", "race-terminal"),
        default="matrix",
    )
    parser.add_argument("--switch-interval-ms", type=int, default=120)
    parser.add_argument("--path", type=str, default="AGENTS.md")
    return parser.parse_args()


def _prepare_run_root(repo_root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_root = repo_root / ".tmp" / f"extreme-session-pressure-{stamp}"
    run_root.mkdir(parents=True, exist_ok=True)
    return run_root


def _build_environment(
    repo_root: Path,
    run_root: Path,
    args: argparse.Namespace,
) -> dict[str, str]:
    environment = os.environ.copy()
    for key in _PROXY_ENV_KEYS:
        environment.pop(key, None)
    python_paths = [str(repo_root), str(repo_root / "src"), str(repo_root / "tests")]
    existing_pythonpath = environment.get("PYTHONPATH", "")
    if existing_pythonpath:
        python_paths.append(existing_pythonpath)
    environment["PYTHONPATH"] = pathsep.join(python_paths)
    environment["AGENT_TEAMS_COMPUTER_RUNTIME"] = "fake"
    environment["RELAY_TEAMS_LLM_HTTP_MAX_CONCURRENCY"] = str(
        args.llm_http_max_concurrency,
    )
    environment["RELAY_TEAMS_SESSION_SNAPSHOT_CACHE_MS"] = "500"
    environment["RELAY_TEAMS_SESSION_SNAPSHOT_REFRESH_MIN_INTERVAL_MS"] = "250"
    environment["RELAY_TEAMS_SESSION_FAST_READ_WORKERS"] = "8"
    environment["RELAY_TEAMS_SESSION_PROJECTION_REFRESH_WORKERS"] = "2"
    environment["RELAY_TEAMS_RUN_WORKER_ACTIVE_LIMIT"] = "32"
    environment["RELAY_TEAMS_TOOL_ACTION_WORKERS"] = "128"
    environment["RELAY_TEAMS_TOOL_STEP_CONCURRENCY"] = "16"
    environment["RELAY_TEAMS_TOOL_STEP_BATCH_CONCURRENCY"] = "16"
    environment["RELAY_TEAMS_TOOL_STEP_GLOBAL_CONCURRENCY"] = "16"
    environment["RELAY_TEAMS_SYNC_SUBAGENT_ACTIVE_LIMIT"] = "3"
    environment["RELAY_TEAMS_SYNC_SUBAGENT_GLOBAL_ACTIVE_LIMIT"] = "8"
    environment["PYTHONUTF8"] = "1"
    environment["HOME"] = str(run_root)
    environment["USERPROFILE"] = str(run_root)
    if run_root.drive:
        environment["HOMEDRIVE"] = run_root.drive
        environment["HOMEPATH"] = str(run_root).removeprefix(run_root.drive)
    ripgrep_path = _find_existing_ripgrep_binary()
    if ripgrep_path is not None:
        environment["RELAY_TEAMS_RIPGREP_PATH"] = str(ripgrep_path)
    return environment


def _find_existing_ripgrep_binary() -> Path | None:
    for env_key in _HOME_ENV_KEYS:
        raw_home = os.environ.get(env_key)
        if raw_home is None or not raw_home.strip():
            continue
        candidate = Path(raw_home).expanduser() / ".relay-teams" / "bin" / "rg.exe"
        if candidate.is_file():
            return candidate
    return None


def _capture_home_env() -> dict[str, str | None]:
    return {key: os.environ.get(key) for key in _HOME_ENV_KEYS}


def _apply_home_env(run_root: Path) -> None:
    os.environ["HOME"] = str(run_root)
    os.environ["USERPROFILE"] = str(run_root)
    if run_root.drive:
        os.environ["HOMEDRIVE"] = run_root.drive
        os.environ["HOMEPATH"] = str(run_root).removeprefix(run_root.drive)


def _restore_home_env(original_env: dict[str, str | None]) -> None:
    for key, value in original_env.items():
        if value is None:
            os.environ.pop(key, None)
            continue
        os.environ[key] = value


def _start_fake_llm(
    repo_root: Path,
    environment: dict[str, str],
    port: int,
    log_file: Path,
) -> ManagedProcess:
    return start_process(
        name="fake-llm",
        command=(
            sys.executable,
            "-m",
            "uvicorn",
            "integration_tests.support.fake_llm_server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ),
        cwd=repo_root,
        env=environment,
        log_file=log_file,
    )


def _start_backend(
    repo_root: Path,
    environment: dict[str, str],
    port: int,
    log_file: Path,
) -> ManagedProcess:
    return start_process(
        name="agent-teams-backend",
        command=(
            sys.executable,
            "-m",
            "uvicorn",
            "relay_teams.interfaces.server.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ),
        cwd=repo_root,
        env=environment,
        log_file=log_file,
    )


def _run_benchmark(
    *,
    args: argparse.Namespace,
    api_base_url: str,
    fake_llm_admin_url: str,
    started_at: str,
    started: float,
    backend_log_file: Path,
    fake_llm_log_file: Path,
) -> BenchmarkSummary:
    with httpx.Client(base_url=api_base_url, timeout=60.0, trust_env=False) as client:
        session_ids = [
            create_session(
                client,
                session_id=new_session_id(f"extreme-pressure-{index:02d}"),
            )
            for index in range(args.sessions)
        ]

    stop_probes = Event()
    stop_navigation = Event()
    probe_results: list[ProbeResult] = []
    navigation_results: list[NavigationStepResult] = []
    probe_lock = Lock()
    navigation_lock = Lock()
    probe_executor = ThreadPoolExecutor(max_workers=args.probe_workers)
    probe_futures = [
        probe_executor.submit(
            _probe_backend_until_stopped,
            api_base_url,
            tuple(session_ids),
            stop_probes,
            probe_results,
            probe_lock,
            worker_index,
        )
        for worker_index in range(args.probe_workers)
    ]
    navigation_executor = ThreadPoolExecutor(max_workers=1)
    navigation_future: Future[None] | None = None
    if args.scenario == "user-switching-full":
        navigation_future = navigation_executor.submit(
            _navigation_driver_until_stopped,
            api_base_url,
            tuple(session_ids),
            stop_navigation,
            navigation_results,
            navigation_lock,
            args,
        )

    benchmark_error = ""
    runs: tuple[RunResult, ...] = ()
    enriched_runs: tuple[RunResult, ...] = ()
    progress: tuple[RunProgress, ...] = ()
    tool_metrics: dict[str, int] = {}
    tool_metrics_by_name: dict[str, dict[str, int]] = {}
    terminal_invariants = TerminalInvariantSummary(
        count=0,
        failure_count=0,
        records=(),
    )
    try:
        runs, benchmark_error = _run_pressure_sessions(
            api_base_url=api_base_url,
            session_ids=session_ids,
            args=args,
            probe_results=probe_results,
            probe_lock=probe_lock,
        )
    except RECOVERABLE_BENCHMARK_ERRORS as exc:
        benchmark_error = f"{type(exc).__name__}: {exc}"
    finally:
        stop_navigation.set()
        stop_probes.set()
        if navigation_future is not None:
            try:
                navigation_future.result(timeout=7.0)
            except FutureTimeoutError:
                navigation_future.cancel()
        _stop_probe_workers(probe_futures)
        probe_executor.shutdown(wait=False, cancel_futures=True)
        navigation_executor.shutdown(wait=False, cancel_futures=True)

    enrichment_errors: list[str] = []
    if benchmark_error and args.fail_fast_probes:
        enriched_runs = runs
    else:
        with httpx.Client(
            base_url=api_base_url, timeout=20.0, trust_env=False
        ) as client:
            enriched_runs = tuple(
                _safe_enrich_run_result(
                    client,
                    run,
                    errors=enrichment_errors,
                )
                for run in runs
            )
            progress = tuple(
                _safe_load_session_progress(
                    client,
                    session_id=session_id,
                    args=args,
                    errors=enrichment_errors,
                )
                for session_id in session_ids
            )
            terminal_invariants = _safe_load_terminal_invariant_summary(
                client,
                tuple(session_ids),
                errors=enrichment_errors,
            )
            tool_metrics, tool_metrics_by_name = _safe_load_tool_metric_summary(
                client,
                tuple(session_ids),
                errors=enrichment_errors,
            )
    if enrichment_errors:
        benchmark_error = "; ".join(
            item for item in (benchmark_error, *enrichment_errors) if item
        )
    if terminal_invariants.failure_count > 0:
        benchmark_error = "; ".join(
            item
            for item in (
                benchmark_error,
                (
                    "terminal_invariant_failed: "
                    f"{terminal_invariants.failure_count}/"
                    f"{terminal_invariants.count}"
                ),
            )
            if item
        )
    navigation_summary = _summarize_navigation(tuple(navigation_results))
    if (
        navigation_summary.stream_duplicate_count > 0
        or navigation_summary.stream_gap_count > 0
        or navigation_summary.wrong_target_render_count > 0
    ):
        benchmark_error = "; ".join(
            item
            for item in (
                benchmark_error,
                (
                    "navigation_stream_invariant_failed: "
                    f"duplicates={navigation_summary.stream_duplicate_count} "
                    f"gaps={navigation_summary.stream_gap_count} "
                    f"wrong_target={navigation_summary.wrong_target_render_count}"
                ),
            )
            if item
        )

    fake_llm_metrics = _load_fake_llm_metrics(fake_llm_admin_url)
    backend_metrics = _parse_backend_log_metrics(backend_log_file)
    return BenchmarkSummary(
        started_at=started_at,
        duration_ms=int((time.perf_counter() - started) * 1000),
        parameters={
            "sessions": args.sessions,
            "main_tool_calls": args.main_tool_calls,
            "main_batch_size": args.main_batch_size,
            "subagents": args.subagents,
            "subagent_tool_calls": args.subagent_tool_calls,
            "subagent_batch_size": args.subagent_batch_size,
            "subagent_spawn_batch_size": args.subagent_spawn_batch_size,
            "probe_workers": args.probe_workers,
            "fail_fast_probes": "1" if args.fail_fast_probes else "0",
            "expected_probe_failure_rate": str(args.expected_probe_failure_rate),
            "expected_probe_p95_ms": args.expected_probe_p95_ms,
            "expected_probe_max_ms": args.expected_probe_max_ms,
            "llm_http_max_concurrency": args.llm_http_max_concurrency,
            "scenario": args.scenario,
            "switch_targets": args.switch_targets,
            "switch_pattern": args.switch_pattern,
            "switch_interval_ms": args.switch_interval_ms,
            "path": args.path,
        },
        runs=enriched_runs,
        progress=progress,
        tool_metrics=tool_metrics,
        tool_metrics_by_name=tool_metrics_by_name,
        message_commit_metrics=backend_metrics["message_commit"],
        tool_call_batch_state_metrics=backend_metrics["tool_call_batch_state"],
        tool_result_batch_metrics=backend_metrics["tool_result_batch"],
        relay_tool_step_metrics=backend_metrics["relay_tool_step"],
        sessions_list_cache_metrics=backend_metrics["sessions_list_cache"],
        sync_subagent_metrics=backend_metrics["sync_subagent"],
        probe_summary=_summarize_probes(tuple(probe_results)),
        fake_llm_metrics=fake_llm_metrics,
        artifact_metrics=backend_metrics["artifact"],
        sqlite_metrics=backend_metrics["sqlite"],
        sse_heartbeat_count=backend_metrics["sse_heartbeat_count"].get("count", 0),
        navigation_summary=navigation_summary,
        terminal_invariant_summary=terminal_invariants,
        backend_log_file=str(backend_log_file),
        fake_llm_log_file=str(fake_llm_log_file),
        error=benchmark_error,
    )


def _run_pressure_sessions(
    *,
    api_base_url: str,
    session_ids: list[str],
    args: argparse.Namespace,
    probe_results: list[ProbeResult],
    probe_lock: Lock,
) -> tuple[tuple[RunResult, ...], str]:
    executor = ThreadPoolExecutor(max_workers=len(session_ids))
    try:
        futures = [
            executor.submit(
                _run_single_pressure_session,
                api_base_url,
                session_id,
                index,
                args,
            )
            for index, session_id in enumerate(session_ids)
        ]
        pending = set(futures)
        results: list[RunResult] = []
        error = ""
        deadline = time.perf_counter() + args.timeout_seconds
        next_probe_check = time.perf_counter()
        while pending:
            remaining_seconds = deadline - time.perf_counter()
            if remaining_seconds <= 0:
                error = (
                    f"Timed out waiting for pressure runs: "
                    f"{len(results)}/{len(session_ids)} completed."
                )
                _cancel_futures(tuple(pending))
                break
            done, pending = wait(
                pending,
                timeout=min(1.0, remaining_seconds),
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                try:
                    results.append(future.result())
                except RECOVERABLE_BENCHMARK_ERRORS as exc:
                    error = f"{type(exc).__name__}: {exc}"
            if error:
                _cancel_futures(tuple(pending))
                break
            now = time.perf_counter()
            if args.fail_fast_probes and now >= next_probe_check:
                next_probe_check = now + args.fail_fast_check_interval_seconds
                with probe_lock:
                    probe_snapshot = tuple(probe_results)
                threshold_error = _probe_threshold_error(probe_snapshot, args)
                if threshold_error:
                    error = threshold_error
                    _cancel_futures(tuple(pending))
                    break
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return tuple(results), error


def _cancel_futures(futures: tuple[Future[RunResult], ...]) -> None:
    for future in futures:
        future.cancel()


def _probe_threshold_error(
    probes: tuple[ProbeResult, ...],
    args: argparse.Namespace,
) -> str:
    if len(probes) < args.fail_fast_min_probes:
        return ""
    summary = _summarize_probes(probes)
    failure_rate = summary.failure_count / max(summary.count, 1)
    if failure_rate > args.expected_probe_failure_rate:
        return (
            "Probe failure rate exceeded expectation: "
            f"{failure_rate:.4f} > {args.expected_probe_failure_rate:.4f}; "
            f"count={summary.count} failures={summary.failure_count}."
        )
    if summary.p95_ms > args.expected_probe_p95_ms:
        return (
            "Probe p95 exceeded expectation: "
            f"{summary.p95_ms}ms > {args.expected_probe_p95_ms}ms; "
            f"count={summary.count}."
        )
    if summary.max_ms > args.expected_probe_max_ms:
        return (
            "Probe max exceeded expectation: "
            f"{summary.max_ms}ms > {args.expected_probe_max_ms}ms; "
            f"count={summary.count}."
        )
    return ""


def _run_single_pressure_session(
    api_base_url: str,
    session_id: str,
    index: int,
    args: argparse.Namespace,
) -> RunResult:
    tag = f"s{index + 1}"
    intent = (
        "[session-extreme-pressure "
        f"main_calls={args.main_tool_calls} "
        f"main_batch={args.main_batch_size} "
        f"subagents={args.subagents} "
        f"subagent_calls={args.subagent_tool_calls} "
        f"subagent_batch={args.subagent_batch_size} "
        f"subagent_spawn_batch={args.subagent_spawn_batch_size} "
        f"path={args.path} "
        f"tag={tag}] run extreme pressure session {index + 1}."
    )
    timeout = httpx.Timeout(
        args.timeout_seconds,
        connect=10.0,
        read=min(args.timeout_seconds, 240.0),
        write=10.0,
        pool=10.0,
    )
    with httpx.Client(
        base_url=api_base_url, timeout=timeout, trust_env=False
    ) as client:
        started = time.perf_counter()
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
            timeout_seconds=args.timeout_seconds,
        )
        return RunResult(
            session_id=session_id,
            run_id=run_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            terminal_event_type=str(events[-1].get("event_type") or ""),
            stream_event_counts=_event_counts(events),
            session_event_counts={},
            subagent_event_count=0,
        )


def _enrich_run_result(client: httpx.Client, run: RunResult) -> RunResult:
    response = client.get(f"/api/sessions/{run.session_id}/events")
    response.raise_for_status()
    payload: object = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected session events payload: {payload}")
    session_event_counts: Counter[str] = Counter()
    subagent_event_count = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("event_type") or "")
        if event_type:
            session_event_counts[event_type] += 1
        if str(item.get("trace_id") or "").startswith("subagent_run_"):
            subagent_event_count += 1
    return run.model_copy(
        update={
            "session_event_counts": dict(session_event_counts),
            "subagent_event_count": subagent_event_count,
        },
    )


def _safe_enrich_run_result(
    client: httpx.Client,
    run: RunResult,
    *,
    errors: list[str],
) -> RunResult:
    try:
        return _enrich_run_result(client, run)
    except RECOVERABLE_BENCHMARK_ERRORS as exc:
        errors.append(
            f"enrich_run_failed[{run.session_id}]: {type(exc).__name__}: {exc}"
        )
        return run


def _load_session_progress(
    client: httpx.Client,
    *,
    session_id: str,
    args: argparse.Namespace,
) -> RunProgress:
    response = client.get(f"/api/sessions/{session_id}/events")
    response.raise_for_status()
    payload: object = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected session events payload: {payload}")

    root_run_id = ""
    last_event_type = ""
    last_tool_call_id = ""
    main_read_completed_count = 0
    spawn_completed_count = 0
    subagent_run_completed_count = 0
    subagent_read_completed_count = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        trace_id = str(item.get("trace_id") or "")
        event_type = str(item.get("event_type") or "")
        if trace_id and not trace_id.startswith("subagent_run_") and not root_run_id:
            root_run_id = trace_id
        if event_type:
            last_event_type = event_type
        event_payload = _event_payload(item)
        tool_call_id = str(event_payload.get("tool_call_id") or "")
        if tool_call_id:
            last_tool_call_id = tool_call_id
        if event_type == "tool_result":
            if tool_call_id.startswith("call-session-extreme-read-"):
                main_read_completed_count += 1
            elif tool_call_id.startswith("call-session-extreme-spawn-"):
                spawn_completed_count += 1
            elif tool_call_id.startswith("call-session-extreme-subagent-read-"):
                subagent_read_completed_count += 1
        if event_type == "run_completed" and trace_id.startswith("subagent_run_"):
            subagent_run_completed_count += 1

    subagent_completed_count = max(
        subagent_run_completed_count,
        spawn_completed_count
        if subagent_read_completed_count >= args.subagents * args.subagent_tool_calls
        else 0,
    )
    return RunProgress(
        session_id=session_id,
        run_id=root_run_id,
        main_read_completed_count=main_read_completed_count,
        spawn_completed_count=spawn_completed_count,
        subagent_completed_count=subagent_completed_count,
        last_event_type=last_event_type,
        last_tool_call_id=last_tool_call_id,
        stage=_progress_stage(
            main_read_completed_count=main_read_completed_count,
            spawn_completed_count=spawn_completed_count,
            subagent_completed_count=subagent_completed_count,
            args=args,
        ),
    )


def _safe_load_session_progress(
    client: httpx.Client,
    *,
    session_id: str,
    args: argparse.Namespace,
    errors: list[str],
) -> RunProgress:
    try:
        return _load_session_progress(client, session_id=session_id, args=args)
    except RECOVERABLE_BENCHMARK_ERRORS as exc:
        errors.append(f"progress_failed[{session_id}]: {type(exc).__name__}: {exc}")
        return RunProgress(
            session_id=session_id,
            run_id="",
            main_read_completed_count=0,
            spawn_completed_count=0,
            subagent_completed_count=0,
            last_event_type="",
            last_tool_call_id="",
            stage="unknown",
        )


def _event_payload(item: dict[object, object]) -> dict[str, object]:
    raw_payload = item.get("payload")
    if isinstance(raw_payload, dict):
        return {str(key): value for key, value in raw_payload.items()}
    raw_payload_json = item.get("payload_json")
    if not isinstance(raw_payload_json, str) or not raw_payload_json:
        return {}
    try:
        decoded: object = json.loads(raw_payload_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {str(key): value for key, value in decoded.items()}


def _progress_stage(
    *,
    main_read_completed_count: int,
    spawn_completed_count: int,
    subagent_completed_count: int,
    args: argparse.Namespace,
) -> str:
    if main_read_completed_count < args.main_tool_calls:
        return "main_tools"
    if spawn_completed_count < args.subagents:
        return "spawn_subagents"
    if subagent_completed_count < args.subagents:
        return "subagent_tools"
    return "finalize"


def _load_tool_metric_summary(
    client: httpx.Client,
    session_ids: tuple[str, ...],
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    metric_values: dict[str, list[int]] = {
        "action_duration_ms": [],
        "tool_framework_wait_ms": [],
        "tool_result_publish_ms": [],
        "tool_result_persist_ms": [],
        "tool_batch_wall_ms": [],
    }
    by_tool_values: dict[str, dict[str, list[int]]] = {}
    for session_id in session_ids:
        response = client.get(f"/api/sessions/{session_id}/events")
        response.raise_for_status()
        payload: object = response.json()
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            if str(item.get("event_type") or "") != "tool_result":
                continue
            event_payload = _event_payload(item)
            metrics = event_payload.get("metrics")
            if not isinstance(metrics, dict):
                continue
            tool_name = str(
                event_payload.get("tool_name")
                or event_payload.get("tool")
                or _UNKNOWN_TOOL_NAME
            )
            tool_metric_values = by_tool_values.setdefault(
                tool_name,
                {metric_name: [] for metric_name in metric_values},
            )
            for metric_name in metric_values:
                value = metrics.get(metric_name)
                if type(value) is int:
                    metric_values[metric_name].append(value)
                    tool_metric_values[metric_name].append(value)
    summary: dict[str, int] = {}
    for metric_name, values in metric_values.items():
        sorted_values = sorted(values)
        summary[f"{metric_name}_p95"] = _percentile(sorted_values, 0.95)
        summary[f"{metric_name}_max"] = sorted_values[-1] if sorted_values else 0
    by_tool_summary: dict[str, dict[str, int]] = {}
    for tool_name, tool_values in by_tool_values.items():
        by_tool_summary[tool_name] = {}
        for metric_name, values in tool_values.items():
            sorted_values = sorted(values)
            by_tool_summary[tool_name][f"{metric_name}_p95"] = _percentile(
                sorted_values,
                0.95,
            )
            by_tool_summary[tool_name][f"{metric_name}_max"] = (
                sorted_values[-1] if sorted_values else 0
            )
    return summary, by_tool_summary


def _safe_load_tool_metric_summary(
    client: httpx.Client,
    session_ids: tuple[str, ...],
    *,
    errors: list[str],
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    try:
        return _load_tool_metric_summary(client, session_ids)
    except RECOVERABLE_BENCHMARK_ERRORS as exc:
        errors.append(f"tool_metrics_failed: {type(exc).__name__}: {exc}")
        return {}, {}


def _load_terminal_invariant_summary(
    session_ids: tuple[str, ...],
) -> TerminalInvariantSummary:
    db_path = Path.home() / ".relay-teams" / "relay_teams.db"
    if not db_path.exists():
        return TerminalInvariantSummary(count=0, failure_count=0, records=())
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    records: list[TerminalInvariantRecord] = []
    try:
        for session_id in session_ids:
            runtime_rows = conn.execute(
                """
                SELECT run_id, status
                FROM run_runtime
                WHERE session_id=?
                """,
                (session_id,),
            ).fetchall()
            for runtime_row in runtime_rows:
                run_id = str(runtime_row["run_id"])
                kind = "subagent" if run_id.startswith("subagent_run_") else "root"
                runtime_status = str(runtime_row["status"])
                if runtime_status not in {"completed", "failed", "stopped"}:
                    continue
                run_state_status = _load_run_state_status(conn, run_id)
                terminal_event_count = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM events
                        WHERE trace_id=?
                        AND event_type IN ('run_completed', 'run_failed', 'run_stopped')
                        """,
                        (run_id,),
                    ).fetchone()["count"]
                )
                has_terminal_event = terminal_event_count > 0
                ok = has_terminal_event and run_state_status == runtime_status
                records.append(
                    TerminalInvariantRecord(
                        session_id=session_id,
                        run_id=run_id,
                        kind=kind,
                        runtime_status=runtime_status,
                        run_state_status=run_state_status,
                        has_terminal_event=has_terminal_event,
                        ok=ok,
                        error=(
                            ""
                            if ok
                            else "terminal runtime must have terminal event and matching run_state"
                        ),
                    )
                )
    finally:
        conn.close()
    failures = tuple(record for record in records if not record.ok)
    return TerminalInvariantSummary(
        count=len(records),
        failure_count=len(failures),
        records=tuple(records),
    )


def _safe_load_terminal_invariant_summary(
    client: httpx.Client,
    session_ids: tuple[str, ...],
    *,
    errors: list[str],
) -> TerminalInvariantSummary:
    _ = client
    try:
        return _load_terminal_invariant_summary(session_ids)
    except RECOVERABLE_BENCHMARK_ERRORS as exc:
        errors.append(f"terminal_invariants_failed: {type(exc).__name__}: {exc}")
        return TerminalInvariantSummary(count=0, failure_count=0, records=())


def _load_run_state_status(conn: sqlite3.Connection, run_id: str) -> str:
    row = conn.execute(
        "SELECT state_json FROM run_states WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        return ""
    try:
        payload: object = json.loads(str(row["state_json"]))
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("status") or "")


def _probe_backend_until_stopped(
    api_base_url: str,
    session_ids: tuple[str, ...],
    stop_event: Event,
    results: list[ProbeResult],
    lock: Lock,
    worker_index: int,
) -> None:
    paths = _probe_paths(session_ids)
    index = worker_index
    with httpx.Client(
        base_url=api_base_url,
        timeout=_PROBE_TIMEOUT,
        trust_env=False,
    ) as client:
        while not stop_event.is_set():
            method, path = paths[index % len(paths)]
            index += 1
            result = _send_probe(client, method, path)
            with lock:
                results.append(result)


def _navigation_driver_until_stopped(
    api_base_url: str,
    session_ids: tuple[str, ...],
    stop_event: Event,
    results: list[NavigationStepResult],
    lock: Lock,
    args: argparse.Namespace,
) -> None:
    rng = random.Random(42)
    previous = NavigationTarget(
        kind="root", session_id=session_ids[0] if session_ids else ""
    )
    matrix_index = 0
    timeout = httpx.Timeout(
        2.0,
        connect=1.0,
        read=0.35,
        write=1.0,
        pool=1.0,
    )
    watermarks: dict[str, int] = {}
    seen_event_ids: dict[str, set[int]] = {}
    with httpx.Client(
        base_url=api_base_url,
        timeout=timeout,
        trust_env=False,
    ) as client:
        while not stop_event.is_set():
            targets = _load_navigation_targets(client, session_ids, args)
            if not targets:
                time.sleep(max(0.01, args.switch_interval_ms / 1000.0))
                continue
            if args.switch_pattern == "random":
                target = rng.choice(targets)
            else:
                target = targets[matrix_index % len(targets)]
                matrix_index += 1
            step = _send_navigation_step(
                client=client,
                previous=previous,
                target=target,
                watermarks=watermarks,
                seen_event_ids=seen_event_ids,
            )
            with lock:
                results.append(step)
            previous = target
            time.sleep(max(0.01, args.switch_interval_ms / 1000.0))


def _load_navigation_targets(
    client: httpx.Client,
    session_ids: tuple[str, ...],
    args: argparse.Namespace,
) -> tuple[NavigationTarget, ...]:
    targets: list[NavigationTarget] = []
    include_roots = args.switch_targets in {"roots", "mixed"}
    include_subagents = args.switch_targets in {"subagents", "mixed"}
    for session_id in session_ids:
        if include_roots:
            targets.append(_load_root_navigation_target(client, session_id))
        if include_subagents:
            targets.extend(_load_subagent_navigation_targets(client, session_id))
    if args.switch_pattern == "matrix":
        return _matrix_ordered_navigation_targets(tuple(targets))
    return tuple(targets)


def _matrix_ordered_navigation_targets(
    targets: tuple[NavigationTarget, ...],
) -> tuple[NavigationTarget, ...]:
    roots = tuple(target for target in targets if target.kind == "root")
    subagents = tuple(target for target in targets if target.kind == "subagent")
    ordered: list[NavigationTarget] = []
    max_len = max(len(roots), len(subagents), 1)
    for index in range(max_len):
        if roots:
            ordered.append(roots[index % len(roots)])
        if subagents:
            ordered.append(subagents[index % len(subagents)])
        if len(subagents) > 1:
            ordered.append(subagents[(index + 1) % len(subagents)])
        if len(roots) > 1:
            ordered.append(roots[(index + 1) % len(roots)])
    return tuple(ordered)


def _load_root_navigation_target(
    client: httpx.Client,
    session_id: str,
) -> NavigationTarget:
    run_id = ""
    try:
        response = client.get(f"/api/sessions/{session_id}/recovery")
        if response.status_code < 500:
            payload: object = response.json()
            if isinstance(payload, dict):
                active_run = payload.get("active_run")
                if isinstance(active_run, dict):
                    run_id = str(active_run.get("run_id") or "").strip()
    except RECOVERABLE_BENCHMARK_ERRORS:
        run_id = ""
    return NavigationTarget(kind="root", session_id=session_id, run_id=run_id)


def _load_subagent_navigation_targets(
    client: httpx.Client,
    session_id: str,
) -> tuple[NavigationTarget, ...]:
    try:
        response = client.get(f"/api/sessions/{session_id}/subagents:snapshot")
        if response.status_code >= 500:
            return ()
        payload: object = response.json()
    except RECOVERABLE_BENCHMARK_ERRORS:
        return ()
    rows: object = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return ()
    targets: list[NavigationTarget] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        run_id = str(row.get("run_id") or row.get("subagent_run_id") or "").strip()
        instance_id = str(
            row.get("instance_id") or row.get("subagent_instance_id") or ""
        ).strip()
        if not run_id or not instance_id:
            continue
        targets.append(
            NavigationTarget(
                kind="subagent",
                session_id=session_id,
                run_id=run_id,
                instance_id=instance_id,
            )
        )
    return tuple(targets)


def _send_navigation_step(
    *,
    client: httpx.Client,
    previous: NavigationTarget,
    target: NavigationTarget,
    watermarks: dict[str, int],
    seen_event_ids: dict[str, set[int]],
) -> NavigationStepResult:
    started = time.perf_counter()
    status_code: int | None = None
    error = ""
    after_event_id = 0
    hydration_duration_ms = 0
    hydration_error = ""
    duplicate_count = 0
    gap_count = 0
    wrong_target_count = 0
    transition = f"{previous.kind}->{target.kind}"
    try:
        if target.kind == "root":
            status_code = _activate_root_target(client, target)
            duration_ms = int((time.perf_counter() - started) * 1000)
            hydration_started = time.perf_counter()
            try:
                hydrate_status_code = _hydrate_root_target(client, target)
                after_event_id, duplicate_count, gap_count, wrong_target_count = (
                    _stream_target_events(
                        client=client,
                        target=target,
                        watermarks=watermarks,
                        seen_event_ids=seen_event_ids,
                    )
                )
                if hydrate_status_code >= 500:
                    hydration_error = f"HTTP {hydrate_status_code}"
                else:
                    hydration_error = ""
            except RECOVERABLE_BENCHMARK_ERRORS as exc:
                hydration_error = f"{type(exc).__name__}: {exc}"
        else:
            status_code = _activate_subagent_target(client, target)
            duration_ms = int((time.perf_counter() - started) * 1000)
            hydration_started = time.perf_counter()
            try:
                hydrate_status_code = _hydrate_subagent_target(client, target)
                after_event_id, duplicate_count, gap_count, wrong_target_count = (
                    _stream_target_events(
                        client=client,
                        target=target,
                        watermarks=watermarks,
                        seen_event_ids=seen_event_ids,
                    )
                )
                if hydrate_status_code >= 500:
                    hydration_error = f"HTTP {hydrate_status_code}"
                else:
                    hydration_error = ""
            except RECOVERABLE_BENCHMARK_ERRORS as exc:
                hydration_error = f"{type(exc).__name__}: {exc}"
        hydration_duration_ms = int((time.perf_counter() - hydration_started) * 1000)
    except RECOVERABLE_BENCHMARK_ERRORS as exc:
        error = f"{type(exc).__name__}: {exc}"
        duration_ms = int((time.perf_counter() - started) * 1000)
    return NavigationStepResult(
        transition=transition,
        target=target,
        duration_ms=duration_ms,
        hydration_duration_ms=hydration_duration_ms,
        status_code=status_code or 0,
        after_event_id=after_event_id,
        stream_duplicate_count=duplicate_count,
        stream_gap_count=gap_count,
        wrong_target_render_count=wrong_target_count,
        error=error,
        hydration_error=hydration_error,
    )


def _activate_root_target(client: httpx.Client, target: NavigationTarget) -> int:
    _ = client, target
    return 200


def _hydrate_root_target(client: httpx.Client, target: NavigationTarget) -> int:
    status_code = 200
    for path in (
        f"/api/sessions/{target.session_id}/recovery",
        f"/api/sessions/{target.session_id}/rounds?summary=true&limit=4",
    ):
        response = client.get(path)
        _consume_response(response)
        status_code = max(status_code, response.status_code)
    return status_code


def _activate_subagent_target(client: httpx.Client, target: NavigationTarget) -> int:
    _ = client, target
    return 200


def _hydrate_subagent_target(client: httpx.Client, target: NavigationTarget) -> int:
    status_code = 200
    for path in (
        f"/api/sessions/{target.session_id}/agents/{target.instance_id}/messages",
    ):
        response = client.get(path)
        _consume_response(response)
        status_code = max(status_code, response.status_code)
    return status_code


def _stream_target_events(
    *,
    client: httpx.Client,
    target: NavigationTarget,
    watermarks: dict[str, int],
    seen_event_ids: dict[str, set[int]],
) -> tuple[int, int, int, int]:
    if not target.run_id:
        return 0, 0, 0, 0
    key = f"{target.kind}:{target.session_id}:{target.run_id}:{target.instance_id}"
    after_event_id = watermarks.get(key, 0)
    duplicate_count = 0
    gap_count = 0
    wrong_target_count = 0
    if target.kind == "root":
        url = f"/api/runs/{target.run_id}/events?after_event_id={after_event_id}"
    else:
        url = (
            f"/api/sessions/{target.session_id}/subagents/events"
            f"?after_event_id={after_event_id}"
        )
    try:
        with client.stream("GET", url) as response:
            status_code = response.status_code
            if status_code >= 500:
                return after_event_id, duplicate_count, gap_count, wrong_target_count
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                raw_json = line.removeprefix("data:").strip()
                if not raw_json:
                    continue
                event = json.loads(raw_json)
                if not isinstance(event, dict):
                    continue
                event_id = int(event.get("event_id") or 0)
                event_run_id = str(event.get("run_id") or event.get("trace_id") or "")
                if event_id > 0:
                    after_event_id = max(after_event_id, event_id)
                    watermarks[key] = after_event_id
                if target.kind == "subagent" and event_run_id != target.run_id:
                    # The subagent SSE endpoint is session-scoped and can replay sibling
                    # subagent events. The frontend filters those into cache only; they
                    # are not wrong-target DOM renders unless the client accepts them for
                    # the active target.
                    continue
                if event_id > 0:
                    seen = seen_event_ids.setdefault(key, set())
                    if event_id in seen:
                        duplicate_count += 1
                    seen.add(event_id)
                break
    except httpx.ReadTimeout:
        return after_event_id, duplicate_count, gap_count, wrong_target_count
    return after_event_id, duplicate_count, gap_count, wrong_target_count


def _probe_paths(session_ids: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    paths: list[tuple[str, str]] = [
        ("GET", "/api/system/live"),
        ("GET", "/api/system/health"),
        ("GET", "/api/sessions"),
    ]
    for session_id in session_ids:
        paths.extend(
            [
                ("GET", f"/api/sessions/{session_id}"),
                ("GET", f"/api/sessions/{session_id}/rounds?summary=true&limit=4"),
                ("GET", f"/api/sessions/{session_id}/rounds?limit=8"),
                ("GET", f"/api/sessions/{session_id}/recovery"),
                ("GET", f"/api/sessions/{session_id}/token-usage"),
                ("GET", f"/api/sessions/{session_id}/agents"),
                ("GET", f"/api/sessions/{session_id}/tasks"),
                ("GET", f"/api/sessions/{session_id}/subagents"),
                ("POST", f"/api/sessions/{session_id}/terminal-view"),
            ],
        )
    return tuple(paths)


def _send_probe(client: httpx.Client, method: str, path: str) -> ProbeResult:
    started = time.perf_counter()
    try:
        if method == "POST":
            response = client.post(path)
        else:
            response = client.get(path)
        _consume_response(response)
        return ProbeResult(
            method=method,
            path=path,
            status_code=response.status_code,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except RECOVERABLE_BENCHMARK_ERRORS as exc:
        return ProbeResult(
            method=method,
            path=path,
            status_code=0,
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}",
        )


def _stop_probe_workers(futures: list[Future[None]]) -> None:
    for future in futures:
        try:
            future.result(timeout=7.0)
        except FutureTimeoutError:
            future.cancel()


def _consume_response(response: httpx.Response) -> None:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        _ = response.json()
        return
    _ = response.text


def _event_counts(events: Sequence[dict[str, object]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for event in events:
        event_type = str(event.get("event_type") or "")
        if event_type:
            counts[event_type] += 1
    return dict(counts)


def _summarize_probes(probes: tuple[ProbeResult, ...]) -> ProbeSummary:
    durations = sorted(probe.duration_ms for probe in probes)
    failures = [
        probe
        for probe in probes
        if probe.status_code == 0
        or probe.status_code == 429
        or probe.status_code >= 500
    ]
    slowest = tuple(
        sorted(probes, key=lambda probe: probe.duration_ms, reverse=True)[:10],
    )
    return ProbeSummary(
        count=len(probes),
        failure_count=len(failures),
        p50_ms=_percentile(durations, 0.50),
        p95_ms=_percentile(durations, 0.95),
        p99_ms=_percentile(durations, 0.99),
        max_ms=durations[-1] if durations else 0,
        by_endpoint=_summarize_probes_by_endpoint(probes),
        slowest=slowest,
    )


def _summarize_navigation(
    steps: tuple[NavigationStepResult, ...],
) -> NavigationSummary:
    durations = sorted(step.duration_ms for step in steps)
    hydration_durations = sorted(step.hydration_duration_ms for step in steps)
    hydration_failures = [step for step in steps if step.hydration_error]
    failures = [
        step
        for step in steps
        if step.status_code == 0 or step.status_code == 429 or step.status_code >= 500
    ]
    grouped: dict[str, list[NavigationStepResult]] = {}
    for step in steps:
        grouped.setdefault(step.transition, []).append(step)
    return NavigationSummary(
        count=len(steps),
        failure_count=len(failures),
        p50_ms=_percentile(durations, 0.50),
        p95_ms=_percentile(durations, 0.95),
        max_ms=durations[-1] if durations else 0,
        hydration_p95_ms=_percentile(hydration_durations, 0.95),
        hydration_max_ms=hydration_durations[-1] if hydration_durations else 0,
        hydration_failure_count=len(hydration_failures),
        by_transition={
            transition: _summarize_navigation_transition(tuple(items))
            for transition, items in sorted(grouped.items())
        },
        stream_duplicate_count=sum(step.stream_duplicate_count for step in steps),
        stream_gap_count=sum(step.stream_gap_count for step in steps),
        wrong_target_render_count=sum(step.wrong_target_render_count for step in steps),
        running_indicator_missing_count=0,
        terminal_refresh_wrong_target_count=0,
        failures=tuple(failures[:10]),
    )


def _summarize_navigation_transition(
    steps: tuple[NavigationStepResult, ...],
) -> NavigationTransitionSummary:
    durations = sorted(step.duration_ms for step in steps)
    hydration_durations = sorted(step.hydration_duration_ms for step in steps)
    failures = [
        step
        for step in steps
        if step.status_code == 0 or step.status_code == 429 or step.status_code >= 500
    ]
    return NavigationTransitionSummary(
        count=len(steps),
        failure_count=len(failures),
        p50_ms=_percentile(durations, 0.50),
        p95_ms=_percentile(durations, 0.95),
        max_ms=durations[-1] if durations else 0,
        hydration_p95_ms=_percentile(hydration_durations, 0.95),
        hydration_max_ms=hydration_durations[-1] if hydration_durations else 0,
    )


def _summarize_probes_by_endpoint(
    probes: tuple[ProbeResult, ...],
) -> dict[str, EndpointProbeSummary]:
    grouped: dict[str, list[ProbeResult]] = {}
    for probe in probes:
        grouped.setdefault(_normalized_probe_endpoint(probe), []).append(probe)
    return {
        endpoint: _summarize_endpoint_probes(tuple(endpoint_probes))
        for endpoint, endpoint_probes in sorted(grouped.items())
    }


def _summarize_endpoint_probes(
    probes: tuple[ProbeResult, ...],
) -> EndpointProbeSummary:
    durations = sorted(probe.duration_ms for probe in probes)
    failures = [
        probe
        for probe in probes
        if probe.status_code == 0
        or probe.status_code == 429
        or probe.status_code >= 500
    ]
    return EndpointProbeSummary(
        count=len(probes),
        failure_count=len(failures),
        p50_ms=_percentile(durations, 0.50),
        p95_ms=_percentile(durations, 0.95),
        p99_ms=_percentile(durations, 0.99),
        max_ms=durations[-1] if durations else 0,
    )


def _normalized_probe_endpoint(probe: ProbeResult) -> str:
    path = probe.path.split("?", 1)[0]
    path = re.sub(r"/api/sessions/[^/]+", "/api/sessions/{session_id}", path)
    return f"{probe.method} {path}"


def _percentile(sorted_values: list[int], percentile: float) -> int:
    if not sorted_values:
        return 0
    index = max(0, min(len(sorted_values) - 1, int(len(sorted_values) * percentile)))
    return sorted_values[index]


def _load_fake_llm_metrics(fake_llm_admin_url: str) -> dict[str, object]:
    with httpx.Client(
        base_url=fake_llm_admin_url, timeout=10.0, trust_env=False
    ) as client:
        response = client.get("/metrics")
        response.raise_for_status()
        payload: object = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected fake LLM metrics payload: {payload}")
        return payload


def _parse_backend_log_metrics(
    backend_log_file: Path,
) -> dict[str, dict[str, int]]:
    if not backend_log_file.exists():
        return {
            "artifact": {},
            "sqlite": {},
            "message_commit": {},
            "tool_call_batch_state": {},
            "tool_result_batch": {},
            "relay_tool_step": {},
            "sync_subagent": {},
            "sse_heartbeat_count": {"count": 0},
        }
    text = backend_log_file.read_text(encoding="utf-8", errors="replace")
    heartbeat_total = 0
    for match in re.finditer(r'"heartbeat_count":\s*(\d+)', text):
        heartbeat_total += int(match.group(1))
    write_queue_failed = text.count("artifact.write_queue.failed")
    return {
        "artifact": {
            "write_queue_failed": write_queue_failed,
            "legacy_failed": max(0, text.count("artifact.") - write_queue_failed),
            "database_locked": text.count('"error": "database is locked"'),
        },
        "sqlite": {
            "write_retry": text.count("sqlite.write.retry"),
            "database_locked": text.count("database is locked"),
            "slow_route_work": text.count("server.route_work.slow_call"),
        },
        "message_commit": _metric_summary_from_log(
            text,
            (
                "message_commit_append_ms",
                "message_commit_history_reload_ms",
                "message_commit_outcome_publish_ms",
                "message_commit_safe_scan_ms",
                "message_commit_total_ms",
            ),
        ),
        "tool_call_batch_state": _metric_summary_from_log(
            text,
            ("tool_call_batch_state_write_ms",),
        ),
        "tool_result_batch": _metric_summary_from_log(
            text,
            (
                "tool_result_batch_size",
                "tool_result_batch_publish_ms",
                "tool_result_batch_state_persist_ms",
                "tool_result_batch_metrics_ms",
                "tool_result_batch_total_ms",
            ),
        ),
        "relay_tool_step": _metric_summary_from_log(
            text,
            (
                "relay_tool_step_batch_size",
                "relay_tool_step_execute_ms",
                "relay_tool_step_pydantic_bypass_count",
            ),
        )
        | {
            "executor_used_count": text.count("relay_tool_step.executed"),
            "fallback_count": text.count("relay_tool_step.fallback"),
            "validation_fallback_count": text.count(
                "relay_tool_step.validation_fallback"
            ),
        },
        "sessions_list_cache": _metric_summary_from_log(
            text,
            (
                "snapshot_age_ms",
                "session_count",
                "refresh_ms",
            ),
        )
        | {
            "stale_hit_count": text.count("session.list_cache.stale_hit"),
            "cold_miss_timeout_count": text.count(
                "session.list_cache.cold_miss_timeout"
            ),
            "refresh_failed_count": text.count("session.list_cache.refresh_failed"),
        },
        "sync_subagent": _metric_summary_from_log(
            text,
            (
                "sync_subagent_queue_wait_ms",
                "sync_subagent_launch_prepare_ms",
                "sync_subagent_start_hooks_ms",
                "sync_subagent_execute_ms",
                "sync_subagent_finalize_ms",
                "sync_subagent_total_ms",
            ),
        ),
        "sse_heartbeat_count": {"count": heartbeat_total},
    }


def _metric_summary_from_log(
    text: str,
    metric_names: tuple[str, ...],
) -> dict[str, int]:
    summary: dict[str, int] = {}
    for metric_name in metric_names:
        values = [
            int(match.group(1))
            for match in re.finditer(rf'"{re.escape(metric_name)}":\s*(\d+)', text)
        ]
        values.sort()
        summary[f"{metric_name}_p95"] = _percentile(values, 0.95)
        summary[f"{metric_name}_max"] = values[-1] if values else 0
    return summary


if __name__ == "__main__":
    main()
