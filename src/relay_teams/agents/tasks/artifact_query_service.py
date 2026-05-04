# -*- coding: utf-8 -*-
from __future__ import annotations

import json as _json

from relay_teams.agents.tasks.artifact_repository import TaskArtifactRepository
from relay_teams.agents.tasks.enums import TaskArtifactPhase
from relay_teams.agents.tasks.models import (
    TaskArtifact,
    TaskArtifactEntry,
    TaskArtifactSnapshot,
    TaskArtifactSummary,
)


def _extract_spec_summary(entries: list[TaskArtifactEntry]) -> str:
    """Extract a spec summary from the first spec-phase artifact entry."""
    if not entries:
        return ""
    entry = entries[0]
    payload_str = entry.payload_json
    if not payload_str:
        return ""
    try:
        data = _json.loads(payload_str)
        return str(
            data.get("objective", "")
            or data.get("title", "")
            or data.get("summary", "")
        )
    except (ValueError, TypeError):
        return ""


def _extract_verification_report_summary(
    entries: list[TaskArtifactEntry],
) -> str:
    """Extract verification report summary text."""
    for entry in entries:
        if entry.event_type == "verification_report":
            payload_str = entry.payload_json
            if payload_str:
                try:
                    data = _json.loads(payload_str)
                    return str(
                        data.get("summary", "")
                        or data.get("message", "")
                        or data.get("details", "")
                    )
                except (ValueError, TypeError):
                    pass
    return ""


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

    def build_snapshot(self, task_id: str) -> TaskArtifactSnapshot | None:
        """Build a normalized read-only snapshot for Gater consumption."""
        artifact = self._repo.get_artifact(task_id)
        if artifact is None:
            return None

        spec_entries: list[TaskArtifactEntry] = []
        execution_entries: list[TaskArtifactEntry] = []
        verification_entries: list[TaskArtifactEntry] = []
        delivery_entries: list[TaskArtifactEntry] = []

        for entry in artifact.entries:
            phase = entry.phase
            if phase == TaskArtifactPhase.SPEC:
                spec_entries.append(entry)
            elif phase == TaskArtifactPhase.EXECUTION:
                execution_entries.append(entry)
            elif phase == TaskArtifactPhase.VERIFICATION:
                verification_entries.append(entry)
            elif phase == TaskArtifactPhase.DELIVERY:
                delivery_entries.append(entry)

        report_summary = _extract_verification_report_summary(verification_entries)

        return TaskArtifactSnapshot(
            task_id=task_id,
            spec_summary=_extract_spec_summary(spec_entries),
            execution_entries=tuple(execution_entries),
            verification_entries=tuple(verification_entries),
            delivery_entries=tuple(delivery_entries),
            evidence_bundle=None,
            verification_report_summary=report_summary,
            total_entries=len(artifact.entries),
        )
