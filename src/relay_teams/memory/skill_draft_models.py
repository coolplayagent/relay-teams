# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MemorySkillDraftScopeKind(str, Enum):
    WORKSPACE = "workspace"
    CROSS_WORKSPACE = "cross_workspace"


class MemorySkillDraftKind(str, Enum):
    SKILL = "skill"
    SOP_SKILL = "sop_skill"


class MemorySkillDraftGenerationKind(str, Enum):
    AUTO = "auto"
    SKILL = "skill"
    SOP_SKILL = "sop_skill"


class MemorySkillDraftStatus(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    APPLYING = "applying"
    APPLIED = "applied"
    REJECTED = "rejected"


class MemorySkillDraftValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class MemorySkillDraftFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    content: str = ""
    encoding: Literal["utf-8", "base64"] = "utf-8"


class MemorySkillDraftValidationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: MemorySkillDraftValidationSeverity
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    path: str = ""


class MemorySkillDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    status: MemorySkillDraftStatus = MemorySkillDraftStatus.DRAFT
    scope_kind: MemorySkillDraftScopeKind
    workspace_id: str | None = None
    workspace_ids: tuple[str, ...] = ()
    source_memory_ids: tuple[str, ...] = ()
    draft_kind: MemorySkillDraftKind
    runtime_name: str = Field(min_length=1)
    description: str = ""
    instructions: str = ""
    files: tuple[MemorySkillDraftFile, ...] = ()
    validation_messages: tuple[MemorySkillDraftValidationMessage, ...] = ()
    generation_error: str = ""
    applied_skill_id: str | None = None
    applied_ref: str | None = None
    created_at: datetime
    updated_at: datetime
    validated_at: datetime | None = None
    applied_at: datetime | None = None

    @field_validator("workspace_ids", "source_memory_ids")
    @classmethod
    def _dedupe_non_empty(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            normalized = value.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return tuple(result)

    @model_validator(mode="after")
    def _validate_scope(self) -> MemorySkillDraft:
        if self.scope_kind == MemorySkillDraftScopeKind.WORKSPACE:
            if self.workspace_id is None or not self.workspace_id.strip():
                raise ValueError("workspace_id is required for workspace skill drafts")
        return self


class MemorySkillDraftSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: MemorySkillDraftStatus
    scope_kind: MemorySkillDraftScopeKind
    workspace_id: str | None
    workspace_ids: tuple[str, ...]
    source_memory_count: int = Field(ge=0)
    draft_kind: MemorySkillDraftKind
    runtime_name: str
    description: str
    validation_error_count: int = Field(ge=0)
    validation_warning_count: int = Field(ge=0)
    applied_ref: str | None
    created_at: datetime
    updated_at: datetime


def draft_to_summary(draft: MemorySkillDraft) -> MemorySkillDraftSummary:
    error_count = sum(
        1
        for message in draft.validation_messages
        if message.severity == MemorySkillDraftValidationSeverity.ERROR
    )
    warning_count = sum(
        1
        for message in draft.validation_messages
        if message.severity == MemorySkillDraftValidationSeverity.WARNING
    )
    return MemorySkillDraftSummary(
        id=draft.id,
        status=draft.status,
        scope_kind=draft.scope_kind,
        workspace_id=draft.workspace_id,
        workspace_ids=draft.workspace_ids,
        source_memory_count=len(draft.source_memory_ids),
        draft_kind=draft.draft_kind,
        runtime_name=draft.runtime_name,
        description=draft.description,
        validation_error_count=error_count,
        validation_warning_count=warning_count,
        applied_ref=draft.applied_ref,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


class GenerateMemorySkillDraftsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_kind: MemorySkillDraftScopeKind = MemorySkillDraftScopeKind.WORKSPACE
    workspace_id: str | None = None
    workspace_ids: tuple[str, ...] = ()
    source_memory_ids: tuple[str, ...] = ()
    draft_kind: MemorySkillDraftGenerationKind = MemorySkillDraftGenerationKind.AUTO
    text_query: str = ""
    max_drafts: int = Field(default=3, ge=1, le=8)
    limit: int = Field(default=80, ge=1, le=200)
    min_confidence: float = Field(default=0.3, ge=0.0, le=1.0)

    @field_validator("workspace_ids", "source_memory_ids")
    @classmethod
    def _dedupe_non_empty(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            normalized = value.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return tuple(result)


class MemorySkillDraftGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[MemorySkillDraftSummary, ...]
    source_memory_count: int = Field(ge=0)
    error_message: str = ""


class MemorySkillDraftQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_kind: MemorySkillDraftScopeKind | None = None
    workspace_id: str | None = None
    status: MemorySkillDraftStatus | None = None
    draft_kind: MemorySkillDraftKind | None = None
    text_query: str = ""
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class MemorySkillDraftQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[MemorySkillDraftSummary, ...]
    total_count: int = Field(ge=0)
    offset: int
    limit: int


class UpdateMemorySkillDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_name: str | None = None
    description: str | None = None
    instructions: str | None = None
    files: tuple[MemorySkillDraftFile, ...] | None = None
    status: MemorySkillDraftStatus | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> UpdateMemorySkillDraftRequest:
        if not any(
            (
                self.runtime_name is not None,
                self.description is not None,
                self.instructions is not None,
                self.files is not None,
                self.status is not None,
            )
        ):
            raise ValueError("At least one field must be provided for update")
        return self


class MemorySkillDraftApplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft: MemorySkillDraft
    skill_id: str
    ref: str
