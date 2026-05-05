from __future__ import annotations

from enum import Enum


class SystemReminderDeliveryMode(str, Enum):
    GUIDANCE = "guidance"
    COMPLETION_GUARD = "completion_guard"
