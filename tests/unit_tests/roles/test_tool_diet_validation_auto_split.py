# -*- coding: utf-8 -*-
from __future__ import annotations


from relay_teams.roles.tool_diet_validation import suggest_auto_split
from relay_teams.roles.tool_diet_policy import ToolDietPolicy


class TestSuggestAutoSplit:
    def test_no_split_needed(self) -> None:
        policy = ToolDietPolicy(max_tools_per_role=10)
        result = suggest_auto_split(
            policy=policy,
            tool_names=("read", "write", "shell"),
        )
        assert result == ()

    def test_split_by_category(self) -> None:
        policy = ToolDietPolicy(max_tools_per_role=5)
        tools = (
            "read",
            "glob",
            "grep",  # read tools
            "edit",
            "write",
            "shell",  # write tools
            "create_monitor",
            "stop_monitor",  # orchestration tools
            "custom_tool",  # other
        )
        result = suggest_auto_split(
            policy=policy,
            tool_names=tools,
        )
        assert len(result) >= 2
        all_tools: list[str] = []
        for group in result:
            all_tools.extend(group)
        assert set(all_tools) == set(tools)

    def test_empty_tools(self) -> None:
        policy = ToolDietPolicy(max_tools_per_role=10)
        result = suggest_auto_split(policy=policy, tool_names=())
        assert result == ()

    def test_split_with_default_midpoint(self) -> None:
        policy = ToolDietPolicy(max_tools_per_role=2)
        tools = ("a", "b", "c", "d")
        result = suggest_auto_split(policy=policy, tool_names=tools)
        assert len(result) >= 2
