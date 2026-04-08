from __future__ import annotations

from enum import Enum


class InstanceStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
