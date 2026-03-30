# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from agent_teams.validation import RequiredIdentifierStr


class ExternalSessionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: RequiredIdentifierStr
    trigger_id: RequiredIdentifierStr
    tenant_key: RequiredIdentifierStr
    external_chat_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    created_at: datetime
    updated_at: datetime
