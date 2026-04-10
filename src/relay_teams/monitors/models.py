# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr


class MonitorSourceKind(str, Enum):
    BACKGROUND_TASK = "background_task"
    GITHUB = "github"


class MonitorActionType(str, Enum):
    WAKE_INSTANCE = "wake_instance"
    WAKE_COORDINATOR = "wake_coordinator"
    START_FOLLOWUP_RUN = "start_followup_run"
    EMIT_NOTIFICATION = "emit_notification"


class MonitorSubscriptionStatus(str, Enum):
    ACTIVE = "active"
    STOPPED = "stopped"


class MonitorRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_names: tuple[str, ...] = ()
    text_patterns_any: tuple[str, ...] = ()
    attribute_equals: dict[str, str] = Field(default_factory=dict)
    attribute_in: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    cooldown_seconds: int = Field(default=0, ge=0)
    max_triggers: int | None = Field(default=None, ge=1)
    auto_stop_on_first_match: bool = False
    case_sensitive: bool = False


class MonitorAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: MonitorActionType = MonitorActionType.WAKE_INSTANCE


class MonitorSubscriptionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monitor_id: RequiredIdentifierStr
    run_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    source_kind: MonitorSourceKind
    source_key: str = Field(min_length=1)
    created_by_instance_id: OptionalIdentifierStr = None
    created_by_role_id: OptionalIdentifierStr = None
    tool_call_id: OptionalIdentifierStr = None
    status: MonitorSubscriptionStatus = MonitorSubscriptionStatus.ACTIVE
    rule: MonitorRule = Field(default_factory=MonitorRule)
    action: MonitorAction = Field(default_factory=MonitorAction)
    trigger_count: int = Field(default=0, ge=0)
    last_triggered_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    stopped_at: datetime | None = None


class MonitorEventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: MonitorSourceKind
    source_key: str = Field(min_length=1)
    event_name: str = Field(min_length=1)
    run_id: RequiredIdentifierStr | None = None
    session_id: RequiredIdentifierStr | None = None
    body_text: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    dedupe_key: str | None = None
    raw_payload_json: str = "{}"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class MonitorTriggerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monitor_trigger_id: RequiredIdentifierStr
    monitor_id: RequiredIdentifierStr
    run_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    source_kind: MonitorSourceKind
    source_key: str = Field(min_length=1)
    event_name: str = Field(min_length=1)
    dedupe_key: str | None = None
    body_text: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    raw_payload_json: str = "{}"
    action_type: MonitorActionType
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
