# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.agents.tasks.artifact_repository import (
    TaskArtifactRepository,
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
