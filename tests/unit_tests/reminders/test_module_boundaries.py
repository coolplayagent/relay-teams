from __future__ import annotations

from pathlib import Path

from relay_teams.reminders import SystemReminderDeliveryMode
from relay_teams.reminders import is_rendered_system_reminder_text


def test_system_reminder_contract_lives_under_reminders_package() -> None:
    source_root = Path(__file__).parents[3] / "src" / "relay_teams"

    assert not (source_root / "system_reminder_delivery.py").exists()
    assert not (source_root / "system_reminder_text.py").exists()


def test_reminders_package_re_exports_text_and_delivery_contracts() -> None:
    assert SystemReminderDeliveryMode.GUIDANCE.value == "guidance"
    assert is_rendered_system_reminder_text(
        "<system-reminder>\n"
        "This is an internal runtime reminder for you to consider silently.\n"
        "</system-reminder>"
    )
