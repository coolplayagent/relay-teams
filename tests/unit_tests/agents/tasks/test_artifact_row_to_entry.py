# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from relay_teams.agents.tasks.artifact_repository import TaskArtifactRepository
from relay_teams.agents.tasks.models import TaskArtifactPhase


class TestRowToEntry:
    """Cover _row_to_entry branches for linked_evidence_ids and payload_json."""

    @staticmethod
    def _base_raw(**overrides: object) -> dict[str, object]:
        raw: dict[str, object] = {
            "entry_id": "e1",
            "phase": "execution",
            "timestamp": "2024-01-01T00:00:00Z",
            "role_id": "r1",
            "instance_id": "i1",
            "event_type": "test",
            "description": "test entry",
            "payload_json": "{}",
            "linked_evidence_ids": "[]",
        }
        raw.update(overrides)
        return raw

    def test_linked_evidence_ids_valid_json_string(self) -> None:
        raw = self._base_raw(linked_evidence_ids='["ev1", "ev2"]')
        entry = TaskArtifactRepository._row_to_entry(raw)
        assert entry.linked_evidence_ids == ("ev1", "ev2")

    def test_linked_evidence_ids_invalid_json_string(self) -> None:
        raw = self._base_raw(linked_evidence_ids="not json")
        entry = TaskArtifactRepository._row_to_entry(raw)
        assert entry.linked_evidence_ids == ()

    def test_linked_evidence_ids_list(self) -> None:
        raw = self._base_raw(linked_evidence_ids=["a", "b"])
        entry = TaskArtifactRepository._row_to_entry(raw)
        assert entry.linked_evidence_ids == ("a", "b")

    def test_linked_evidence_ids_tuple(self) -> None:
        raw = self._base_raw(linked_evidence_ids=("x",))
        entry = TaskArtifactRepository._row_to_entry(raw)
        assert entry.linked_evidence_ids == ("x",)

    def test_linked_evidence_ids_other_type(self) -> None:
        raw = self._base_raw(linked_evidence_ids=42)
        entry = TaskArtifactRepository._row_to_entry(raw)
        assert entry.linked_evidence_ids == ()

    def test_payload_json_string(self) -> None:
        raw = self._base_raw(payload_json='{"key": "val"}')
        entry = TaskArtifactRepository._row_to_entry(raw)
        assert json.loads(entry.payload_json) == {"key": "val"}

    def test_payload_json_dict(self) -> None:
        raw = self._base_raw(payload_json={"k": "v"})
        entry = TaskArtifactRepository._row_to_entry(raw)
        assert json.loads(entry.payload_json) == {"k": "v"}

    def test_payload_json_other_type(self) -> None:
        raw = self._base_raw(payload_json=None)
        entry = TaskArtifactRepository._row_to_entry(raw)
        assert entry.payload_json == "{}"

    def test_default_phase(self) -> None:
        raw = self._base_raw()
        raw.pop("phase", None)
        entry = TaskArtifactRepository._row_to_entry(raw)
        assert entry.phase == TaskArtifactPhase.EXECUTION
