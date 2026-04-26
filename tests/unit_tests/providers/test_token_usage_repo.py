# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Barrier

from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)


def test_token_usage_repo_migrates_legacy_schema_before_recording(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "token_usage_legacy.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE token_usage (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id    TEXT NOT NULL,
                run_id        TEXT NOT NULL,
                instance_id   TEXT NOT NULL,
                role_id       TEXT NOT NULL,
                input_tokens  INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                recorded_at   TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    repo = TokenUsageRepository(db_path)
    repo.record(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-1",
        role_id="role-1",
        input_tokens=10,
        cached_input_tokens=6,
        output_tokens=4,
        reasoning_output_tokens=2,
        requests=2,
        tool_calls=1,
    )

    columns = {
        str(row["name"])
        for row in repo._conn.execute("PRAGMA table_info(token_usage)").fetchall()
    }
    row = repo._conn.execute(
        """
        SELECT
            input_tokens,
            cached_input_tokens,
            latest_input_tokens,
            max_input_tokens,
            output_tokens,
            reasoning_output_tokens,
            requests,
            tool_calls,
            context_window,
            model_profile
        FROM token_usage
        WHERE run_id=?
        """,
        ("run-1",),
    ).fetchone()

    assert "requests" in columns
    assert "tool_calls" in columns
    assert row is not None
    assert int(row["input_tokens"]) == 10
    assert int(row["cached_input_tokens"]) == 6
    assert int(row["latest_input_tokens"]) == 10
    assert int(row["max_input_tokens"]) == 10
    assert int(row["output_tokens"]) == 4
    assert int(row["reasoning_output_tokens"]) == 2
    assert int(row["requests"]) == 2
    assert int(row["tool_calls"]) == 1
    assert int(row["context_window"]) == 0
    assert row["model_profile"] == ""


def test_token_usage_repo_treats_null_numeric_values_as_zero(tmp_path: Path) -> None:
    db_path = tmp_path / "token_usage_nulls.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE token_usage (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id    TEXT NOT NULL,
                run_id        TEXT NOT NULL,
                instance_id   TEXT NOT NULL,
                role_id       TEXT NOT NULL,
                input_tokens  INTEGER DEFAULT 0,
                cached_input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                reasoning_output_tokens INTEGER DEFAULT 0,
                requests      INTEGER DEFAULT 0,
                tool_calls    INTEGER DEFAULT 0,
                recorded_at   TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO token_usage (
                session_id,
                run_id,
                instance_id,
                role_id,
                input_tokens,
                cached_input_tokens,
                output_tokens,
                reasoning_output_tokens,
                requests,
                tool_calls,
                recorded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "session-1",
                "run-1",
                "inst-1",
                "role-1",
                None,
                None,
                7,
                None,
                None,
                None,
                "2026-03-12T09:16:31+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    repo = TokenUsageRepository(db_path)

    run_usage = repo.get_by_run("run-1")
    session_usage = repo.get_by_session("session-1")

    assert run_usage.total_input_tokens == 0
    assert run_usage.total_cached_input_tokens == 0
    assert run_usage.total_output_tokens == 7
    assert run_usage.total_reasoning_output_tokens == 0
    assert run_usage.total_tokens == 7
    assert run_usage.total_requests == 0
    assert run_usage.total_tool_calls == 0
    assert run_usage.by_agent[0].input_tokens == 0
    assert run_usage.by_agent[0].cached_input_tokens == 0
    assert run_usage.by_agent[0].output_tokens == 7
    assert run_usage.by_agent[0].reasoning_output_tokens == 0
    assert run_usage.by_agent[0].requests == 0
    assert run_usage.by_agent[0].tool_calls == 0

    assert session_usage.total_input_tokens == 0
    assert session_usage.total_cached_input_tokens == 0
    assert session_usage.total_output_tokens == 7
    assert session_usage.total_reasoning_output_tokens == 0
    assert session_usage.total_tokens == 7
    assert session_usage.total_requests == 0
    assert session_usage.total_tool_calls == 0
    assert session_usage.by_role["role-1"].input_tokens == 0
    assert session_usage.by_role["role-1"].cached_input_tokens == 0
    assert session_usage.by_role["role-1"].output_tokens == 7
    assert session_usage.by_role["role-1"].reasoning_output_tokens == 0
    assert session_usage.by_role["role-1"].requests == 0
    assert session_usage.by_role["role-1"].tool_calls == 0


def test_token_usage_repo_serializes_concurrent_reads(tmp_path: Path) -> None:
    repo = TokenUsageRepository(tmp_path / "token_usage_concurrent.db")
    repo.record(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-1",
        role_id="role-1",
        input_tokens=11,
        cached_input_tokens=5,
        output_tokens=5,
        reasoning_output_tokens=3,
        requests=2,
        tool_calls=1,
    )

    errors: list[str] = []
    totals: list[int] = []

    def worker(barrier: Barrier) -> int:
        barrier.wait()
        return repo.get_by_run("run-1").total_tokens

    for _ in range(8):
        barrier = Barrier(16)
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(worker, barrier) for _ in range(16)]
            for future in as_completed(futures):
                try:
                    totals.append(future.result())
                except Exception as exc:  # noqa: BLE001
                    errors.append(repr(exc))

    assert errors == []
    assert totals
    assert all(total == 16 for total in totals)


def test_token_usage_repo_aggregates_cached_and_reasoning_tokens(
    tmp_path: Path,
) -> None:
    repo = TokenUsageRepository(tmp_path / "token_usage_aggregate.db")
    repo.record(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-coordinator",
        role_id="coordinator",
        input_tokens=120,
        cached_input_tokens=70,
        output_tokens=20,
        reasoning_output_tokens=9,
        requests=1,
        tool_calls=2,
    )
    repo.record(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-subagent",
        role_id="researcher",
        input_tokens=30,
        cached_input_tokens=12,
        output_tokens=11,
        reasoning_output_tokens=4,
        requests=1,
        tool_calls=0,
    )

    run_usage = repo.get_by_run("run-1")
    session_usage = repo.get_by_session("session-1")

    assert run_usage.total_input_tokens == 150
    assert run_usage.total_cached_input_tokens == 82
    assert run_usage.total_output_tokens == 31
    assert run_usage.total_reasoning_output_tokens == 13
    assert session_usage.total_input_tokens == 150
    assert session_usage.total_cached_input_tokens == 82
    assert session_usage.total_output_tokens == 31
    assert session_usage.total_reasoning_output_tokens == 13


def test_token_usage_repo_tracks_latest_and_peak_request_input_tokens(
    tmp_path: Path,
) -> None:
    repo = TokenUsageRepository(tmp_path / "token_usage_latest.db")
    repo.record(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-coordinator",
        role_id="coordinator",
        input_tokens=120,
        latest_input_tokens=70,
        max_input_tokens=70,
        output_tokens=20,
        requests=2,
        context_window=1_000_000,
        model_profile="gpt-4.1",
    )
    repo.record(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-coordinator",
        role_id="coordinator",
        input_tokens=30,
        latest_input_tokens=30,
        max_input_tokens=80,
        output_tokens=11,
        requests=1,
        context_window=1_000_000,
        model_profile="gpt-4.1",
    )

    run_usage = repo.get_by_run("run-1")
    session_usage = repo.get_by_session("session-1")

    agent_usage = run_usage.by_agent[0]
    role_usage = session_usage.by_role["coordinator"]
    assert run_usage.total_input_tokens == 150
    assert agent_usage.input_tokens == 150
    assert agent_usage.latest_input_tokens == 30
    assert agent_usage.max_input_tokens == 80
    assert agent_usage.context_window == 1_000_000
    assert agent_usage.model_profile == "gpt-4.1"
    assert role_usage.latest_input_tokens == 30
    assert role_usage.max_input_tokens == 80
    assert role_usage.context_window == 1_000_000
    assert role_usage.model_profile == "gpt-4.1"


def test_token_usage_repo_latest_row_can_clear_context_metadata(
    tmp_path: Path,
) -> None:
    repo = TokenUsageRepository(tmp_path / "token_usage_context_clear.db")
    repo.record(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-coordinator",
        role_id="coordinator",
        input_tokens=120,
        latest_input_tokens=70,
        max_input_tokens=70,
        output_tokens=20,
        requests=2,
        context_window=1_000_000,
        model_profile="gpt-4.1",
    )
    repo.record(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-coordinator",
        role_id="coordinator",
        input_tokens=30,
        latest_input_tokens=30,
        max_input_tokens=80,
        output_tokens=11,
        requests=1,
    )

    agent_usage = repo.get_by_run("run-1").by_agent[0]
    role_usage = repo.get_by_session("session-1").by_role["coordinator"]

    assert agent_usage.latest_input_tokens == 30
    assert agent_usage.max_input_tokens == 80
    assert agent_usage.context_window is None
    assert agent_usage.model_profile == ""
    assert role_usage.latest_input_tokens == 30
    assert role_usage.max_input_tokens == 80
    assert role_usage.context_window is None
    assert role_usage.model_profile == ""


def test_token_usage_repo_filters_pre_clear_usage_from_session_totals(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "token_usage_markers.db"
    marker_repo = SessionHistoryMarkerRepository(db_path)
    repo = TokenUsageRepository(
        db_path,
        session_history_marker_repo=marker_repo,
    )
    repo.record(
        session_id="session-1",
        run_id="run-old",
        instance_id="inst-1",
        role_id="coordinator",
        input_tokens=20,
        output_tokens=5,
    )
    marker_repo.create_clear_marker("session-1")
    repo.record(
        session_id="session-1",
        run_id="run-new",
        instance_id="inst-1",
        role_id="coordinator",
        input_tokens=7,
        output_tokens=3,
    )

    active_usage = repo.get_by_session("session-1")
    historical_usage = repo.get_by_session("session-1", include_cleared=True)
    old_run_usage = repo.get_by_run("run-old")

    assert active_usage.total_input_tokens == 7
    assert active_usage.total_output_tokens == 3
    assert active_usage.total_tokens == 10
    assert historical_usage.total_input_tokens == 27
    assert historical_usage.total_output_tokens == 8
    assert old_run_usage.total_tokens == 25
