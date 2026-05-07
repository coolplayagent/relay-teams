# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from relay_teams.validation import require_non_empty_patch
from relay_teams.sessions.runs.enums import ExecutionMode
from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.sessions.session_models import SessionMode
from relay_teams.gateway.xiaoluban import XiaolubanAutomationBindingPreview
from relay_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr


class AutomationProjectStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class AutomationScheduleMode(str, Enum):
    CRON = "cron"
    INTERVAL = "interval"
    ONE_SHOT = "one_shot"


class AutomationIntervalUnit(str, Enum):
    MINUTES = "minutes"
    HOURS = "hours"
    DAYS = "days"


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


class AutomationCleanupStatus(str, Enum):
    PENDING = "pending"
    CLEANING = "cleaning"
    CLEANED = "cleaned"
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
    normal_root_role_id: OptionalIdentifierStr = None
    orchestration_preset_id: OptionalIdentifierStr = None
    execution_mode: ExecutionMode = ExecutionMode.AI
    yolo: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)


class AutomationFeishuBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["feishu"] = "feishu"
    trigger_id: RequiredIdentifierStr
    tenant_key: RequiredIdentifierStr
    chat_id: RequiredIdentifierStr
    session_id: OptionalIdentifierStr = None
    chat_type: str = Field(min_length=1)
    source_label: str = Field(min_length=1)


class AutomationFeishuBindingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["feishu"] = "feishu"
    trigger_id: RequiredIdentifierStr
    trigger_name: str = Field(min_length=1)
    tenant_key: RequiredIdentifierStr
    chat_id: RequiredIdentifierStr
    chat_type: str = Field(min_length=1)
    source_label: str = Field(min_length=1)
    session_id: RequiredIdentifierStr
    session_title: str = Field(min_length=1)
    updated_at: datetime


class AutomationXiaolubanBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="xiaoluban", pattern="^xiaoluban$")
    account_id: RequiredIdentifierStr
    display_name: str = Field(min_length=1)
    derived_uid: RequiredIdentifierStr
    source_label: str = Field(min_length=1)


class AutomationXiaolubanBindingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(default="xiaoluban", pattern="^xiaoluban$")
    account_id: RequiredIdentifierStr
    display_name: str = Field(min_length=1)
    derived_uid: RequiredIdentifierStr
    source_label: str = Field(min_length=1)
    updated_at: datetime


AutomationDeliveryBinding: TypeAlias = (
    AutomationFeishuBinding | AutomationXiaolubanBinding
)
AutomationDeliveryBindingCandidate: TypeAlias = (
    AutomationFeishuBindingCandidate | AutomationXiaolubanBindingCandidate
)


class AutomationProjectCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    display_name: str | None = None
    workspace_id: RequiredIdentifierStr
    prompt: str = Field(min_length=1)
    schedule_mode: AutomationScheduleMode
    cron_expression: str | None = None
    interval_every: int | None = Field(default=None, ge=1)
    interval_unit: AutomationIntervalUnit | None = None
    run_at: datetime | None = None
    timezone: str = Field(default="UTC", min_length=1)
    run_config: AutomationRunConfig = Field(default_factory=AutomationRunConfig)
    delivery_binding: AutomationDeliveryBinding | None = None
    delivery_events: tuple[AutomationDeliveryEvent, ...] = ()
    enabled: bool = True

    @model_validator(mode="after")
    def _validate_schedule_fields(self) -> AutomationProjectCreateInput:
        if self.schedule_mode == AutomationScheduleMode.CRON:
            if not self.cron_expression or not self.cron_expression.strip():
                raise ValueError("cron_expression is required for cron schedules")
            if len(self.cron_expression.split()) != 5:
                raise ValueError("cron_expression must use five fields")
            if self.run_at is not None:
                raise ValueError("run_at is not supported for cron schedules")
            if self.interval_every is not None or self.interval_unit is not None:
                raise ValueError("interval fields are not supported for cron schedules")
        if self.schedule_mode == AutomationScheduleMode.INTERVAL:
            if self.interval_every is None:
                raise ValueError("interval_every is required for interval schedules")
            if self.interval_unit is None:
                raise ValueError("interval_unit is required for interval schedules")
            if self.cron_expression is not None and self.cron_expression.strip():
                raise ValueError(
                    "cron_expression is not supported for interval schedules"
                )
            if self.run_at is not None:
                raise ValueError("run_at is not supported for interval schedules")
        if self.schedule_mode == AutomationScheduleMode.ONE_SHOT:
            if self.run_at is None:
                raise ValueError("run_at is required for one-shot schedules")
            if self.cron_expression is not None and self.cron_expression.strip():
                raise ValueError(
                    "cron_expression is not supported for one-shot schedules"
                )
            if self.interval_every is not None or self.interval_unit is not None:
                raise ValueError(
                    "interval fields are not supported for one-shot schedules"
                )
        return self


class AutomationProjectUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    display_name: str | None = None
    workspace_id: OptionalIdentifierStr = None
    prompt: str | None = Field(default=None, min_length=1)
    schedule_mode: AutomationScheduleMode | None = None
    cron_expression: str | None = None
    interval_every: int | None = Field(default=None, ge=1)
    interval_unit: AutomationIntervalUnit | None = None
    run_at: datetime | None = None
    timezone: str | None = Field(default=None, min_length=1)
    run_config: AutomationRunConfig | None = None
    delivery_binding: AutomationDeliveryBinding | None = None
    delivery_events: tuple[AutomationDeliveryEvent, ...] | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _validate_patch(self) -> AutomationProjectUpdateInput:
        require_non_empty_patch(self)
        if (
            self.schedule_mode == AutomationScheduleMode.CRON
            and self.run_at is not None
        ):
            raise ValueError("run_at is not supported for cron schedules")
        if self.schedule_mode == AutomationScheduleMode.CRON and (
            self.interval_every is not None or self.interval_unit is not None
        ):
            raise ValueError("interval fields are not supported for cron schedules")
        if (
            self.schedule_mode == AutomationScheduleMode.INTERVAL
            and self.cron_expression is not None
            and self.cron_expression.strip()
        ):
            raise ValueError("cron_expression is not supported for interval schedules")
        if (
            self.schedule_mode == AutomationScheduleMode.INTERVAL
            and self.run_at is not None
        ):
            raise ValueError("run_at is not supported for interval schedules")
        if (
            self.schedule_mode == AutomationScheduleMode.ONE_SHOT
            and self.cron_expression is not None
            and self.cron_expression.strip()
        ):
            raise ValueError("cron_expression is not supported for one-shot schedules")
        if (
            self.schedule_mode == AutomationScheduleMode.CRON
            and self.cron_expression is not None
            and self.cron_expression.strip()
            and len(self.cron_expression.split()) != 5
        ):
            raise ValueError("cron_expression must use five fields")
        if self.schedule_mode == AutomationScheduleMode.ONE_SHOT and (
            self.interval_every is not None or self.interval_unit is not None
        ):
            raise ValueError("interval fields are not supported for one-shot schedules")
        return self


class AutomationProjectRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    automation_project_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    status: AutomationProjectStatus
    workspace_id: RequiredIdentifierStr
    prompt: str = Field(min_length=1)
    schedule_mode: AutomationScheduleMode
    cron_expression: str | None = None
    interval_every: int | None = Field(default=None, ge=1)
    interval_unit: AutomationIntervalUnit | None = None
    run_at: datetime | None = None
    timezone: str = Field(min_length=1)
    run_config: AutomationRunConfig = Field(default_factory=AutomationRunConfig)
    delivery_binding: AutomationDeliveryBinding | None = None
    delivery_events: tuple[AutomationDeliveryEvent, ...] = ()
    trigger_id: RequiredIdentifierStr
    last_session_id: OptionalIdentifierStr = None
    last_run_started_at: datetime | None = None
    last_error: str | None = None
    next_run_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class AutomationRunDeliveryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    automation_delivery_id: RequiredIdentifierStr
    automation_project_id: RequiredIdentifierStr
    automation_project_name: str = Field(min_length=1)
    run_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    reason: str = Field(min_length=1)
    binding: AutomationDeliveryBinding
    delivery_events: tuple[AutomationDeliveryEvent, ...] = ()
    started_status: AutomationDeliveryStatus = AutomationDeliveryStatus.SKIPPED
    terminal_status: AutomationDeliveryStatus = AutomationDeliveryStatus.SKIPPED
    terminal_event: AutomationDeliveryEvent | None = None
    started_attempts: int = Field(default=0, ge=0)
    terminal_attempts: int = Field(default=0, ge=0)
    started_message: str | None = None
    terminal_message: str | None = None
    reply_to_message_id: OptionalIdentifierStr = None
    started_message_id: OptionalIdentifierStr = None
    terminal_message_id: OptionalIdentifierStr = None
    started_sent_at: datetime | None = None
    terminal_sent_at: datetime | None = None
    started_cleanup_status: AutomationCleanupStatus = AutomationCleanupStatus.SKIPPED
    started_cleanup_attempts: int = Field(default=0, ge=0)
    started_cleaned_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class AutomationBoundSessionQueueRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    automation_queue_id: RequiredIdentifierStr
    automation_project_id: RequiredIdentifierStr
    automation_project_name: str = Field(min_length=1)
    session_id: RequiredIdentifierStr
    reason: str = Field(min_length=1)
    binding: AutomationFeishuBinding
    delivery_events: tuple[AutomationDeliveryEvent, ...] = ()
    run_config: AutomationRunConfig = Field(default_factory=AutomationRunConfig)
    prompt: str = Field(min_length=1)
    queue_message: str = Field(min_length=1)
    run_id: OptionalIdentifierStr = None
    status: AutomationBoundSessionQueueStatus = AutomationBoundSessionQueueStatus.QUEUED
    start_attempts: int = Field(default=0, ge=0)
    next_attempt_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    resume_attempts: int = Field(default=0, ge=0)
    resume_next_attempt_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    queue_message_id: OptionalIdentifierStr = None
    queue_cleanup_status: AutomationCleanupStatus = AutomationCleanupStatus.SKIPPED
    queue_cleanup_attempts: int = Field(default=0, ge=0)
    queue_cleaned_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    completed_at: datetime | None = None


_DELIVERY_BINDING_ADAPTER = TypeAdapter(AutomationDeliveryBinding)
_DELIVERY_BINDING_CANDIDATE_ADAPTER = TypeAdapter(AutomationDeliveryBindingCandidate)


def validate_automation_delivery_binding(value: object) -> AutomationDeliveryBinding:
    return _DELIVERY_BINDING_ADAPTER.validate_python(value)


def validate_automation_delivery_binding_candidate(
    value: object,
) -> AutomationDeliveryBindingCandidate:
    return _DELIVERY_BINDING_CANDIDATE_ADAPTER.validate_python(value)


def xiaoluban_candidate_to_binding(
    candidate: XiaolubanAutomationBindingPreview,
) -> AutomationXiaolubanBinding:
    return AutomationXiaolubanBinding(
        account_id=candidate.account_id,
        display_name=candidate.display_name,
        derived_uid=candidate.derived_uid,
        source_label=candidate.source_label,
    )


class AutomationExecutionHandle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: RequiredIdentifierStr
    run_id: OptionalIdentifierStr = None
    queued: bool = False
    reused_bound_session: bool = False


__all__ = [
    "AutomationBoundSessionQueueRecord",
    "AutomationBoundSessionQueueStatus",
    "AutomationCleanupStatus",
    "AutomationDeliveryEvent",
    "AutomationDeliveryBinding",
    "AutomationDeliveryBindingCandidate",
    "AutomationDeliveryStatus",
    "AutomationExecutionHandle",
    "AutomationFeishuBinding",
    "AutomationFeishuBindingCandidate",
    "AutomationIntervalUnit",
    "AutomationProjectCreateInput",
    "AutomationProjectRecord",
    "AutomationProjectStatus",
    "AutomationProjectUpdateInput",
    "AutomationRunDeliveryRecord",
    "AutomationRunConfig",
    "AutomationScheduleMode",
    "AutomationXiaolubanBinding",
    "AutomationXiaolubanBindingCandidate",
    "validate_automation_delivery_binding",
    "validate_automation_delivery_binding_candidate",
    "xiaoluban_candidate_to_binding",
]
