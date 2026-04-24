from __future__ import annotations


def render_system_reminder(content: str) -> str:
    text = content.strip()
    if not text:
        return ""
    return f"<system-reminder>\n{text}\n</system-reminder>"
