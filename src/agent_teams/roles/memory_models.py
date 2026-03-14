# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class MemoryKind(str, Enum):
    RAW = "raw"
    DIGEST = "digest"


class MemoryProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    daily_enabled: bool = True


def default_memory_profile() -> MemoryProfile:
    return MemoryProfile()


class RoleMemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    content_markdown: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class RoleDailyMemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    memory_date: str = Field(min_length=1)
    kind: MemoryKind
    content_markdown: str = ""
    source_session_id: str | None = None
    source_task_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
