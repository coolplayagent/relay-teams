from __future__ import annotations

from relay_teams.reminders.models import ToolEffect

READ_ONLY_TOOLS = frozenset(
    {
        "glob",
        "grep",
        "list_available_roles",
        "list_background_tasks",
        "list_delegated_tasks",
        "list_monitors",
        "list_run_tasks",
        "office_read_markdown",
        "read",
        "ripgrep",
        "todo_read",
        "wait_background_task",
        "webfetch",
        "websearch",
    }
)

MUTATING_TOOLS = frozenset(
    {
        "ask_question",
        "create_monitor",
        "create_tasks",
        "create_temporary_role",
        "dispatch_task",
        "edit",
        "im_send",
        "notebook_edit",
        "shell",
        "spawn_subagent",
        "stop_background_task",
        "stop_monitor",
        "todo_write",
        "update_task",
        "write",
        "write_tmp",
    }
)


def classify_tool_effect(tool_name: str) -> ToolEffect:
    normalized = tool_name.strip()
    if normalized in READ_ONLY_TOOLS:
        return ToolEffect.READ_ONLY
    if normalized in MUTATING_TOOLS:
        return ToolEffect.MUTATING
    return ToolEffect.NEUTRAL
