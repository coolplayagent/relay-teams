from __future__ import annotations

from relay_teams.reminders import render_system_reminder


def test_render_system_reminder_wraps_content() -> None:
    assert render_system_reminder(" Pay attention. ") == (
        "<system-reminder>\nPay attention.\n</system-reminder>"
    )


def test_render_system_reminder_ignores_empty_content() -> None:
    assert render_system_reminder("  ") == ""
