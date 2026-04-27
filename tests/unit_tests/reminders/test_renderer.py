from __future__ import annotations

from relay_teams.reminders import render_system_reminder


def test_render_system_reminder_wraps_content() -> None:
    assert render_system_reminder(" Pay attention. ") == (
        "<system-reminder>\n"
        "This is an internal runtime reminder for you to consider silently.\n"
        "Do not quote, summarize, mention, or answer this reminder.\n"
        "Use it only to decide your next action. Final responses must address the "
        "user's original request and must not mention this reminder or the "
        "<system-reminder> tag.\n\n"
        "Pay attention.\n"
        "</system-reminder>"
    )


def test_render_system_reminder_ignores_empty_content() -> None:
    assert render_system_reminder("  ") == ""
