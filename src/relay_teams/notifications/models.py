# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.gateway.feishu.models import FeishuMessageFormat


class NotificationType(str, Enum):
    TOOL_APPROVAL_REQUESTED = "tool_approval_requested"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_STOPPED = "run_stopped"
    MONITOR_TRIGGERED = "monitor_triggered"


class NotificationChannel(str, Enum):
    BROWSER = "browser"
    TOAST = "toast"
    FEISHU = "feishu"


class NotificationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    channels: tuple[NotificationChannel, ...] = (
        NotificationChannel.BROWSER,
        NotificationChannel.TOAST,
    )
    feishu_format: FeishuMessageFormat = FeishuMessageFormat.TEXT


class NotificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_approval_requested: NotificationRule = Field(
        default_factory=lambda: NotificationRule(
            enabled=True,
            channels=(NotificationChannel.BROWSER, NotificationChannel.TOAST),
        )
    )
    run_completed: NotificationRule = Field(
        default_factory=lambda: NotificationRule(
            enabled=True,
            channels=(NotificationChannel.TOAST, NotificationChannel.FEISHU),
        )
    )
    run_failed: NotificationRule = Field(
        default_factory=lambda: NotificationRule(
            enabled=True,
            channels=(
                NotificationChannel.BROWSER,
                NotificationChannel.TOAST,
                NotificationChannel.FEISHU,
            ),
        )
    )
    run_stopped: NotificationRule = Field(
        default_factory=lambda: NotificationRule(
            enabled=False,
            channels=(NotificationChannel.TOAST,),
        )
    )
    monitor_triggered: NotificationRule = Field(
        default_factory=lambda: NotificationRule(
            enabled=True,
            channels=(NotificationChannel.BROWSER, NotificationChannel.TOAST),
        )
    )

    def rule_for(self, notification_type: NotificationType) -> NotificationRule:
        if notification_type == NotificationType.TOOL_APPROVAL_REQUESTED:
            return self.tool_approval_requested
        if notification_type == NotificationType.RUN_COMPLETED:
            return self.run_completed
        if notification_type == NotificationType.RUN_FAILED:
            return self.run_failed
        if notification_type == NotificationType.RUN_STOPPED:
            return self.run_stopped
        return self.monitor_triggered


class NotificationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    task_id: str | None = None
    instance_id: str | None = None
    role_id: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None


class NotificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notification_type: NotificationType
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    channels: tuple[NotificationChannel, ...] = ()
    feishu_format: FeishuMessageFormat = FeishuMessageFormat.TEXT
    dedupe_key: str = Field(min_length=1)
    context: NotificationContext


def default_notification_config() -> NotificationConfig:
    return NotificationConfig()
