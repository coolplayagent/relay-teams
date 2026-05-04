# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.tasks.artifact_repository import TaskArtifactRepository
from relay_teams.agents.tasks.enums import TaskArtifactPhase
from relay_teams.agents.tasks.models import (
    TaskArtifact,
    TaskArtifactEntry,
    TaskArtifactSummary,
)


class ArtifactQueryService:
    """Thin service layer exposing artifact read operations for the API.

    Routers depend on this service rather than the repository directly,
    keeping the module boundary clean.
    """

    def __init__(self, artifact_repo: TaskArtifactRepository) -> None:
        self._repo = artifact_repo

    def get_artifact(self, task_id: str) -> TaskArtifact | None:
        return self._repo.get_artifact(task_id)

    def get_artifact_summary(self, task_id: str) -> TaskArtifactSummary | None:
        return self._repo.get_artifact_summary(task_id)

    def query_entries(
        self,
        task_id: str,
        phase: TaskArtifactPhase | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[TaskArtifactEntry], int]:
        return self._repo.query_entries(
            task_id=task_id,
            phase=phase,
            event_type=event_type,
            limit=limit,
            offset=offset,
        )
