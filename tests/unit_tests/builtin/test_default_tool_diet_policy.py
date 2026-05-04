# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.builtin.default_tool_diet_policy import DEFAULT_TOOL_DIET_POLICY
from relay_teams.roles.tool_diet_policy import ToolDietPolicy


def test_default_tool_diet_policy_is_tool_diet_policy() -> None:
    assert isinstance(DEFAULT_TOOL_DIET_POLICY, ToolDietPolicy)


def test_default_tool_diet_policy_defaults() -> None:
    assert DEFAULT_TOOL_DIET_POLICY.max_tools_per_role == 10
    assert DEFAULT_TOOL_DIET_POLICY.max_tools_warning_threshold == 7
