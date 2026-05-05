# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class A2aTopic(str, Enum):
    """Pre-defined message topics. Agents may also use arbitrary string topics."""

    FILE_DISCOVERY = "file_discovery"
    DESIGN_FEEDBACK = "design_feedback"
    ARCHITECTURE_DECISION = "architecture_decision"
    ERROR_ESCALATION = "error_escalation"
    STATUS_UPDATE = "status_update"
    ARTIFACT_READY = "artifact_ready"


class A2aBusMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1)
    sender_role_id: str = Field(min_length=1)
    sender_instance_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    content: str = Field(min_length=1)
    payload_json: str = "{}"
    target_role_id: str | None = None
    source_task_id: str | None = None
    require_ack: bool = False
    published_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )


class A2aSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    receive_broadcast: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class A2aBusState(BaseModel):
    """Snapshot of the current bus state."""

    run_id: str
    message_count: int
    subscription_count: int
    active_topics: tuple[str, ...]
