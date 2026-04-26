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


class TaskSpecStrictness(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskTimeoutAction(str, Enum):
    FAIL = "fail"
    RETRY = "retry"
    HUMAN_GATE = "human_gate"


class VerificationLayer(str, Enum):
    STRUCTURE = "structure"
    BEHAVIOR = "behavior"
    SPEC = "spec"
