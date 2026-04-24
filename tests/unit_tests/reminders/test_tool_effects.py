from __future__ import annotations

from relay_teams.reminders import ToolEffect, classify_tool_effect


def test_classify_tool_effect_treats_registered_orchestration_lists_as_read_only() -> (
    None
):
    assert classify_tool_effect("orch_list_available_roles") == ToolEffect.READ_ONLY
    assert classify_tool_effect("orch_list_delegated_tasks") == ToolEffect.READ_ONLY
