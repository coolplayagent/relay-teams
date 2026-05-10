# -*- coding: utf-8 -*-
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
import sqlite3
from threading import Event
import time
from typing import cast

import pytest

from relay_teams.agents.tasks.artifact_repository import (
    TaskArtifactRepository,
    _TaskArtifactWriteJob,
    _enable_wal_if_available,
    _last_insert_row_id,
)
from relay_teams.agents.tasks.enums import TaskArtifactPhase, VerificationEvidenceKind
from relay_teams.agents.tasks.models import (
    TaskArtifactEntry,
    VerificationEvidenceBundle,
    VerificationEvidenceItem,
)


@pytest.fixture
def repo(tmp_path: Path) -> TaskArtifactRepository:
    return TaskArtifactRepository(tmp_path / "test_artifact.db")


def test_ensure_artifact_creates_new(repo: TaskArtifactRepository):
    artifact = repo.ensure_artifact("task-1", "spec-1")
    assert artifact.task_id == "task-1"
    assert artifact.spec_artifact_id == "spec-1"
    assert artifact.entries == []


def test_ensure_artifact_idempotent(repo: TaskArtifactRepository):
    a1 = repo.ensure_artifact("task-1", "spec-1")
    a2 = repo.ensure_artifact("task-1", "spec-1")
    assert a1.task_id == a2.task_id


