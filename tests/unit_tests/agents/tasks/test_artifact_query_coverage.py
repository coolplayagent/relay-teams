# -*- coding: utf-8 -*-
"""Coverage gap tests for artifact_query_service.py."""

from __future__ import annotations

import json as _json


from relay_teams.agents.tasks.enums import TaskArtifactPhase
from relay_teams.agents.tasks.models import TaskArtifactEntry
from relay_teams.agents.tasks.artifact_query_service import (
    _extract_spec_summary,
    _extract_verification_report_summary,
)


def _make_entry(
    event_type: str = "spec_checkpoint",
    phase: TaskArtifactPhase = TaskArtifactPhase.SPEC,
    payload: dict[str, object] | None = None,
    payload_json_override: str | None = None,
) -> TaskArtifactEntry:
    pj = (
        payload_json_override
        if payload_json_override is not None
        else (_json.dumps(payload) if payload is not None else "")
    )
    return TaskArtifactEntry(
        entry_id="entry_cov",
        event_type=event_type,
        phase=phase,
        payload_json=pj,
        timestamp="2026-01-01T00:00:00Z",
    )


class TestExtractSpecSummary:
    """Cover lines 19, 23, 31-32."""

    def test_empty_entries(self) -> None:
        assert _extract_spec_summary([]) == ""

    def test_entry_with_empty_payload(self) -> None:
        entry = _make_entry(payload_json_override="")
        assert _extract_spec_summary([entry]) == ""

    def test_entry_with_objective(self) -> None:
        entry = _make_entry(payload={"objective": "Test obj"})
        assert _extract_spec_summary([entry]) == "Test obj"

    def test_entry_with_title_fallback(self) -> None:
        entry = _make_entry(payload={"title": "Test title"})
        assert _extract_spec_summary([entry]) == "Test title"

    def test_entry_with_summary_fallback(self) -> None:
        entry = _make_entry(payload={"summary": "Test summary"})
        assert _extract_spec_summary([entry]) == "Test summary"

    def test_entry_with_invalid_json(self) -> None:
        entry = _make_entry(payload_json_override="{invalid")
        assert _extract_spec_summary([entry]) == ""


class TestExtractVerificationReportSummary:
    """Cover lines 50-52, 106-107."""

    def test_empty_entries(self) -> None:
        assert _extract_verification_report_summary([]) == ""

    def test_no_verification_report_entries(self) -> None:
        entry = _make_entry(event_type="other_event")
        assert _extract_verification_report_summary([entry]) == ""

    def test_verification_report_with_summary(self) -> None:
        entry = _make_entry(
            event_type="verification_report",
            phase=TaskArtifactPhase.VERIFICATION,
            payload={"summary": "All checks passed"},
        )
        assert _extract_verification_report_summary([entry]) == "All checks passed"

    def test_verification_report_with_message_fallback(self) -> None:
        entry = _make_entry(
            event_type="verification_report",
            phase=TaskArtifactPhase.VERIFICATION,
            payload={"message": "Some message"},
        )
        assert _extract_verification_report_summary([entry]) == "Some message"

    def test_verification_report_with_details_fallback(self) -> None:
        entry = _make_entry(
            event_type="verification_report",
            phase=TaskArtifactPhase.VERIFICATION,
            payload={"details": "Detail output"},
        )
        assert _extract_verification_report_summary([entry]) == "Detail output"

    def test_verification_report_with_empty_payload(self) -> None:
        entry = _make_entry(
            event_type="verification_report",
            phase=TaskArtifactPhase.VERIFICATION,
            payload_json_override="",
        )
        assert _extract_verification_report_summary([entry]) == ""

    def test_verification_report_with_invalid_json(self) -> None:
        entry = _make_entry(
            event_type="verification_report",
            phase=TaskArtifactPhase.VERIFICATION,
            payload_json_override="not-json",
        )
        assert _extract_verification_report_summary([entry]) == ""
