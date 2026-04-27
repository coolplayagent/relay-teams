from __future__ import annotations

import fnmatch
from pydantic import JsonValue

from relay_teams.hooks.hook_event_models import (
    HookEventInput,
    PermissionDeniedInput,
    PermissionRequestInput,
    PostToolUseFailureInput,
    PostToolUseInput,
    PreToolUseInput,
)
from relay_teams.hooks.hook_loader import CLAUDE_TOOL_MATCHER_ALIASES

_TOOL_CONDITION_INPUT_FIELDS: dict[str, tuple[str, ...]] = {
    "edit": ("file_path", "path"),
    "glob": ("pattern", "path"),
    "grep": ("pattern", "path"),
    "notebook_edit": ("file_path", "path"),
    "office_read_markdown": ("file_path", "path"),
    "read": ("file_path", "path"),
    "shell": ("command",),
    "webfetch": ("url",),
    "websearch": ("query",),
    "write": ("file_path", "path"),
    "write_tmp": ("file_path", "path"),
}


def hook_handler_condition_matches(
    *,
    if_rule: str | None,
    event_input: HookEventInput,
    tool_name: str,
) -> bool:
    condition = str(if_rule or "").strip()
    if not condition:
        return True
    parsed = _parse_tool_condition(condition)
    if parsed is None:
        return False
    condition_tool_name, pattern = parsed
    if condition_tool_name != tool_name:
        return False
    if not pattern:
        return True
    candidate = _tool_condition_candidate(
        tool_name=tool_name,
        tool_input=_tool_input_for_event(event_input),
    )
    if not candidate:
        return False
    return fnmatch.fnmatchcase(candidate, pattern)


def _parse_tool_condition(condition: str) -> tuple[str, str] | None:
    open_index = condition.find("(")
    if open_index == -1:
        return _normalize_tool_condition_name(condition), ""
    if not condition.endswith(")") or open_index == 0:
        return None
    tool_name = _normalize_tool_condition_name(condition[:open_index].strip())
    pattern = condition[open_index + 1 : -1].strip()
    if not tool_name:
        return None
    return tool_name, pattern


def _normalize_tool_condition_name(value: str) -> str:
    return CLAUDE_TOOL_MATCHER_ALIASES.get(value, value)


def _tool_input_for_event(event_input: HookEventInput) -> dict[str, JsonValue]:
    if isinstance(event_input, PreToolUseInput):
        return event_input.tool_input
    if isinstance(event_input, PermissionRequestInput):
        return event_input.tool_input
    if isinstance(event_input, PermissionDeniedInput):
        return event_input.tool_input
    if isinstance(event_input, PostToolUseInput):
        return event_input.tool_input
    if isinstance(event_input, PostToolUseFailureInput):
        return event_input.tool_input
    return {}


def _tool_condition_candidate(
    *,
    tool_name: str,
    tool_input: dict[str, JsonValue],
) -> str:
    for field_name in _TOOL_CONDITION_INPUT_FIELDS.get(tool_name, ()):
        value = tool_input.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
