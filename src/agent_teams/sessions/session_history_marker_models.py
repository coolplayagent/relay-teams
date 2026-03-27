# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SessionHistoryMarkerType(str, Enum):
    CLEAR = "clear"
    COMPACTION = "compaction"


class SessionHistoryMarkerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    marker_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    marker_type: SessionHistoryMarkerType
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
