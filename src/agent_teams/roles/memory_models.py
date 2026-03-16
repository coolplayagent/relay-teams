# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class MemoryProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


def default_memory_profile() -> MemoryProfile:
    return MemoryProfile()


class RoleMemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    content_markdown: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
