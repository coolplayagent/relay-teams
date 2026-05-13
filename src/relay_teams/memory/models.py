# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from relay_teams.memory.memory_defaults import (
    MEDIUM_TERM_TTL,
    PERSISTENT_TTL,
    WORKING_TTL,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ConsolidationMode(str, Enum):
    STRUCTURAL = "structural"
    SEMANTIC = "semantic"


class MemoryTier(str, Enum):
    WORKING = "working"
    MEDIUM_TERM = "medium_term"
    PERSISTENT = "persistent"


class MemoryScope(str, Enum):
    WORKSPACE = "workspace"
    SESSION = "session"
    ROLE = "role"


class MemoryEntryKind(str, Enum):
    INSIGHT = "insight"
    CONSTRAINT = "constraint"
    DECISION = "decision"
    FAILURE_MODE = "failure_mode"
    PREFERENCE = "preference"
    FACT = "fact"
    SUMMARY = "summary"


class MemoryEntryStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


class MemorySourceKind(str, Enum):
    CONSOLIDATION = "consolidation"
    MANUAL = "manual"
    CONDENSATION = "condensation"
    TASK_RESULT = "task_result"


# ---------------------------------------------------------------------------
# Structured content
# ---------------------------------------------------------------------------


class MemoryContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1)
    context: str = ""
    outcome: str = ""


# ---------------------------------------------------------------------------
# Core entry model
# ---------------------------------------------------------------------------


def _validate_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    for tag in tags:
        if not tag:
            message = "Tag must be non-empty"
            raise ValueError(message)
        lower = tag.lower()
        if lower in seen:
            message = f"Duplicate tag (case-insensitive): {tag}"
            raise ValueError(message)
        seen.add(lower)
    return tags


class MemoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    tier: MemoryTier
    scope: MemoryScope
    workspace_id: str = Field(min_length=1)
    session_id: str | None = None
    run_id: str | None = None
    role_id: str | None = None
    kind: MemoryEntryKind
    status: MemoryEntryStatus = MemoryEntryStatus.ACTIVE
    content: MemoryContent
    tags: tuple[str, ...] = ()
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    source: MemorySourceKind
    source_ref: str = ""
    superseded_by_id: str | None = None
    parent_entry_id: str | None = None
    version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    last_accessed_at: datetime | None = None
    access_count: int = Field(default=0, ge=0)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=20)

    validate_tags = field_validator("tags")(_validate_tags)

    @model_validator(mode="after")
    def _validate_scope_tier_rules(self) -> MemoryEntry:
        if self.scope == MemoryScope.SESSION and self.session_id is None:
            message = "session_id is required when scope=SESSION"
            raise ValueError(message)
        if self.scope == MemoryScope.ROLE and self.role_id is None:
            message = "role_id is required when scope=ROLE"
            raise ValueError(message)
        if self.tier == MemoryTier.WORKING and self.run_id is None:
            message = "run_id is required when tier=WORKING"
            raise ValueError(message)
        return self


# ---------------------------------------------------------------------------
# Summary projection
# ---------------------------------------------------------------------------


class MemoryEntrySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tier: MemoryTier
    scope: MemoryScope
    workspace_id: str
    session_id: str | None
    role_id: str | None
    kind: MemoryEntryKind
    status: MemoryEntryStatus
    content_title: str
    content_body_preview: str
    tags: tuple[str, ...]
    confidence_score: float
    source: MemorySourceKind
    version: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None


def _entry_to_summary(entry: MemoryEntry) -> MemoryEntrySummary:
    body_preview = entry.content.body[:200]
    return MemoryEntrySummary(
        id=entry.id,
        tier=entry.tier,
        scope=entry.scope,
        workspace_id=entry.workspace_id,
        session_id=entry.session_id,
        role_id=entry.role_id,
        kind=entry.kind,
        status=entry.status,
        content_title=entry.content.title,
        content_body_preview=body_preview,
        tags=entry.tags,
        confidence_score=entry.confidence_score,
        source=entry.source,
        version=entry.version,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        expires_at=entry.expires_at,
    )


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateMemoryEntryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier: MemoryTier
    scope: MemoryScope
    workspace_id: str = Field(min_length=1)
    session_id: str | None = None
    run_id: str | None = None
    role_id: str | None = None
    kind: MemoryEntryKind
    content: MemoryContent
    tags: tuple[str, ...] = ()
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    source: MemorySourceKind = MemorySourceKind.MANUAL
    source_ref: str = ""
    expires_at: datetime | None = None
    metadata: dict[str, str] = Field(default_factory=dict, max_length=20)

    validate_tags = field_validator("tags")(_validate_tags)

    @model_validator(mode="after")
    def _validate_scope_tier_rules(self) -> CreateMemoryEntryRequest:
        if self.scope == MemoryScope.SESSION and self.session_id is None:
            message = "session_id is required when scope=SESSION"
            raise ValueError(message)
        if self.scope == MemoryScope.ROLE and self.role_id is None:
            message = "role_id is required when scope=ROLE"
            raise ValueError(message)
        if self.tier == MemoryTier.WORKING and self.run_id is None:
            message = "run_id is required when tier=WORKING"
            raise ValueError(message)
        return self


