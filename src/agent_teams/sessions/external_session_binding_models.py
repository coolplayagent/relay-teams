# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ExternalSessionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: str = Field(min_length=1)
    trigger_id: str = Field(min_length=1)
    tenant_key: str = Field(min_length=1)
    external_chat_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
