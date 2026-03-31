# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr


class BackgroundTerminalStatus(str, Enum):
    RUNNING = "running"
    BLOCKED = "blocked"
    STOPPED = "stopped"
    FAILED = "failed"
    COMPLETED = "completed"


class BackgroundTerminalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terminal_id: RequiredIdentifierStr
    run_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    instance_id: OptionalIdentifierStr = None
    role_id: OptionalIdentifierStr = None
    tool_call_id: OptionalIdentifierStr = None
    command: str = Field(min_length=1)
    cwd: str = Field(min_length=1)
    execution_mode: Literal["background"] = "background"
    status: BackgroundTerminalStatus = BackgroundTerminalStatus.RUNNING
    tty: bool = False
    timeout_ms: int | None = Field(default=None, ge=1)
    exit_code: int | None = None
    recent_output: tuple[str, ...] = ()
    stdout_tail: tuple[str, ...] = ()
    stderr_tail: tuple[str, ...] = ()
    log_path: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    completed_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.status in {
            BackgroundTerminalStatus.RUNNING,
            BackgroundTerminalStatus.BLOCKED,
        }