# Sentinel for distinguishing ``null`` from absent in updates.
_UNSET: Literal["_unset_sentinel"] = "_unset_sentinel"


class UpdateMemoryEntryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: MemoryContent | None = None
    tags: tuple[str, ...] | None = None
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    status: MemoryEntryStatus | None = None
    expires_at: datetime | None | Literal["_unset_sentinel"] = _UNSET
    metadata: dict[str, str] | None = None

    validate_tags = field_validator("tags")(_validate_tags)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> UpdateMemoryEntryRequest:
        provided = [
            self.content is not None,
            self.tags is not None,
            self.confidence_score is not None,
            self.status is not None,
            self.expires_at is not _UNSET,
            self.metadata is not None,
        ]
        if not any(provided):
            message = "At least one field must be provided for update"
            raise ValueError(message)
        return self


def default_ttl_for_tier(tier: MemoryTier) -> datetime | None:
    """Return an expiry datetime for the given tier relative to now, or None."""
    now = datetime.now(tz=timezone.utc)
    if tier == MemoryTier.WORKING:
        return now + WORKING_TTL
    if tier == MemoryTier.MEDIUM_TERM:
        return now + MEDIUM_TERM_TTL
    return PERSISTENT_TTL


# ---------------------------------------------------------------------------
# Query / Result models
# ---------------------------------------------------------------------------


class MemoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = Field(default=None, min_length=1)
    tier: MemoryTier | None = None
    scope: MemoryScope | None = None
    session_id: str | None = None
    role_id: str | None = None
    kind: MemoryEntryKind | None = None
    status: MemoryEntryStatus | None = None
    tags: tuple[str, ...] = ()
    text_query: str = ""
    created_after: datetime | None = None
    created_before: datetime | None = None
    min_confidence: float = 0.0
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class MemoryQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[MemoryEntrySummary, ...]
    total_count: int = Field(ge=0)
    offset: int
    limit: int


# ---------------------------------------------------------------------------
# Consolidation models
# ---------------------------------------------------------------------------


class MemoryConsolidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    session_id: str | None = None
    role_id: str | None = None
    source_run_id: str | None = None
    target_tier: MemoryTier
    target_scope: MemoryScope
    consolidation_mode: ConsolidationMode = ConsolidationMode.STRUCTURAL
    max_extracted_entries: int = Field(default=10, ge=1, le=50)
    extraction_kinds: tuple[MemoryEntryKind, ...] = ()
    prompt_override: str = ""
    filter_tags: tuple[str, ...] = ()
    filter_kind: MemoryEntryKind | None = None

    validate_filter_tags = field_validator("filter_tags")(_validate_tags)

    @model_validator(mode="after")
    def _validate_target_tier(self) -> MemoryConsolidationRequest:
        if self.target_tier == MemoryTier.WORKING:
            message = "Consolidation target tier must be MEDIUM_TERM or PERSISTENT"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _validate_semantic_mode(self) -> MemoryConsolidationRequest:
        if (
            self.consolidation_mode == ConsolidationMode.SEMANTIC
            and self.source_run_id is None
        ):
            raise ValueError(
                "source_run_id is required for SEMANTIC consolidation mode"
            )
        return self


class MemoryConsolidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_entry_count: int
    consolidated_entry_count: int
    superseded_entry_ids: tuple[str, ...]
    new_entry_ids: tuple[str, ...]
    extraction_tokens_used: int = 0
    extraction_duration_ms: int = 0


# ---------------------------------------------------------------------------
# Search models
# ---------------------------------------------------------------------------


class MemorySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    text_query: str = Field(min_length=1)
    tier: MemoryTier | None = None
    scope: MemoryScope | None = None
    session_id: str | None = None
    role_id: str | None = None
    kind: MemoryEntryKind | None = None
    status: MemoryEntryStatus | None = MemoryEntryStatus.ACTIVE
    tags: tuple[str, ...] = ()
    min_confidence: float = 0.3
    limit: int = Field(default=10, ge=1, le=100)

    validate_tags = field_validator("tags")(_validate_tags)


class GlobalMemorySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text_query: str = Field(min_length=1)
    workspace_id: str | None = Field(default=None, min_length=1)
    tier: MemoryTier | None = None
    scope: MemoryScope | None = None
    session_id: str | None = None
    role_id: str | None = None
    kind: MemoryEntryKind | None = None
    status: MemoryEntryStatus | None = MemoryEntryStatus.ACTIVE
    tags: tuple[str, ...] = ()
    min_confidence: float = 0.3
    limit: int = Field(default=10, ge=1, le=100)

    validate_tags = field_validator("tags")(_validate_tags)


class MemorySearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry: MemoryEntrySummary
    score: float
    rank: int = Field(ge=1)
    snippet: str


class MemorySearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[MemorySearchHit, ...]
    total_count: int


# ---------------------------------------------------------------------------
# Evolution models
# ---------------------------------------------------------------------------


class MemoryEvolutionTarget(str, Enum):
    SKILL = "skill"
    SOP_SKILL = "sop_skill"


class MemoryEvolutionStatus(str, Enum):
    DRAFT = "draft"
    APPLYING = "applying"
    APPLIED = "applied"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


_CLAW_HUB_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _normalize_claw_hub_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not _CLAW_HUB_IDENTIFIER_PATTERN.fullmatch(normalized):
        message = (
            f"{field_name} must start with an alphanumeric character and only use "
            "letters, digits, dot, underscore, or hyphen"
        )
        raise ValueError(message)
    return normalized


class CreateMemoryEvolutionDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = ""
    source_memory_ids: tuple[str, ...] = Field(min_length=1, max_length=50)
    target: MemoryEvolutionTarget = MemoryEvolutionTarget.SOP_SKILL
    skill_id: str = Field(min_length=1, max_length=128)
    runtime_name: str = Field(min_length=1, max_length=128)
    description: str = ""
    objective: str = ""

    @field_validator("source_memory_ids")
    @classmethod
    def _validate_source_memory_ids(
        cls, source_memory_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for memory_id in source_memory_ids:
            normalized = memory_id.strip()
            if not normalized:
                message = "source_memory_ids must contain non-empty values"
                raise ValueError(message)
            if normalized in seen:
                message = f"Duplicate source memory id: {normalized}"
                raise ValueError(message)
            seen.add(normalized)
            cleaned.append(normalized)
        return tuple(cleaned)

    @field_validator("skill_id")
    @classmethod
    def _validate_skill_id(cls, skill_id: str) -> str:
        return _normalize_claw_hub_identifier(skill_id, field_name="skill_id")

    @field_validator("runtime_name")
    @classmethod
    def _validate_runtime_name(cls, runtime_name: str) -> str:
        return _normalize_claw_hub_identifier(
            runtime_name,
            field_name="runtime_name",
        )


class ApplyMemoryEvolutionDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str | None = Field(default=None, min_length=1, max_length=128)
    runtime_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    instructions: str | None = None

    @field_validator("skill_id")
    @classmethod
    def _validate_optional_skill_id(cls, skill_id: str | None) -> str | None:
        if skill_id is None:
            return None
        return _normalize_claw_hub_identifier(skill_id, field_name="skill_id")

    @field_validator("runtime_name")
    @classmethod
    def _validate_optional_runtime_name(
        cls,
        runtime_name: str | None,
    ) -> str | None:
        if runtime_name is None:
            return None
        return _normalize_claw_hub_identifier(
            runtime_name,
            field_name="runtime_name",
        )


class RejectMemoryEvolutionDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = ""


class MemoryEvolutionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    source_memory_ids: tuple[str, ...] = Field(min_length=1)
    target: MemoryEvolutionTarget
    status: MemoryEvolutionStatus = MemoryEvolutionStatus.DRAFT
    skill_id: str = Field(min_length=1)
    runtime_name: str = Field(min_length=1)
    description: str = ""
    instructions: str = Field(min_length=1)
    applied_skill_ref: str | None = None
    rejection_reason: str = ""
    created_at: datetime
    updated_at: datetime
    applied_at: datetime | None = None
    rejected_at: datetime | None = None


class MemoryEvolutionDraftQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    target: MemoryEvolutionTarget | None = None
    status: MemoryEvolutionStatus | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class MemoryEvolutionDraftQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[MemoryEvolutionDraft, ...]
    total_count: int = Field(ge=0)
    offset: int
    limit: int
