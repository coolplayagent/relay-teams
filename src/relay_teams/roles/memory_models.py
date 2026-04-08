# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from relay_teams.validation import RequiredIdentifierStr


class MemoryProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


def default_memory_profile() -> MemoryProfile:
    return MemoryProfile()


class RoleMemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
    content_markdown: str = ""
    updated_at: datetime | None = None