def test_ensure_artifact_raises_when_insert_cannot_be_read(
    repo: TaskArtifactRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_artifact(_task_id: str) -> None:
        return None

    monkeypatch.setattr(repo, "get_artifact", missing_artifact)

    with pytest.raises(RuntimeError, match="Failed to create task artifact"):
        repo.ensure_artifact("task-1", "spec-1")


def test_get_artifact_missing(repo: TaskArtifactRepository):
    assert repo.get_artifact("nonexistent") is None


def test_get_artifact_summary_missing(repo: TaskArtifactRepository):
    assert repo.get_artifact_summary("nonexistent") is None


def test_append_entry(repo: TaskArtifactRepository):
    repo.ensure_artifact("task-1", "spec-1")
    entry = TaskArtifactEntry(
        entry_id="entry-1",
        phase=TaskArtifactPhase.EXECUTION,
        timestamp="2024-01-01T00:00:00",
        event_type="tool_call",
        description="Ran shell command",
    )
    row_id = repo.append_entry("task-1", entry)
    assert row_id > 0


@pytest.mark.asyncio
async def test_async_artifact_lifecycle(repo: TaskArtifactRepository) -> None:
    artifact = await repo.ensure_artifact_async("task-async-1", "spec-async-1")
    assert artifact.task_id == "task-async-1"

    row_id = await repo.append_entry_async(
        "task-async-1",
        TaskArtifactEntry(
            entry_id="entry-async-1",
            phase=TaskArtifactPhase.EXECUTION,
            timestamp="2024-01-01T00:00:00",
            event_type="tool_call",
            description="Ran async artifact write",
        ),
    )
    assert row_id > 0

    await repo.update_summary_async("task-async-1", "Async checks passed")
    loaded = await repo.get_artifact_async("task-async-1")
    assert loaded is not None
    assert loaded.summary == "Async checks passed"
    assert len(loaded.entries) == 1

    entries, total = await repo.query_entries_async(task_id="task-async-1")
    assert total == 1
    assert entries[0].entry_id == "entry-async-1"


@pytest.mark.asyncio
async def test_async_artifact_summary_evidence_and_filters(
    repo: TaskArtifactRepository,
) -> None:
    assert await repo.get_artifact_async("missing") is None
    assert await repo.get_artifact_summary_async("missing") is None

    _ = await repo.ensure_artifact_async("task-async-2", "spec-async-2")
    await repo.append_entry_async(
        "task-async-2",
        TaskArtifactEntry(
            entry_id="entry-async-2a",
            phase=TaskArtifactPhase.EXECUTION,
            timestamp="2024-01-01T00:00:00",
            event_type="tool_call",
            description="Ran async tool",
        ),
    )
    await repo.append_entry_async(
        "task-async-2",
        TaskArtifactEntry(
            entry_id="entry-async-2b",
            phase=TaskArtifactPhase.VERIFICATION,
            timestamp="2024-01-01T00:00:01",
            event_type="check",
            description="Verified async tool",
        ),
    )
    bundle = VerificationEvidenceBundle(
        task_id="task-async-2",
        items=(
            VerificationEvidenceItem(
                evidence_id="ev-async-1",
                kind=VerificationEvidenceKind.TASK_RESULT,
                summary="Async task completed",
            ),
        ),
    )
    await repo.update_evidence_bundle_async("task-async-2", bundle)

    artifact = await repo.get_artifact_async("task-async-2")
    assert artifact is not None
    assert artifact.evidence_bundle is not None
    assert len(artifact.evidence_bundle.items) == 1

    summary = await repo.get_artifact_summary_async("task-async-2")
    assert summary is not None
    assert summary.phase_counts == {"execution": 1, "verification": 1}
    assert summary.evidence_item_count == 1
    assert summary.has_verification_bundle is True

    phase_entries, phase_total = await repo.query_entries_async(
        task_id="task-async-2",
        phase=TaskArtifactPhase.VERIFICATION,
    )
    event_entries, event_total = await repo.query_entries_async(
        task_id="task-async-2",
        event_type="tool_call",
    )
    assert phase_total == 1
    assert phase_entries[0].entry_id == "entry-async-2b"
    assert event_total == 1
    assert event_entries[0].entry_id == "entry-async-2a"


@pytest.mark.asyncio
async def test_ensure_artifact_async_raises_when_insert_cannot_be_read(
    repo: TaskArtifactRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def missing_artifact(_task_id: str) -> None:
        return None

    monkeypatch.setattr(repo, "get_artifact_async", missing_artifact)

    with pytest.raises(RuntimeError, match="Failed to create task artifact"):
        await repo.ensure_artifact_async("task-1", "spec-1")


class _WalFailingConnection:
    def execute(self, sql: str) -> object:
        if sql == "PRAGMA journal_mode = WAL":
            raise sqlite3.OperationalError("readonly filesystem")
        raise AssertionError(f"unexpected SQL: {sql}")


class _CursorWithNonIntegerRowId:
    def fetchone(self) -> tuple[str]:
        return ("not-an-int",)


class _NonIntegerRowIdConnection:
    def execute(self, sql: str) -> _CursorWithNonIntegerRowId:
        assert sql == "SELECT last_insert_rowid()"
        return _CursorWithNonIntegerRowId()


def test_enable_wal_logs_and_continues_when_wal_unavailable() -> None:
    _enable_wal_if_available(cast(sqlite3.Connection, _WalFailingConnection()))


def test_last_insert_row_id_rejects_non_integer_value() -> None:
    with pytest.raises(RuntimeError, match="non-integer"):
        _last_insert_row_id(cast(sqlite3.Connection, _NonIntegerRowIdConnection()))


def test_concurrent_append_entry_uses_sqlite_retry_coordination(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent_artifacts.db"
    repo = TaskArtifactRepository(db_path)
    repo.ensure_artifact("task-1", "spec-1")

    def append_entry(index: int) -> int:
        worker_repo = TaskArtifactRepository(db_path)
        return worker_repo.append_entry(
            "task-1",
            TaskArtifactEntry(
                entry_id=f"entry-{index}",
                phase=TaskArtifactPhase.EXECUTION,
                timestamp="2024-01-01T00:00:00",
                event_type="tool_call",
                description=f"Ran shell command {index}",
            ),
        )

    with ThreadPoolExecutor(max_workers=12) as executor:
        row_ids = tuple(executor.map(append_entry, range(36)))

    entries, total = repo.query_entries(task_id="task-1")
    assert len(row_ids) == 36
    assert all(row_id > 0 for row_id in row_ids)
    assert total == 36
    assert len(entries) == 36


def test_queued_artifact_writes_are_persisted(repo: TaskArtifactRepository) -> None:
    accepted = repo.enqueue_ensure_artifact(
        task_id="task-queued",
        spec_artifact_id="spec-queued",
    )
    accepted_entry = repo.enqueue_append_entry(
        task_id="task-queued",
        entry=TaskArtifactEntry(
            entry_id="entry-queued",
            phase=TaskArtifactPhase.EXECUTION,
            timestamp="2024-01-01T00:00:00",
            event_type="tool_call",
            description="Queued write",
        ),
    )

    drained = repo.drain_write_queue(timeout_seconds=2.0)
    artifact = repo.get_artifact("task-queued")
    metrics = repo.write_metrics()

    assert accepted is True
    assert accepted_entry is True
    assert drained is True
    assert artifact is not None
    assert artifact.spec_artifact_id == "spec-queued"
    assert tuple(entry.entry_id for entry in artifact.entries) == ("entry-queued",)
    assert metrics.enqueued == 2
    assert metrics.completed == 2


def test_queued_artifact_summary_and_evidence_updates_are_persisted(
    repo: TaskArtifactRepository,
) -> None:
    repo.ensure_artifact("task-queued-evidence", "spec-queued")
    bundle = VerificationEvidenceBundle(
        task_id="task-queued-evidence",
        items=(
            VerificationEvidenceItem(
                evidence_id="ev-queued",
                kind=VerificationEvidenceKind.TASK_RESULT,
                summary="Queued evidence",
            ),
        ),
    )

    accepted_summary = repo.enqueue_update_summary(
        task_id="task-queued-evidence",
        summary="Queued summary",
    )
    accepted_evidence = repo.enqueue_update_evidence_bundle(
        task_id="task-queued-evidence",
        bundle=bundle,
    )
    repo.close()

    artifact = repo.get_artifact("task-queued-evidence")
    metrics = repo.write_metrics()

    assert accepted_summary is True
    assert accepted_evidence is True
    assert artifact is not None
    assert artifact.summary == "Queued summary"
    assert artifact.evidence_bundle is not None
    assert artifact.evidence_bundle.items[0].evidence_id == "ev-queued"
    assert metrics.completed == 2


def test_close_drains_accepted_artifact_writes_before_stopping_worker(
    repo: TaskArtifactRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_execute = repo._execute_queued_job
    first_job_started = Event()

    def slow_execute(job: _TaskArtifactWriteJob) -> None:
        first_job_started.set()
        time.sleep(0.05)
        original_execute(job)

    monkeypatch.setattr(repo, "_execute_queued_job", slow_execute)

    accepted_first = repo.enqueue_ensure_artifact(
        task_id="task-close-first",
        spec_artifact_id="spec-close",
    )
    accepted_second = repo.enqueue_ensure_artifact(
        task_id="task-close-second",
        spec_artifact_id="spec-close",
    )

    assert first_job_started.wait(timeout=1.0) is True
    repo.close(drain_timeout_seconds=0.001)

    metrics = repo.write_metrics()
    assert accepted_first is True
    assert accepted_second is True
    assert repo.get_artifact("task-close-first") is not None
    assert repo.get_artifact("task-close-second") is not None
    assert metrics.completed == 2


def test_queued_artifact_write_failure_is_recorded(
    repo: TaskArtifactRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_job(_job: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repo, "_execute_queued_job", fail_job)

    accepted = repo.enqueue_ensure_artifact(
        task_id="task-failed",
        spec_artifact_id="",
    )
    drained = repo.drain_write_queue(timeout_seconds=2.0)
    metrics = repo.write_metrics()

    assert accepted is True
    assert drained is True
    assert metrics.failed == 1
    assert metrics.sqlite_lock_timeout_count == 1


def test_queued_artifact_write_drop_is_recorded(
    repo: TaskArtifactRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repo, "_queue", Queue(maxsize=1))
    monkeypatch.setattr(repo, "_ensure_write_worker_started", lambda: None)

    first = repo.enqueue_ensure_artifact(task_id="task-1", spec_artifact_id="")
    second = repo.enqueue_ensure_artifact(task_id="task-2", spec_artifact_id="")
    metrics = repo.write_metrics()

    assert first is True
    assert second is False
    assert metrics.enqueued == 1
    assert metrics.dropped == 1
    assert metrics.queue_length == 1


def test_queued_artifact_job_validation_failures_are_recorded(
    repo: TaskArtifactRepository,
) -> None:
    with pytest.raises(ValueError, match="missing entry"):
        repo._execute_queued_job(
            _TaskArtifactWriteJob(
                operation="append",
                task_id="task-invalid-append",
                enqueued_monotonic=0.0,
            )
        )

    with pytest.raises(ValueError, match="missing bundle"):
        repo._execute_queued_job(
            _TaskArtifactWriteJob(
                operation="update_evidence_bundle",
                task_id="task-invalid-evidence",
                enqueued_monotonic=0.0,
            )
        )


def test_get_artifact_with_entries(repo: TaskArtifactRepository):
    repo.ensure_artifact("task-1", "spec-1")
    entry = TaskArtifactEntry(
        entry_id="entry-1",
        phase=TaskArtifactPhase.EXECUTION,
        timestamp="2024-01-01T00:00:00",
        event_type="tool_call",
        description="Ran shell command",
        linked_evidence_ids=("ev-1", "ev-2"),
    )
    repo.append_entry("task-1", entry)

    artifact = repo.get_artifact("task-1")
    assert artifact is not None
    assert len(artifact.entries) == 1
    assert artifact.entries[0].entry_id == "entry-1"
    assert artifact.entries[0].phase == TaskArtifactPhase.EXECUTION
    assert artifact.entries[0].linked_evidence_ids == ("ev-1", "ev-2")


def test_get_artifact_summary(repo: TaskArtifactRepository):
    repo.ensure_artifact("task-1", "spec-1")
    entry = TaskArtifactEntry(
        entry_id="entry-1",
        phase=TaskArtifactPhase.VERIFICATION,
        timestamp="2024-01-01T00:00:00",
        event_type="check",
        description="Verification check",
    )
    repo.append_entry("task-1", entry)

    summary = repo.get_artifact_summary("task-1")
    assert summary is not None
    assert summary.total_entries == 1
    assert summary.phase_counts.get("verification") == 1


def test_update_evidence_bundle(repo: TaskArtifactRepository):
    repo.ensure_artifact("task-1", "spec-1")
    bundle = VerificationEvidenceBundle(
        task_id="task-1",
        items=(
            VerificationEvidenceItem(
                evidence_id="ev-1",
                kind=VerificationEvidenceKind.TASK_RESULT,
                summary="Task completed",
            ),
        ),
    )
    repo.update_evidence_bundle("task-1", bundle)

    artifact = repo.get_artifact("task-1")
    assert artifact is not None
    assert artifact.evidence_bundle is not None
    assert len(artifact.evidence_bundle.items) == 1

    summary = repo.get_artifact_summary("task-1")
    assert summary is not None
    assert summary.evidence_item_count == 1
    assert summary.has_verification_bundle is True


def test_update_summary(repo: TaskArtifactRepository):
    repo.ensure_artifact("task-1", "spec-1")
    repo.update_summary("task-1", "All checks passed")

    artifact = repo.get_artifact("task-1")
    assert artifact is not None
    assert artifact.summary == "All checks passed"


def test_query_entries_filter_phase(repo: TaskArtifactRepository):
    repo.ensure_artifact("task-1", "spec-1")
    repo.append_entry(
        "task-1",
        TaskArtifactEntry(
            entry_id="e1",
            phase=TaskArtifactPhase.EXECUTION,
            timestamp="2024-01-01T00:00:00",
            event_type="tool",
            description="exec",
        ),
    )
    repo.append_entry(
        "task-1",
        TaskArtifactEntry(
            entry_id="e2",
            phase=TaskArtifactPhase.VERIFICATION,
            timestamp="2024-01-01T00:00:00",
            event_type="check",
            description="verify",
        ),
    )

    entries, total = repo.query_entries(
        task_id="task-1", phase=TaskArtifactPhase.VERIFICATION
    )
    assert total == 1
    assert entries[0].entry_id == "e2"


def test_query_entries_filter_event_type(repo: TaskArtifactRepository):
    repo.ensure_artifact("task-1", "spec-1")
    repo.append_entry(
        "task-1",
        TaskArtifactEntry(
            entry_id="e1",
            phase=TaskArtifactPhase.EXECUTION,
            timestamp="2024-01-01T00:00:00",
            event_type="tool",
            description="exec",
        ),
    )
    repo.append_entry(
        "task-1",
        TaskArtifactEntry(
            entry_id="e2",
            phase=TaskArtifactPhase.EXECUTION,
            timestamp="2024-01-01T00:00:00",
            event_type="file_write",
            description="write",
        ),
    )

    entries, total = repo.query_entries(task_id="task-1", event_type="tool")
    assert total == 1
    assert entries[0].entry_id == "e1"


def test_query_entries_pagination(repo: TaskArtifactRepository):
    repo.ensure_artifact("task-1", "spec-1")
    for i in range(5):
        repo.append_entry(
            "task-1",
            TaskArtifactEntry(
                entry_id=f"e{i}",
                phase=TaskArtifactPhase.EXECUTION,
                timestamp="2024-01-01T00:00:00",
                event_type="tool",
                description=f"exec {i}",
            ),
        )

    entries, total = repo.query_entries(task_id="task-1", limit=2, offset=0)
    assert total == 5
    assert len(entries) == 2


class TestArtifactRowToEntryCoverage:
    """Cover _row_to_entry round-trip."""

    def test_row_to_entry_round_trip(self) -> None:
        from relay_teams.agents.tasks.artifact_repository import (
            TaskArtifactRepository,
        )
        from relay_teams.agents.tasks.models import TaskArtifactEntry
        from relay_teams.agents.tasks.enums import TaskArtifactPhase
        from datetime import datetime, timezone
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            repo = TaskArtifactRepository(Path(td) / "test.db")
            task_id = "row-test-1"
            repo.ensure_artifact(task_id=task_id, spec_artifact_id="")
            entry = TaskArtifactEntry(
                entry_id="e-1",
                phase=TaskArtifactPhase.EXECUTION,
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                role_id="role-1",
                instance_id="inst-1",
                event_type="test_event",
                description="test",
                payload_json='{"key": "value"}',
            )
            repo.append_entry(task_id=task_id, entry=entry)
            entries, total = repo.query_entries(task_id=task_id)
            assert total == 1
            assert entries[0].entry_id == "e-1"


# appended coverage tests
