# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from relay_teams.agents.tasks.enums import TaskTimeoutAction, WakeupReason, WakeupStatus


class AgentWakeupEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    wakeup_id: str
    task_id: str
    trace_id: str
    session_id: str
    coalesce_key: str
    timeout_action: TaskTimeoutAction
    timeout_seconds: float
    attempt: int
    max_attempts: int
    status: WakeupStatus
    enqueued_at: datetime
    claimed_at: datetime | None = None
    completed_at: datetime | None = None
    wake_reason: WakeupReason = WakeupReason.TIMEOUT_RETRY
    target_role: str = ""
    target_instance: str = ""
    source_event_type: str = ""
    source_trigger_id: str = ""
