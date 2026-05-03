# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from relay_teams.agents.tasks.enums import TaskArtifactPhase
from relay_teams.agents.tasks.models import (
    TaskArtifact,
    TaskArtifactEntry,
    TaskArtifactSummary,
    VerificationEvidenceBundle,
)

_CREATE_TABLES_SQL = """\
CREATE TABLE IF NOT EXISTS task_artifacts (
    task_id TEXT NOT NULL PRIMARY KEY,
    spec_artifact_id TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    evidence_bundle_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_artifact_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    role_id TEXT NOT NULL DEFAULT '',
    instance_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    linked_evidence_ids TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_artifact_entries_task_id
    ON task_artifact_entries(task_id);
CREATE INDEX IF NOT EXISTS idx_artifact_entries_phase
    ON task_artifact_entries(phase);
CREATE INDEX IF NOT EXISTS idx_artifact_entries_event_type
    ON task_artifact_entries(event_type);
"""


class TaskArtifactRepository:
    """Persist and query task artifacts and entries."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.executescript(_CREATE_TABLES_SQL)
            conn.commit()
        finally:
            conn.close()

    def get_artifact(self, task_id: str) -> TaskArtifact | None:
        """Load the full artifact including all entries."""
        conn = sqlite3.connect(str(self._db_path))
        try:
            row = conn.execute(
                "SELECT * FROM task_artifacts WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return None

            columns = [
                desc[0]
                for desc in conn.execute(
                    "SELECT * FROM task_artifacts WHERE task_id = ?",
                    (task_id,),
                ).description
            ]
            artifact_row = dict(zip(columns, row))

            entry_rows = conn.execute(
                "SELECT * FROM task_artifact_entries WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
            columns_e = [
                desc[0]
                for desc in conn.execute(
                    "SELECT * FROM task_artifact_entries "
                    "WHERE task_id = ? ORDER BY id ASC",
                    (task_id,),
                ).description
            ]
            entries = [self._row_to_entry(dict(zip(columns_e, r))) for r in entry_rows]

            evidence_bundle = None
            eb_json = artifact_row.get("evidence_bundle_json")
            if eb_json:
                try:
                    evidence_bundle = VerificationEvidenceBundle.model_validate_json(
                        eb_json
                    )
                except (json.JSONDecodeError, ValueError):
                    pass

            return TaskArtifact(
                task_id=artifact_row["task_id"],
                spec_artifact_id=artifact_row.get("spec_artifact_id", ""),
                entries=entries,
                evidence_bundle=evidence_bundle,
                summary=artifact_row.get("summary", ""),
                created_at=artifact_row.get("created_at", ""),
                updated_at=artifact_row.get("updated_at", ""),
            )
        finally:
            conn.close()

    def get_artifact_summary(self, task_id: str) -> TaskArtifactSummary | None:
        """Compute and return a summary view of the artifact."""
        artifact = self.get_artifact(task_id)
        if artifact is None:
            return None

        phase_counts: dict[str, int] = {}
        for entry in artifact.entries:
            phase = entry.phase.value
            phase_counts[phase] = phase_counts.get(phase, 0) + 1

        evidence_count = 0
        if artifact.evidence_bundle is not None:
            evidence_count = len(artifact.evidence_bundle.items)

        return TaskArtifactSummary(
            task_id=artifact.task_id,
            spec_artifact_id=artifact.spec_artifact_id,
            total_entries=len(artifact.entries),
            phase_counts=phase_counts,
            evidence_item_count=evidence_count,
            has_verification_bundle=artifact.evidence_bundle is not None,
            has_summary=bool(artifact.summary),
            created_at=artifact.created_at,
            updated_at=artifact.updated_at,
        )

    def ensure_artifact(self, task_id: str, spec_artifact_id: str) -> TaskArtifact:
        """Create the artifact record if it does not exist."""
        existing = self.get_artifact(task_id)
        if existing is not None:
            return existing

        now = datetime.now(tz=timezone.utc).isoformat()
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "INSERT INTO task_artifacts "
                "(task_id, spec_artifact_id, summary, "
                "created_at, updated_at) "
                "VALUES (?, ?, '', ?, ?)",
                (task_id, spec_artifact_id, now, now),
            )
            conn.commit()
        finally:
            conn.close()

        return TaskArtifact(
            task_id=task_id,
            spec_artifact_id=spec_artifact_id,
            entries=[],
            created_at=now,
            updated_at=now,
        )

    def append_entry(
        self,
        task_id: str,
        entry: TaskArtifactEntry,
    ) -> int:
        """Append an entry to the artifact."""
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "INSERT INTO task_artifact_entries "
                "(entry_id, task_id, phase, timestamp, role_id, "
                "instance_id, event_type, description, payload_json, "
                "linked_evidence_ids) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.entry_id,
                    task_id,
                    entry.phase.value,
                    entry.timestamp,
                    entry.role_id,
                    entry.instance_id,
                    entry.event_type,
                    entry.description,
                    entry.payload_json,
                    json.dumps(list(entry.linked_evidence_ids)),
                ),
            )
            now = datetime.now(tz=timezone.utc).isoformat()
            conn.execute(
                "UPDATE task_artifacts SET updated_at = ? WHERE task_id = ?",
                (now, task_id),
            )
            conn.commit()
            cursor = conn.execute("SELECT last_insert_rowid()")
            row_id = cursor.fetchone()[0]
            return row_id
        finally:
            conn.close()

    def update_evidence_bundle(
        self,
        task_id: str,
        bundle: VerificationEvidenceBundle,
    ) -> None:
        """Update the evidence bundle for an artifact."""
        now = datetime.now(tz=timezone.utc).isoformat()
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "UPDATE task_artifacts "
                "SET evidence_bundle_json = ?, updated_at = ? "
                "WHERE task_id = ?",
                (bundle.model_dump_json(), now, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def update_summary(
        self,
        task_id: str,
        summary: str,
    ) -> None:
        """Update the summary text for an artifact."""
        now = datetime.now(tz=timezone.utc).isoformat()
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "UPDATE task_artifacts SET summary = ?, updated_at = ? "
                "WHERE task_id = ?",
                (summary, now, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def query_entries(
        self,
        *,
        task_id: str,
        phase: TaskArtifactPhase | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[TaskArtifactEntry], int]:
        """Query artifact entries with optional filters.

        Returns (entries, total_count).
        """
        conn = sqlite3.connect(str(self._db_path))
        try:
            conditions: list[str] = ["task_id = ?"]
            params: list[object] = [task_id]

            if phase is not None:
                conditions.append("phase = ?")
                params.append(phase.value)
            if event_type is not None:
                conditions.append("event_type = ?")
                params.append(event_type)

            where = " AND ".join(conditions)
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM task_artifact_entries WHERE {where}",
                tuple(params),
            ).fetchone()
            total = count_row[0] if count_row else 0

            params.append(limit)
            params.append(offset)
            rows = conn.execute(
                f"SELECT * FROM task_artifact_entries "
                f"WHERE {where} "
                f"ORDER BY id ASC LIMIT ? OFFSET ?",
                tuple(params),
            ).fetchall()
            col_names = [
                desc[0]
                for desc in conn.execute(
                    f"SELECT * FROM task_artifact_entries "
                    f"WHERE {where} "
                    f"ORDER BY id ASC LIMIT ? OFFSET ?",
                    tuple(params),
                ).description
            ]
            entries = [self._row_to_entry(dict(zip(col_names, r))) for r in rows]
            return entries, total
        finally:
            conn.close()

    @staticmethod
    def _row_to_entry(raw: dict[str, object]) -> TaskArtifactEntry:
        linked_raw = raw.get("linked_evidence_ids", "[]")
        if isinstance(linked_raw, str):
            try:
                linked = tuple(json.loads(linked_raw))
            except json.JSONDecodeError:
                linked = ()
        elif isinstance(linked_raw, (list, tuple)):
            linked = tuple(str(x) for x in linked_raw)
        else:
            linked = ()

        payload_raw = raw.get("payload_json", "{}")
        if isinstance(payload_raw, str):
            payload = payload_raw
        elif isinstance(payload_raw, dict):
            payload = json.dumps(payload_raw)
        else:
            payload = "{}"

        return TaskArtifactEntry(
            entry_id=str(raw.get("entry_id", "")),
            phase=TaskArtifactPhase(
                str(
                    raw.get(
                        "phase",
                        TaskArtifactPhase.EXECUTION.value,
                    )
                )
            ),
            timestamp=str(raw.get("timestamp", "")),
            role_id=str(raw.get("role_id", "")),
            instance_id=str(raw.get("instance_id", "")),
            event_type=str(raw.get("event_type", "")),
            description=str(raw.get("description", "")),
            payload_json=payload,
            linked_evidence_ids=linked,
        )
