# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_teams.sessions.runs.enums import ExecutionMode
from agent_teams.sessions.runs.run_models import RunThinkingConfig
from agent_teams.sessions.session_models import SessionMode


class AutomationProjectStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class AutomationScheduleMode(str, Enum):
    CRON = "cron"
    ONE_SHOT = "one_shot"


class AutomationDeliveryEvent(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class AutomationDeliveryStatus(str, Enum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"


class AutomationBoundSessionQueueStatus(str, Enum):
    QUEUED = "queued"
    STARTING = "starting"
    WAITING_RESULT = "waiting_result"
    COMPLETED = "completed"
    FAILED = "failed"


class AutomationRunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_mode: SessionMode = SessionMode.NORMAL
    orchestration_preset_id: str | None = None
    execution_mode: ExecutionMode = ExecutionMode.AI
    yolo: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)


class AutomationFeishuBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["feishu"] = "feishu"
    trigger_id: str = Field(min_length=1)
    tenant_key: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)
    chat_type: str = Field(min_length=1)
    source_label: str = Field(min_length=1)


class AutomationFeishuBindingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["feishu"] = "feishu"
    trigger_id: str = Field(min_length=1)
    trigger_name: str = Field(min_length=1)
    tenant_key: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)
    chat_type: str = Field(min_length=1)
    source_label: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    session_title: str = Field(min_length=1)
    updated_at: datetime


class AutomationProjectCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    display_name: str | None = None
    workspace_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    schedule_mode: AutomationScheduleMode
    cron_expression: str | None = None
    run_at: datetime | None = None
    timezone: str = Field(default="UTC", min_length=1)
    run_config: AutomationRunConfig = Field(default_factory=AutomationRunConfig)
    delivery_binding: AutomationFeishuBinding | None = None
    delivery_events: tuple[AutomationDeliveryEvent, ...] = ()
    enabled: bool = True

    @model_validator(mode="after")
    def _validate_schedule_fields(self) -> AutomationProjectCreateInput:
        if self.schedule_mode == AutomationScheduleMode.CRON:
            if not self.cron_expression or not self.cron_expression.strip():
                raise ValueError("cron_expression is required for cron schedules")
            if self.run_at is not None:
                raise ValueError("run_at is not supported for cron schedules")
        if self.schedule_mode == AutomationScheduleMode.ONE_SHOT:
            if self.run_at is None:
                raise ValueError("run_at is required for one-shot schedules")
            if self.cron_expression is not None and self.cron_expression.strip():
                raise ValueError(
                    "cron_expression is not supported for one-shot schedules"
                )
        return self


class AutomationProjectUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    display_name: str | None = None
    workspace_id: str | None = Field(default=None, min_length=1)
    prompt: str | None = Field(default=None, min_length=1)
    schedule_mode: AutomationScheduleMode | None = None
    cron_expression: str | None = None
    run_at: datetime | None = None
    timezone: str | None = Field(default=None, min_length=1)
    run_config: AutomationRunConfig | None = None
    delivery_binding: AutomationFeishuBinding | None = None
    delivery_events: tuple[AutomationDeliveryEvent, ...] | None = None
    enabled: bool | None = None


class AutomationProjectRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    automation_project_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    status: AutomationProjectStatus
    workspace_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    schedule_mode: AutomationScheduleMode
    cron_expression: str | None = None
    run_at: datetime | None = None
    timezone: str = Field(min_length=1)
    run_config: AutomationRunConfig = Field(default_factory=AutomationRunConfig)
    delivery_binding: AutomationFeishuBinding | None = None
    delivery_events: tuple[AutomationDeliveryEvent, ...] = ()
    trigger_id: str = Field(min_length=1)
    last_session_id: str | None = None
    last_run_started_at: datetime | None = None
    last_error: str | None = None
    next_run_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class AutomationRunDeliveryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    automation_delivery_id: str = Field(min_length=1)
    automation_project_id: str = Field(min_length=1)
    automation_project_name: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    binding: AutomationFeishuBinding
    delivery_events: tuple[AutomationDeliveryEvent, ...] = ()
    started_status: AutomationDeliveryStatus = AutomationDeliveryStatus.SKIPPED
    terminal_status: AutomationDeliveryStatus = AutomationDeliveryStatus.SKIPPED
    terminal_event: AutomationDeliveryEvent | None = None
    started_attempts: int = Field(default=0, ge=0)
    terminal_attempts: int = Field(default=0, ge=0)
    started_message: str | None = None
    terminal_message: str | None = None
    started_sent_at: datetime | None = None
    terminal_sent_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class AutomationBoundSessionQueueRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    automation_queue_id: str = Field(min_length=1)
    automation_project_id: str = Field(min_length=1)
    automation_project_name: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    binding: AutomationFeishuBinding
    delivery_events: tuple[AutomationDeliveryEvent, ...] = ()
    run_config: AutomationRunConfig = Field(default_factory=AutomationRunConfig)
    prompt: str = Field(min_length=1)
    queue_message: str = Field(min_length=1)
    run_id: str | None = None
    status: AutomationBoundSessionQueueStatus = AutomationBoundSessionQueueStatus.QUEUED
    start_attempts: int = Field(default=0, ge=0)
    next_attempt_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    completed_at: datetime | None = None


class AutomationExecutionHandle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1)
    run_id: str | None = None
    queued: bool = False


__all__ = [
    "AutomationBoundSessionQueueRecord",
    "AutomationBoundSessionQueueStatus",
    "AutomationDeliveryEvent",
    "AutomationDeliveryStatus",
    "AutomationExecutionHandle",
    "AutomationFeishuBinding",
    "AutomationFeishuBindingCandidate",
    "AutomationProjectCreateInput",
    "AutomationProjectRecord",
    "AutomationProjectStatus",
    "AutomationProjectUpdateInput",
    "AutomationRunDeliveryRecord",
    "AutomationRunConfig",
    "AutomationScheduleMode",
]
