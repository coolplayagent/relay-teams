# -*- coding: utf-8 -*-
from __future__ import annotations

import json

import pytest

from relay_teams.agents.tasks.artifact_query_service import (
    ArtifactQueryService,
)
from relay_teams.agents.tasks.artifact_repository import TaskArtifactRepository
from relay_teams.agents.tasks.enums import TaskArtifactPhase
from relay_teams.agents.tasks.models import (
    TaskArtifactEntry,
    TaskArtifactSnapshot,
)


def _make_entry(
    phase: TaskArtifactPhase,
    event_type: str = "generic",
    payload: dict[str, object] | None = None,
) -> TaskArtifactEntry:
    return TaskArtifactEntry(
        entry_id=f"entry_{phase.value}_{event_type}",
        phase=phase,
        event_type=event_type,
        payload_json=json.dumps(payload or {}),
    )


@pytest.fixture
def artifact_repo(tmp_path: object) -> TaskArtifactRepository:
    from pathlib import Path

    return TaskArtifactRepository(Path(str(tmp_path)) / "test_artifacts.db")


@pytest.fixture
def query_service(artifact_repo: TaskArtifactRepository) -> ArtifactQueryService:
    return ArtifactQueryService(artifact_repo)


class TestArtifactQueryServiceSnapshot:
    def test_build_snapshot_empty(self, query_service: ArtifactQueryService) -> None:
        result = query_service.build_snapshot("nonexistent")
        assert result is None

    def test_build_snapshot_with_entries(
        self, query_service: ArtifactQueryService, artifact_repo: TaskArtifactRepository
    ) -> None:
        artifact_repo.ensure_artifact("task_snap_1", spec_artifact_id="spec_1")
        artifact_repo.append_entry(
            "task_snap_1",
            _make_entry(
                TaskArtifactPhase.SPEC,
                "spec_created",
                {"objective": "Build feature X"},
            ),
        )
        artifact_repo.append_entry(
            "task_snap_1",
            _make_entry(TaskArtifactPhase.EXECUTION, "tool_call"),
        )
        artifact_repo.append_entry(
            "task_snap_1",
            _make_entry(
                TaskArtifactPhase.VERIFICATION,
                "verification_report",
                {"summary": "All checks passed"},
            ),
        )

        snapshot = query_service.build_snapshot("task_snap_1")
        assert snapshot is not None
        assert snapshot.task_id == "task_snap_1"
        assert len(snapshot.execution_entries) == 1
        assert len(snapshot.verification_entries) == 1
        assert snapshot.total_entries == 3

    def test_task_artifact_snapshot_model(self) -> None:
        snapshot = TaskArtifactSnapshot(
            task_id="t1",
            spec_summary="Build X",
            execution_entries=(),
            verification_entries=(),
            delivery_entries=(),
            total_entries=0,
        )
        assert snapshot.task_id == "t1"
        assert snapshot.spec_summary == "Build X"
