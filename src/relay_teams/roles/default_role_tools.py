from __future__ import annotations

OFFICE_READ_MARKDOWN_TOOL = "office_read_markdown"
_COORDINATOR_ROLE_IDS = frozenset({"coordinator"})


def apply_default_role_tools(
    *,
    role_id: str,
    tools: tuple[str, ...],
) -> tuple[str, ...]:
    normalized_role_id = role_id.strip().casefold()
    if normalized_role_id in _COORDINATOR_ROLE_IDS:
        return tools
    if OFFICE_READ_MARKDOWN_TOOL in tools:
        return tools
    return (*tools, OFFICE_READ_MARKDOWN_TOOL)
