from __future__ import annotations

COORDINATOR_REQUIRED_TOOLS = frozenset(
    (
        "orch_create_tasks",
        "orch_update_task",
        "orch_dispatch_task",
    )
)
COORDINATOR_IDENTIFIERS = frozenset(("coordinator",))
OFFICE_READ_MARKDOWN_TOOL = "office_read_markdown"
TODO_WRITE_TOOL = "todo_write"
TODO_READ_TOOL = "todo_read"
_TODO_TOOLS = frozenset((TODO_WRITE_TOOL, TODO_READ_TOOL))


def apply_default_role_tools(
    *,
    role_id: str,
    role_name: str | None = None,
    mode: str | None = None,
    tools: tuple[str, ...],
) -> tuple[str, ...]:
    normalized_tools = tuple(tool_name for tool_name in tools if tool_name.strip())
    if _is_coordinator_role(role_id=role_id, role_name=role_name, tools=tools):
        return tuple(
            tool_name
            for tool_name in normalized_tools
            if tool_name != OFFICE_READ_MARKDOWN_TOOL and tool_name not in _TODO_TOOLS
        )
    result = [
        tool_name
        for tool_name in normalized_tools
        if _mode_allows_todo_tools(mode=mode) or tool_name not in _TODO_TOOLS
    ]
    if OFFICE_READ_MARKDOWN_TOOL not in result:
        result.append(OFFICE_READ_MARKDOWN_TOOL)
    if _mode_allows_todo_tools(mode=mode):
        if TODO_WRITE_TOOL not in result:
            result.append(TODO_WRITE_TOOL)
        if TODO_READ_TOOL not in result:
            result.append(TODO_READ_TOOL)
    return tuple(result)


def _is_coordinator_role(
    *,
    role_id: str,
    role_name: str | None,
    tools: tuple[str, ...],
) -> bool:
    normalized_role_id = role_id.strip().casefold()
    normalized_role_name = str(role_name or "").strip().casefold()
    return (
        normalized_role_id in COORDINATOR_IDENTIFIERS
        or normalized_role_name in COORDINATOR_IDENTIFIERS
        or COORDINATOR_REQUIRED_TOOLS.issubset(set(tools))
    )


def _mode_allows_todo_tools(*, mode: str | None) -> bool:
    normalized_mode = str(mode or "").strip().casefold()
    return normalized_mode in {"", "primary", "all"}
