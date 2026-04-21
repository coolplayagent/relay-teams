from __future__ import annotations

from relay_teams.hooks import HookEventName, SessionStartInput
from relay_teams.hooks.hook_models import HookMatcherGroup
from relay_teams.hooks.hook_matcher import hook_matches_event


def test_hook_matcher_matches_session_start_source() -> None:
    event = SessionStartInput(
        event_name=HookEventName.SESSION_START,
        session_id="session-1",
        run_id="run-1",
        trace_id="run-1",
        source="startup",
        model="default",
    )

    assert hook_matches_event(HookMatcherGroup(matcher="startup"), event_input=event)
    assert not hook_matches_event(
        HookMatcherGroup(matcher="resume"),
        event_input=event,
    )
