# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.tools.registry import build_default_registry
from relay_teams.tools.registry.tool_groups import list_default_tool_groups


class _SubsetToolRegistry:
    def __init__(self, names: tuple[str, ...]) -> None:
        self._names = names

    def list_configurable_names(self) -> tuple[str, ...]:
        return self._names


def test_default_tool_groups_include_expected_buckets() -> None:
    registry = build_default_registry()

    groups = list_default_tool_groups(registry)

    assert [group.group_id for group in groups] == [
        "workspace",
        "web",
        "computer",
        "orchestration",
        "task",
        "todo",
    ]
    workspace_group = next(group for group in groups if group.group_id == "workspace")
    assert "shell" in workspace_group.tools
    assert "office_read_markdown" in workspace_group.tools
    computer_group = next(group for group in groups if group.group_id == "computer")
    assert computer_group.name == "Computer Use"
    assert "capture_screen" in computer_group.tools
    orchestration_group = next(
        group for group in groups if group.group_id == "orchestration"
    )
    assert "orch_dispatch_task" in orchestration_group.tools
    assert "orch_create_tasks" in orchestration_group.tools
    task_group = next(group for group in groups if group.group_id == "task")
    assert task_group.tools == ("spawn_subagent", "ask_question")
    todo_group = next(group for group in groups if group.group_id == "todo")
    assert todo_group.tools == ("todo_read", "todo_write")


def test_default_tool_groups_filter_hidden_and_missing_tools() -> None:
    groups = list_default_tool_groups(
        _SubsetToolRegistry(
            (
                "shell",
                "orch_dispatch_task",
            )
        )
    )

    assert [group.group_id for group in groups] == ["workspace", "orchestration"]
    assert groups[0].tools == ("shell",)
    assert groups[1].tools == ("orch_dispatch_task",)
