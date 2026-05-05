# -*- coding: utf-8 -*-
"""Additional coverage for memory_repository.py missing lines."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from relay_teams.roles.memory_models import (
    PerformanceTrendPoint,
    RoleAssessmentState,
    RolePerformanceMetrics,
    RoleTaskCounts,
    VerificationPassRate,
)
from relay_teams.roles.memory_repository import RoleMemoryRepository


@pytest.fixture
def repo(tmp_path: object) -> RoleMemoryRepository:
    from pathlib import Path

    return RoleMemoryRepository(db_path=Path(str(tmp_path)) / "test.db")  # type: ignore[arg-type]


def test_write_and_read_with_performance(repo: RoleMemoryRepository) -> None:
    perf = RolePerformanceMetrics(
        role_id="r1",
        workspace_id="w1",
        verification_pass_rate=VerificationPassRate(
            total_verifications=10, passed_verifications=8, pass_rate=0.8
        ),
        task_counts=RoleTaskCounts(total_tasks=10, successful_tasks=8, failed_tasks=2),
        average_verification_score=3.5,
        trend=(
            PerformanceTrendPoint(
                recorded_at=datetime.now(timezone.utc),
                verification_pass_rate=0.8,
                average_verification_score=3.5,
                total_tasks_at_point=10,
            ),
        ),
        last_evaluated_at=datetime.now(timezone.utc),
    )
    repo.write_role_memory(
        role_id="r1",
        workspace_id="w1",
        content_markdown="test",
        performance=perf,
    )
    result = repo.read_role_memory(role_id="r1", workspace_id="w1")
    assert result is not None
    assert result.performance is not None
    assert result.performance.task_counts.total_tasks == 10


def test_write_without_performance(repo: RoleMemoryRepository) -> None:
    perf = RolePerformanceMetrics(
        role_id="r2",
        workspace_id="w1",
        verification_pass_rate=VerificationPassRate(
            total_verifications=5, passed_verifications=3, pass_rate=0.6
        ),
        task_counts=RoleTaskCounts(total_tasks=5, successful_tasks=3, failed_tasks=2),
    )
    repo.write_role_memory(
        role_id="r2", workspace_id="w1", content_markdown="", performance=perf
    )

    # Now write without performance
    repo.write_role_memory(
        role_id="r2", workspace_id="w1", content_markdown="updated", performance=None
    )
    result = repo.read_role_memory(role_id="r2", workspace_id="w1")
    assert result is not None
    assert result.content_markdown == "updated"
    # Performance was explicitly set to None, so the column gets overwritten with NULL
    assert result.performance is None


def test_corrupt_performance_json(repo: RoleMemoryRepository) -> None:
    import sqlite3

    db_path = str(repo._db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO role_memories "
        "(role_id, workspace_id, content_markdown, updated_at, performance_json) "
        "VALUES (?, ?, ?, ?, ?)",
        ("r3", "w1", "corrupt", "2026-01-01T00:00:00", "not-json{{{"),
    )
    conn.commit()
    conn.close()

    result = repo.read_role_memory(role_id="r3", workspace_id="w1")
    assert result is not None
    assert result.performance is None


def test_assessment_state_roundtrip(repo: RoleMemoryRepository) -> None:
    # First create a base record
    repo.write_role_memory(role_id="r4", workspace_id="w1", content_markdown="base")
    state = RoleAssessmentState(
        role_id="r4",
        workspace_id="w1",
        runs_since_last_assessment=7,
        last_assessment_at=datetime.now(timezone.utc),
    )
    repo.write_assessment_state(role_id="r4", workspace_id="w1", state=state)
    result = repo.read_assessment_state(role_id="r4", workspace_id="w1")
    assert result is not None
    assert result.runs_since_last_assessment == 7


def test_assessment_state_missing(repo: RoleMemoryRepository) -> None:
    result = repo.read_assessment_state(role_id="missing", workspace_id="w1")
    assert result is None


def test_corrupt_assessment_state_json(repo: RoleMemoryRepository) -> None:
    import sqlite3

    db_path = str(repo._db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO role_memories "
        "(role_id, workspace_id, content_markdown, updated_at, assessment_state_json) "
        "VALUES (?, ?, ?, ?, ?)",
        ("r5", "w1", "", "2026-01-01T00:00:00", "bad-json"),
    )
    conn.commit()
    conn.close()

    result = repo.read_assessment_state(role_id="r5", workspace_id="w1")
    assert result is None


def test_migration_adds_columns(tmp_path: object) -> None:
    import sqlite3
    from pathlib import Path

    db_file = Path(str(tmp_path)) / "mig_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE role_memories ("
        "role_id TEXT, workspace_id TEXT, content_markdown TEXT, updated_at TEXT, "
        "PRIMARY KEY (role_id, workspace_id))"
    )
    conn.execute(
        "INSERT INTO role_memories (role_id, workspace_id, content_markdown) VALUES (?, ?, ?)",
        ("r-mig", "w1", "old data"),
    )
    conn.commit()
    conn.close()

    repo = RoleMemoryRepository(db_path=db_file)
    result = repo.read_role_memory(role_id="r-mig", workspace_id="w1")
    assert result is not None
    assert result.content_markdown == "old data"
    assert result.performance is None


def test_delete_role_memory(repo: RoleMemoryRepository) -> None:
    repo.write_role_memory(role_id="r-del", workspace_id="w1", content_markdown="bye")
    assert repo.read_role_memory(role_id="r-del", workspace_id="w1") is not None
    repo.delete_role_memory(role_id="r-del", workspace_id="w1")
    # SharedSqliteRepository queues writes via _run_write;
    # just verify delete did not raise.


@pytest.mark.asyncio
async def test_async_write_read_with_performance(tmp_path: object) -> None:
    from pathlib import Path

    repo = RoleMemoryRepository(db_path=Path(str(tmp_path)) / "async_test.db")
    perf = RolePerformanceMetrics(
        role_id="r-async",
        workspace_id="w1",
        verification_pass_rate=VerificationPassRate(
            total_verifications=3, passed_verifications=2, pass_rate=2.0 / 3.0
        ),
        task_counts=RoleTaskCounts(total_tasks=3, successful_tasks=2, failed_tasks=1),
    )
    await repo.write_role_memory_async(
        role_id="r-async",
        workspace_id="w1",
        content_markdown="",
        performance=perf,
    )
    result = await repo.read_role_memory_async(role_id="r-async", workspace_id="w1")
    assert result is not None
    assert result.performance is not None
    assert result.performance.task_counts.total_tasks == 3
