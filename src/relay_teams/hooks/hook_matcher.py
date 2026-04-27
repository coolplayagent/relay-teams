from __future__ import annotations

import fnmatch
from typing import Optional

from relay_teams.hooks.hook_event_models import (
    HookEventInput,
    InstructionsLoadedInput,
    NotificationInput,
    PostCompactInput,
    PreCompactInput,
    SessionEndInput,
    SessionStartInput,
    StopFailureInput,
    SubagentStartInput,
    SubagentStopInput,
)
from relay_teams.hooks.hook_models import HookEventName, HookMatcherGroup

MATCHER_UNSUPPORTED_EVENTS = {
    HookEventName.USER_PROMPT_SUBMIT,
    HookEventName.STOP,
    HookEventName.TASK_CREATED,
    HookEventName.TASK_COMPLETED,
}


def get_matcher_target(
    event_input: HookEventInput,
    *,
    tool_name: str = "",
) -> Optional[str]:
    if event_input.event_name in {
        HookEventName.PRE_TOOL_USE,
        HookEventName.PERMISSION_REQUEST,
        HookEventName.PERMISSION_DENIED,
        HookEventName.POST_TOOL_USE,
        HookEventName.POST_TOOL_USE_FAILURE,
    }:
        return tool_name or None
    if event_input.event_name == HookEventName.SESSION_START:
        typed = SessionStartInput.model_validate(event_input.model_dump())
        return typed.start_reason or event_input.event_name.value
    if event_input.event_name == HookEventName.SESSION_END:
        typed = SessionEndInput.model_validate(event_input.model_dump())
        return typed.end_reason or typed.completion_reason or None
    if event_input.event_name == HookEventName.STOP_FAILURE:
        typed = StopFailureInput.model_validate(event_input.model_dump())
        return typed.error_code or None
    if event_input.event_name == HookEventName.SUBAGENT_START:
        typed = SubagentStartInput.model_validate(event_input.model_dump())
        return typed.subagent_type or typed.subagent_role_id or None
    if event_input.event_name == HookEventName.SUBAGENT_STOP:
        typed = SubagentStopInput.model_validate(event_input.model_dump())
        return typed.subagent_type or typed.subagent_role_id or None
    if event_input.event_name == HookEventName.NOTIFICATION:
        typed = NotificationInput.model_validate(event_input.model_dump())
        return typed.notification_type or event_input.event_name.value
    if event_input.event_name == HookEventName.INSTRUCTIONS_LOADED:
        typed = InstructionsLoadedInput.model_validate(event_input.model_dump())
        return typed.load_reason or event_input.role_id or None
    if event_input.event_name == HookEventName.PRE_COMPACT:
        typed = PreCompactInput.model_validate(event_input.model_dump())
        return typed.compact_trigger or event_input.event_name.value
    if event_input.event_name == HookEventName.POST_COMPACT:
        typed = PostCompactInput.model_validate(event_input.model_dump())
        return typed.compact_trigger or event_input.event_name.value
    return None


def hook_matches_event(
    group: HookMatcherGroup,
    event_input: HookEventInput,
    *,
    tool_name: str = "",
) -> bool:
    if group.role_ids and str(event_input.role_id or "") not in group.role_ids:
        return False
    if group.session_modes and event_input.session_mode not in group.session_modes:
        return False
    if group.run_kinds and event_input.run_kind not in group.run_kinds:
        return False
    matcher = group.matcher.strip() or "*"
    if event_input.event_name in MATCHER_UNSUPPORTED_EVENTS:
        return matcher == "*"
    matcher_target = get_matcher_target(event_input, tool_name=tool_name)
    if matcher == "*":
        return True
    if not matcher_target:
        return False
    return _matcher_matches(matcher_target=matcher_target, matcher=matcher)


def _matcher_matches(*, matcher_target: str, matcher: str) -> bool:
    return any(
        fnmatch.fnmatchcase(matcher_target, part.strip())
        for part in matcher.split("|")
        if part.strip()
    )
