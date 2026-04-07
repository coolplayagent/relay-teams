from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    CREATED = "created"
    ASSIGNED = "assigned"
    RUNNING = "running"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
