# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ReflectionJobType(StrEnum):
    DAILY_REFLECTION = "daily_reflection"
    LONG_TERM_CONSOLIDATION = "long_term_consolidation"


class ReflectionJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MemoryOwnerScope(StrEnum):
    SESSION_ROLE = "session_role"


class DailyMemoryKind(StrEnum):
    RAW = "raw"
    DIGEST = "digest"


class ReflectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    model_profile: str = Field(default="reflection", min_length=1)
    poll_interval_seconds: float = Field(default=2.0, gt=0.0, le=60.0)
    max_retry_attempts: int = Field(default=3, ge=1, le=20)
    max_transcript_messages: int = Field(default=40, ge=1, le=200)
    max_injected_memory_chars: int = Field(default=8000, ge=256, le=64000)
    max_long_term_injection_chars: int = Field(default=5000, ge=128, le=64000)
    max_daily_digest_injection_chars: int = Field(default=3000, ge=128, le=64000)
    daily_retention_days: int = Field(default=14, ge=1, le=365)


def default_reflection_config() -> ReflectionConfig:
    return ReflectionConfig()


class ReflectionJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_type: ReflectionJobType
    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    memory_owner_scope: MemoryOwnerScope = MemoryOwnerScope.SESSION_ROLE
    memory_owner_id: str = Field(min_length=1)
    trigger_date: str = Field(min_length=10, max_length=10)


class ReflectionJobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str = Field(min_length=1)
    job_type: ReflectionJobType
    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    memory_owner_scope: MemoryOwnerScope
    memory_owner_id: str = Field(min_length=1)
    trigger_date: str = Field(min_length=10, max_length=10)
    status: ReflectionJobStatus
    attempt_count: int = Field(ge=0)
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class DailyRawMemoryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_date: str = Field(min_length=10, max_length=10)
    session_facts: tuple[str, ...] = ()
    observations: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    failures_and_recoveries: tuple[str, ...] = ()
    open_threads: tuple[str, ...] = ()
    candidate_long_term_learnings: tuple[str, ...] = ()


class DailyDigestDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_date: str = Field(min_length=10, max_length=10)
    summary_items: tuple[str, ...] = ()


class LongTermMemoryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_identity: tuple[str, ...] = ()
    stable_user_project_preferences: tuple[str, ...] = ()
    proven_strategies: tuple[str, ...] = ()
    reusable_constraints_and_boundaries: tuple[str, ...] = ()
    important_ongoing_tendencies: tuple[str, ...] = ()


class DailyReflectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_document: DailyRawMemoryDocument
    digest_document: DailyDigestDocument


class MemoryFileView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path
    exists: bool
    content: str
