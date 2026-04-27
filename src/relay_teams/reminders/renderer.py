from __future__ import annotations

from relay_teams.system_reminder_text import (
    SYSTEM_REMINDER_INTERNAL_MARKER,
)


def render_system_reminder(content: str) -> str:
    text = content.strip()
    if not text:
        return ""
    return (
        "<system-reminder>\n"
        f"{SYSTEM_REMINDER_INTERNAL_MARKER}\n"
        "Do not quote, summarize, mention, or answer this reminder.\n"
        "Use it only to decide your next action. Final responses must address the "
        "user's original request and must not mention this reminder or the "
        "<system-reminder> tag.\n\n"
        f"{text}\n"
        "</system-reminder>"
    )
