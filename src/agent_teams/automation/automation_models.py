# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

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


class AutomationRunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_mode: SessionMode = SessionMode.NORMAL
    orchestration_preset_id: str | None = None
    execution_mode: ExecutionMode = ExecutionMode.AI
    yolo: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)


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
    trigger_id: str = Field(min_length=1)
    last_session_id: str | None = None
    last_run_started_at: datetime | None = None
    last_error: str | None = None
    next_run_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


__all__ = [
    "AutomationProjectCreateInput",
    "AutomationProjectRecord",
    "AutomationProjectStatus",
    "AutomationProjectUpdateInput",
    "AutomationRunConfig",
    "AutomationScheduleMode",
]
