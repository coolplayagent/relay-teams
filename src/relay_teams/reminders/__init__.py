from __future__ import annotations

from typing import Any

from relay_teams.reminders.delivery import SystemReminderDeliveryMode
from relay_teams.reminders.models import (
    CompletionAttemptObservation,
    ContextPressureObservation,
    IncompleteTodoItem,
    ReminderDecision,
    ReminderKind,
    ToolEffect,
    ToolResultObservation,
)
from relay_teams.reminders.policy import ReminderPolicyConfig, SystemReminderPolicy
from relay_teams.reminders.renderer import render_system_reminder
from relay_teams.reminders.state import ReminderStateRepository, ReminderRunState
from relay_teams.reminders.text import is_rendered_system_reminder_text
from relay_teams.reminders.tool_effects import classify_tool_effect


def __getattr__(name: str) -> Any:
    if name == "SystemReminderService":
        from relay_teams.reminders.service import SystemReminderService

        globals()["SystemReminderService"] = SystemReminderService
        return SystemReminderService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CompletionAttemptObservation",
    "ContextPressureObservation",
    "IncompleteTodoItem",
    "ReminderDecision",
    "ReminderKind",
    "ReminderPolicyConfig",
    "ReminderRunState",
    "ReminderStateRepository",
    "SystemReminderDeliveryMode",
    "SystemReminderPolicy",
    "SystemReminderService",
    "ToolEffect",
    "ToolResultObservation",
    "classify_tool_effect",
    "is_rendered_system_reminder_text",
    "render_system_reminder",
]
