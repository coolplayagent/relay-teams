from __future__ import annotations

COORDINATOR_REQUIRED_TOOLS = frozenset(
    (
        "create_tasks",
        "update_task",
        "dispatch_task",
    )
)
COORDINATOR_IDENTIFIERS = frozenset(("coordinator",))
OFFICE_READ_MARKDOWN_TOOL = "office_read_markdown"


def apply_default_role_tools(
    *,
    role_id: str,
    role_name: str | None = None,
    tools: tuple[str, ...],
) -> tuple[str, ...]:
    if _is_coordinator_role(role_id=role_id, role_name=role_name, tools=tools):
        return tuple(
            tool_name for tool_name in tools if tool_name != OFFICE_READ_MARKDOWN_TOOL
        )
    if OFFICE_READ_MARKDOWN_TOOL in tools:
        return tools
    return (*tools, OFFICE_READ_MARKDOWN_TOOL)


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
