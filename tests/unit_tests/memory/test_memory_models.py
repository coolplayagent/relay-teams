# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest
from pydantic import ValidationError

from relay_teams.memory.models import (
    CreateMemoryEntryRequest,
    MemoryContent,
    MemoryEntry,
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryScope,
    MemorySourceKind,
    MemoryTier,
    UpdateMemoryEntryRequest,
    _UNSET,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(**overrides: object) -> MemoryEntry:
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc)
    base: dict[str, object] = {
        "id": "mem-test123",
        "tier": MemoryTier.PERSISTENT,
        "scope": MemoryScope.WORKSPACE,
        "workspace_id": "ws-1",
        "kind": MemoryEntryKind.FACT,
        "content": MemoryContent(title="Test", body="Body text"),
        "source": MemorySourceKind.MANUAL,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return MemoryEntry(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AC-1: Enums exist with correct values
# ---------------------------------------------------------------------------


class TestEnums:
    def test_memory_tier_values(self) -> None:
        assert {e.value for e in MemoryTier} == {
            "working",
            "medium_term",
            "persistent",
        }

    def test_memory_scope_values(self) -> None:
        assert {e.value for e in MemoryScope} == {"workspace", "session", "role"}

    def test_memory_entry_kind_values(self) -> None:
        assert {e.value for e in MemoryEntryKind} == {
            "insight",
            "constraint",
            "decision",
            "failure_mode",
            "preference",
            "fact",
            "summary",
        }

    def test_memory_entry_status_values(self) -> None:
        assert {e.value for e in MemoryEntryStatus} == {
            "active",
            "superseded",
            "expired",
        }

    def test_memory_source_kind_values(self) -> None:
        assert {e.value for e in MemorySourceKind} == {
            "consolidation",
            "manual",
            "reflection",
            "condensation",
            "task_result",
        }


# ---------------------------------------------------------------------------
# AC-2: Scope/tier validation rules
# ---------------------------------------------------------------------------


class TestScopeTierValidation:
    def test_session_scope_requires_session_id(self) -> None:
        with pytest.raises(ValidationError, match="session_id is required"):
            _make_entry(
                scope=MemoryScope.SESSION,
                session_id=None,
            )

    def test_role_scope_requires_role_id(self) -> None:
        with pytest.raises(ValidationError, match="role_id is required"):
            _make_entry(
                scope=MemoryScope.ROLE,
                role_id=None,
            )

    def test_working_tier_requires_run_id(self) -> None:
        with pytest.raises(ValidationError, match="run_id is required"):
            _make_entry(
                tier=MemoryTier.WORKING,
                run_id=None,
            )

    def test_valid_session_entry(self) -> None:
        entry = _make_entry(
            scope=MemoryScope.SESSION,
            session_id="sess-1",
        )
        assert entry.session_id == "sess-1"

    def test_valid_working_entry(self) -> None:
        entry = _make_entry(
            tier=MemoryTier.WORKING,
            run_id="run-1",
        )
        assert entry.run_id == "run-1"

    def test_valid_role_entry(self) -> None:
        entry = _make_entry(
            scope=MemoryScope.ROLE,
            role_id="role-1",
        )
        assert entry.role_id == "role-1"


# ---------------------------------------------------------------------------
# AC-3: MemoryContent min_length validation
# ---------------------------------------------------------------------------


class TestMemoryContentValidation:
    def test_empty_title_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryContent(title="", body="body")

    def test_empty_body_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryContent(title="title", body="")

    def test_valid_content(self) -> None:
        c = MemoryContent(title="T", body="B")
        assert c.title == "T"
        assert c.context == ""
        assert c.outcome == ""


# ---------------------------------------------------------------------------
# AC-4: No typing.Any; ConfigDict(extra="forbid")
# ---------------------------------------------------------------------------


class TestModelForbidExtra:
    def test_entry_forbids_extra(self) -> None:
        with pytest.raises(ValidationError):
            _make_entry(unknown_field="x")  # type: ignore[arg-type]

    def test_content_forbids_extra(self) -> None:
        with pytest.raises(ValidationError):
            MemoryContent(title="T", body="B", extra="x")  # type: ignore[call-arg]

    def test_create_request_forbids_extra(self) -> None:
        with pytest.raises(ValidationError):
            CreateMemoryEntryRequest(
                tier=MemoryTier.WORKING,
                scope=MemoryScope.SESSION,
                workspace_id="ws-1",
                session_id="s-1",
                run_id="r-1",
                kind=MemoryEntryKind.FACT,
                content=MemoryContent(title="T", body="B"),
                extra_field="x",  # type: ignore[call-arg]
            )


# ---------------------------------------------------------------------------
# Tags validation
# ---------------------------------------------------------------------------


class TestTagsValidation:
    def test_empty_tag_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Tag must be non-empty"):
            _make_entry(tags=("",))

    def test_duplicate_tag_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate tag"):
            _make_entry(tags=("a", "A"))

    def test_valid_tags(self) -> None:
        entry = _make_entry(tags=("python", "pydantic"))
        assert entry.tags == ("python", "pydantic")


# ---------------------------------------------------------------------------
# Update request validation
# ---------------------------------------------------------------------------


class TestUpdateRequestValidation:
    def test_at_least_one_field_required(self) -> None:
        with pytest.raises(ValidationError, match="At least one field"):
            UpdateMemoryEntryRequest()

    def test_valid_update_with_content(self) -> None:
        req = UpdateMemoryEntryRequest(
            content=MemoryContent(title="New", body="New body")
        )
        assert req.content is not None
        assert req.content.title == "New"

    def test_valid_update_with_confidence(self) -> None:
        req = UpdateMemoryEntryRequest(confidence_score=0.5)
        assert req.confidence_score == 0.5

    def test_expires_at_sentinel(self) -> None:
        req = UpdateMemoryEntryRequest(expires_at=None)
        assert req.expires_at is None

    def test_expires_at_unset(self) -> None:
        req = UpdateMemoryEntryRequest(confidence_score=0.9)
        assert req.expires_at is _UNSET


# ---------------------------------------------------------------------------
# Create request validation
# ---------------------------------------------------------------------------


class TestCreateRequestValidation:
    def test_create_working_requires_run_id(self) -> None:
        with pytest.raises(ValidationError, match="run_id is required"):
            CreateMemoryEntryRequest(
                tier=MemoryTier.WORKING,
                scope=MemoryScope.WORKSPACE,
                workspace_id="ws-1",
                kind=MemoryEntryKind.FACT,
                content=MemoryContent(title="T", body="B"),
            )

    def test_create_session_requires_session_id(self) -> None:
        with pytest.raises(ValidationError, match="session_id is required"):
            CreateMemoryEntryRequest(
                tier=MemoryTier.WORKING,
                scope=MemoryScope.SESSION,
                workspace_id="ws-1",
                run_id="r-1",
                kind=MemoryEntryKind.FACT,
                content=MemoryContent(title="T", body="B"),
            )

    def test_create_valid_working(self) -> None:
        req = CreateMemoryEntryRequest(
            tier=MemoryTier.WORKING,
            scope=MemoryScope.SESSION,
            workspace_id="ws-1",
            session_id="s-1",
            run_id="r-1",
            kind=MemoryEntryKind.INSIGHT,
            content=MemoryContent(title="Discovery", body="Found pattern X"),
        )
        assert req.source == MemorySourceKind.MANUAL
        assert req.confidence_score == 1.0
