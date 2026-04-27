from __future__ import annotations

SYSTEM_REMINDER_INTERNAL_MARKER = (
    "This is an internal runtime reminder for you to consider silently."
)


def is_rendered_system_reminder_text(content: str) -> bool:
    text = content.strip()
    return (
        text.startswith("<system-reminder>")
        and text.endswith("</system-reminder>")
        and SYSTEM_REMINDER_INTERNAL_MARKER in text
    )
