# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.agents.tasks.artifact_repository import TaskArtifactRepository
from relay_teams.agents.tasks.enums import TaskArtifactPhase
from relay_teams.agents.tasks.models import TaskArtifactEntry


@pytest.fixture
def artifact_repo(tmp_path: Path) -> TaskArtifactRepository:
    return TaskArtifactRepository(tmp_path / "test_lifecycle.db")


class TestArtifactLifecycleCreation:
    """Tests that the artifact lifecycle methods work as the wiring in
    task_execution_service expects."""

    def test_ensure_then_append_spec_entry(
        self, artifact_repo: TaskArtifactRepository
    ) -> None:
        artifact_repo.ensure_artifact(task_id="task-1", spec_artifact_id="spec-1")
        artifact_repo.append_entry(
            task_id="task-1",
            entry=TaskArtifactEntry(
                entry_id="start-task-1",
                phase=TaskArtifactPhase.SPEC,
                timestamp="2024-01-01T00:00:00+00:00",
                role_id="crafter",
                instance_id="inst-1",
                event_type="task_started",
                description="Task execution started",
                payload_json="{}",
            ),
        )

        artifact = artifact_repo.get_artifact("task-1")
        assert artifact is not None
        assert len(artifact.entries) == 1
        assert artifact.entries[0].phase == TaskArtifactPhase.SPEC

    def test_full_lifecycle_entries(
        self, artifact_repo: TaskArtifactRepository
    ) -> None:
        task_id = "task-lifecycle"
        role_id = "crafter"
        instance_id = "inst-lc"

        # Phase 1: Creation / spec
        artifact_repo.ensure_artifact(task_id=task_id, spec_artifact_id="spec-lc")
        artifact_repo.append_entry(
            task_id=task_id,
            entry=TaskArtifactEntry(
                entry_id=f"start-{task_id}",
                phase=TaskArtifactPhase.SPEC,
                timestamp="2024-01-01T00:00:00+00:00",
                role_id=role_id,
                instance_id=instance_id,
                event_type="task_started",
                description="Task execution started",
                payload_json="{}",
            ),
        )

        # Phase 2: Implementation start
        artifact_repo.append_entry(
            task_id=task_id,
            entry=TaskArtifactEntry(
                entry_id=f"impl-{task_id}",
                phase=TaskArtifactPhase.EXECUTION,
                timestamp="2024-01-01T00:01:00+00:00",
                role_id=role_id,
                instance_id=instance_id,
                event_type="llm_execution_start",
                description="LLM execution started",
                payload_json="{}",
            ),
        )

        # Phase 3: Verification complete
        artifact_repo.append_entry(
            task_id=task_id,
            entry=TaskArtifactEntry(
                entry_id=f"verify-{task_id}",
                phase=TaskArtifactPhase.VERIFICATION,
                timestamp="2024-01-01T00:02:00+00:00",
                role_id=role_id,
                instance_id=instance_id,
                event_type="guardrail_report_completed",
                description="Guardrail report completed",
                payload_json="{}",
            ),
        )

        # Phase 4: Delivery / completion
        artifact_repo.append_entry(
            task_id=task_id,
            entry=TaskArtifactEntry(
                entry_id=f"delivery-{task_id}",
                phase=TaskArtifactPhase.DELIVERY,
                timestamp="2024-01-01T00:03:00+00:00",
                role_id=role_id,
                instance_id=instance_id,
                event_type="task_completed",
                description="Task execution completed",
                payload_json="{}",
            ),
        )
        artifact_repo.update_summary(task_id=task_id, summary="All tests passed")

        # Verify full artifact
        artifact = artifact_repo.get_artifact(task_id)
        assert artifact is not None
        assert len(artifact.entries) == 4
        assert artifact.summary == "All tests passed"

        phases = [e.phase for e in artifact.entries]
        assert phases == [
            TaskArtifactPhase.SPEC,
            TaskArtifactPhase.EXECUTION,
            TaskArtifactPhase.VERIFICATION,
            TaskArtifactPhase.DELIVERY,
        ]

        summary = artifact_repo.get_artifact_summary(task_id)
        assert summary is not None
        assert summary.total_entries == 4

    def test_lifecycle_query_by_phase(
        self, artifact_repo: TaskArtifactRepository
    ) -> None:
        task_id = "task-query"
        artifact_repo.ensure_artifact(task_id=task_id, spec_artifact_id="")
        for phase in (
            TaskArtifactPhase.SPEC,
            TaskArtifactPhase.EXECUTION,
            TaskArtifactPhase.EXECUTION,
            TaskArtifactPhase.VERIFICATION,
            TaskArtifactPhase.DELIVERY,
        ):
            artifact_repo.append_entry(
                task_id=task_id,
                entry=TaskArtifactEntry(
                    entry_id=f"e-{phase.value}",
                    phase=phase,
                    timestamp="2024-01-01T00:00:00+00:00",
                    event_type="test",
                    description="test",
                ),
            )

        entries, total = artifact_repo.query_entries(
            task_id=task_id, phase=TaskArtifactPhase.EXECUTION
        )
        assert total == 2

        all_entries, all_total = artifact_repo.query_entries(task_id=task_id)
        assert all_total == 5

    def test_ensure_artifact_idempotent_no_duplicate_entries(
        self, artifact_repo: TaskArtifactRepository
    ) -> None:
        task_id = "task-idem"
        artifact_repo.ensure_artifact(task_id=task_id, spec_artifact_id="spec-1")
        artifact_repo.ensure_artifact(task_id=task_id, spec_artifact_id="spec-1")

        artifact_repo.append_entry(
            task_id=task_id,
            entry=TaskArtifactEntry(
                entry_id="e1",
                phase=TaskArtifactPhase.SPEC,
                timestamp="2024-01-01T00:00:00+00:00",
                event_type="test",
                description="test",
            ),
        )

        artifact = artifact_repo.get_artifact(task_id)
        assert artifact is not None
        assert len(artifact.entries) == 1
