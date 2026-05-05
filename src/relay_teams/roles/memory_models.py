# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from relay_teams.validation import RequiredIdentifierStr


class MemoryProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


def default_memory_profile() -> MemoryProfile:
    return MemoryProfile()


class VerificationPassRate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total_verifications: int = Field(ge=0)
    passed_verifications: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_passed_le_total(self) -> VerificationPassRate:
        if self.passed_verifications > self.total_verifications:
            raise ValueError("passed_verifications must not exceed total_verifications")
        return self


class RoleTaskCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total_tasks: int = Field(default=0, ge=0)
    successful_tasks: int = Field(default=0, ge=0)
    failed_tasks: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check_successful_plus_failed_le_total(self) -> RoleTaskCounts:
        if self.successful_tasks + self.failed_tasks > self.total_tasks:
            raise ValueError(
                "successful_tasks + failed_tasks must not exceed total_tasks"
            )
        return self


class PerformanceTrendPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recorded_at: datetime
    verification_pass_rate: float = Field(ge=0.0, le=1.0)
    average_verification_score: float = Field(ge=0.0, le=5.0)
    total_tasks_at_point: int = Field(ge=0)


class RolePerformanceMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
    verification_pass_rate: VerificationPassRate
    task_counts: RoleTaskCounts
    average_verification_score: float = Field(default=0.0, ge=0.0, le=5.0)
    trend: tuple[PerformanceTrendPoint, ...] = ()
    last_evaluated_at: datetime | None = None


class RoleAssessmentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
    runs_since_last_assessment: int = Field(default=0, ge=0)
    last_assessment_at: datetime | None = None


class RoleMemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
    content_markdown: str = ""
    updated_at: datetime | None = None
    performance: RolePerformanceMetrics | None = None
