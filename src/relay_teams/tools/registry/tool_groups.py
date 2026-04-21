# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class ConfigurableToolRegistry(Protocol):
    def list_configurable_names(self) -> tuple[str, ...]: ...


class ToolGroupDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    tools: tuple[str, ...] = ()


DEFAULT_TOOL_GROUPS: tuple[ToolGroupDefinition, ...] = (
    ToolGroupDefinition(
        group_id="workspace",
        name="Workspace",
        description="File, shell, and background task tools for working in the local workspace.",
        tools=(
            "read",
            "write",
            "write_tmp",
            "edit",
            "notebook_edit",
            "glob",
            "grep",
            "shell",
            "office_read_markdown",
            "create_monitor",
            "list_monitors",
            "stop_monitor",
            "list_background_tasks",
            "wait_background_task",
            "stop_background_task",
        ),
    ),
    ToolGroupDefinition(
        group_id="web",
        name="Web",
        description="Web fetch and search tools for external research.",
        tools=("webfetch", "websearch"),
    ),
    ToolGroupDefinition(
        group_id="computer",
        name="Computer",
        description="Desktop observation, input, and pointer tools.",
        tools=(
            "capture_screen",
            "launch_app",
            "wait_for_window",
            "list_windows",
            "focus_window",
            "type_text",
            "hotkey",
            "click_at",
            "double_click_at",
            "drag_between",
            "scroll_view",
        ),
    ),
    ToolGroupDefinition(
        group_id="orchestration",
        name="Orchestration",
        description="Coordinator-only orchestration tools for delegated task management.",
        tools=(
            "orch_create_tasks",
            "orch_update_task",
            "orch_dispatch_task",
            "orch_list_delegated_tasks",
            "orch_list_available_roles",
            "orch_create_temporary_role",
        ),
    ),
    ToolGroupDefinition(
        group_id="task",
        name="Task",
        description="Task-adjacent tools for spawning subagents or asking the user questions.",
        tools=(
            "spawn_subagent",
            "ask_question",
        ),
    ),
    ToolGroupDefinition(
        group_id="todo",
        name="Todo",
        description="Run-scoped todo read and write tools.",
        tools=("todo_read", "todo_write"),
    ),
)


def list_default_tool_groups(
    tool_registry: ConfigurableToolRegistry,
) -> tuple[ToolGroupDefinition, ...]:
    configurable_tools = frozenset(tool_registry.list_configurable_names())
    groups: list[ToolGroupDefinition] = []
    for group in DEFAULT_TOOL_GROUPS:
        visible_tools = tuple(
            tool_name for tool_name in group.tools if tool_name in configurable_tools
        )
        if not visible_tools:
            continue
        groups.append(group.model_copy(update={"tools": visible_tools}))
    return tuple(groups)
