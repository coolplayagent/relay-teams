# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr


class FailureMode(str, Enum):
    CONTEXT_ROT = "context_rot"
    TOOL_SPRAWL = "tool_sprawl"
    SPEC_DRIFT = "spec_drift"
    PERMISSION_FRICTION = "permission_friction"
    VERIFICATION_MISS = "verification_miss"


class FailureModeClassification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    classification_id: RequiredIdentifierStr = Field(min_length=1)
    run_id: RequiredIdentifierStr = Field(min_length=1)
    session_id: RequiredIdentifierStr = Field(min_length=1)
    workspace_id: RequiredIdentifierStr = Field(min_length=1)
    role_id: OptionalIdentifierStr = None
    primary_mode: FailureMode
    secondary_modes: tuple[FailureMode, ...] = ()
    confidence_score: float = Field(ge=0.0, le=1.0)
    evidence_summary: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    classified_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    classifier_version: str = Field(min_length=1)
