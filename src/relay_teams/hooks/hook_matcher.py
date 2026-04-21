from __future__ import annotations

import fnmatch

from relay_teams.hooks.hook_event_models import HookEventInput, SessionStartInput
from relay_teams.hooks.hook_models import HookMatcherGroup


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
    if group.tool_names and tool_name not in group.tool_names:
        return False
    matcher = group.matcher.strip() or "*"
    if matcher == "*":
        return True
    candidate = _matcher_candidate(event_input=event_input, tool_name=tool_name)
    return fnmatch.fnmatchcase(candidate, matcher)


def _matcher_candidate(*, event_input: HookEventInput, tool_name: str) -> str:
    if isinstance(event_input, SessionStartInput):
        return (
            event_input.source.strip()
            or event_input.model.strip()
            or str(event_input.agent_type or "").strip()
            or event_input.event_name.value
        )
    return tool_name or str(event_input.role_id or "") or event_input.event_name.value
