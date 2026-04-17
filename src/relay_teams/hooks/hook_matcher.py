from __future__ import annotations

import fnmatch

from relay_teams.hooks.hook_event_models import HookEventInput
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
    candidate = (
        tool_name or str(event_input.role_id or "") or event_input.event_name.value
    )
    return fnmatch.fnmatchcase(candidate, matcher)
