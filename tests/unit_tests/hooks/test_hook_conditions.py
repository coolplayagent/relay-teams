from __future__ import annotations

from relay_teams.hooks.hook_conditions import hook_handler_condition_matches
from relay_teams.hooks.hook_event_models import (
    HookEventInput,
    PermissionDeniedInput,
    PermissionRequestInput,
    PostToolUseFailureInput,
    PostToolUseInput,
    PreToolUseInput,
)
from relay_teams.hooks.hook_models import HookEventName


def test_hook_handler_condition_matches_empty_rule() -> None:
    assert hook_handler_condition_matches(
        if_rule=None,
        event_input=HookEventInput(
            event_name=HookEventName.SESSION_START,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
        ),
        tool_name="",
    )


def test_hook_handler_condition_matches_tool_alias_and_pattern() -> None:
    event_input = PreToolUseInput(
        event_name=HookEventName.PRE_TOOL_USE,
        session_id="session-1",
        run_id="run-1",
        trace_id="trace-1",
        tool_name="shell",
        tool_call_id="tool-1",
        tool_input={"command": "git status --short"},
    )

    assert hook_handler_condition_matches(
        if_rule="Bash(git *)",
        event_input=event_input,
        tool_name="shell",
    )
    assert not hook_handler_condition_matches(
        if_rule="Bash(npm *)",
        event_input=event_input,
        tool_name="shell",
    )


def test_hook_handler_condition_rejects_invalid_or_wrong_tool_rules() -> None:
    event_input = PreToolUseInput(
        event_name=HookEventName.PRE_TOOL_USE,
        session_id="session-1",
        run_id="run-1",
        trace_id="trace-1",
        tool_name="read",
        tool_call_id="tool-1",
        tool_input={"path": "README.md"},
    )

    assert not hook_handler_condition_matches(
        if_rule="Read(*",
        event_input=event_input,
        tool_name="read",
    )
    assert not hook_handler_condition_matches(
        if_rule="Write(*)",
        event_input=event_input,
        tool_name="read",
    )


def test_hook_handler_condition_matches_supported_tool_event_inputs() -> None:
    event_inputs = (
        PermissionRequestInput(
            event_name=HookEventName.PERMISSION_REQUEST,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            tool_name="read",
            tool_call_id="tool-1",
            tool_input={"path": "README.md"},
        ),
        PermissionDeniedInput(
            event_name=HookEventName.PERMISSION_DENIED,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            tool_name="read",
            tool_call_id="tool-1",
            tool_input={"file_path": "README.md"},
        ),
        PostToolUseInput(
            event_name=HookEventName.POST_TOOL_USE,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            tool_name="read",
            tool_call_id="tool-1",
            tool_input={"path": "README.md"},
            tool_result={"ok": True},
        ),
        PostToolUseFailureInput(
            event_name=HookEventName.POST_TOOL_USE_FAILURE,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            tool_name="read",
            tool_call_id="tool-1",
            tool_input={"path": "README.md"},
            tool_error={"message": "failed"},
        ),
    )

    for event_input in event_inputs:
        assert hook_handler_condition_matches(
            if_rule="Read(README.md)",
            event_input=event_input,
            tool_name="read",
        )


def test_hook_handler_condition_requires_candidate_value() -> None:
    event_input = PreToolUseInput(
        event_name=HookEventName.PRE_TOOL_USE,
        session_id="session-1",
        run_id="run-1",
        trace_id="trace-1",
        tool_name="read",
        tool_call_id="tool-1",
        tool_input={"offset": 10},
    )

    assert not hook_handler_condition_matches(
        if_rule="Read(*)",
        event_input=event_input,
        tool_name="read",
    )
