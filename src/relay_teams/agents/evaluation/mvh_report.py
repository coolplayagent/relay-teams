# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.agents.evaluation.failure_modes import (
    FailureMode,
    FailureModeClassification,
)
from relay_teams.validation import RequiredIdentifierStr


class HarnessPriorityItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int = Field(ge=1)
    harness_layer: str = Field(min_length=1)
    failure_mode: FailureMode
    prevalence_pct: float = Field(ge=0.0, le=100.0)
    recommended_action: str = Field(min_length=1)


class MVHRecommendationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: RequiredIdentifierStr = Field(min_length=1)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    sample_size: int = Field(ge=1)
    total_runs_available: int = Field(ge=0)
    failure_distribution: dict[FailureMode, int]
    failure_mode_percentages: dict[FailureMode, float]
    multi_mode_rate: float = Field(ge=0.0, le=1.0)
    harness_layer_priorities: tuple[HarnessPriorityItem, ...] = ()
    summary: str = Field(min_length=1)
    classifications: tuple[FailureModeClassification, ...] = ()
