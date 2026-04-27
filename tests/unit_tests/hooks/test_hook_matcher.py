from __future__ import annotations

from relay_teams.hooks.hook_event_models import (
    HookEventInput,
    InstructionsLoadedInput,
    NotificationInput,
    PermissionDeniedInput,
    PostToolUseFailureInput,
    PostCompactInput,
    PostToolUseInput,
    PreCompactInput,
    SessionEndInput,
    SessionStartInput,
    StopFailureInput,
    StopInput,
    SubagentStartInput,
    SubagentStopInput,
)
from relay_teams.hooks.hook_matcher import get_matcher_target, hook_matches_event
from relay_teams.hooks.hook_models import (
    HookEventName,
    HookHandlerConfig,
    HookHandlerType,
    HookMatcherGroup,
)


def test_get_matcher_target_uses_tool_name_for_tool_events() -> None:
    event_input = PostToolUseInput(
        event_name=HookEventName.POST_TOOL_USE,
        session_id="session",
        run_id="run",
        trace_id="trace",
        tool_name="write",
        tool_call_id="call",
        tool_input={},
        tool_result={},
    )

    assert get_matcher_target(event_input, tool_name=event_input.tool_name) == "write"


def test_get_matcher_target_supports_permission_denied_tool_name() -> None:
    event_input = PermissionDeniedInput(
        event_name=HookEventName.PERMISSION_DENIED,
        session_id="session",
        run_id="run",
        trace_id="trace",
        tool_name="write",
        tool_call_id="call",
        tool_input={},
        denial_source="user_approval",
        approval_status="deny",
    )

    assert get_matcher_target(event_input, tool_name=event_input.tool_name) == "write"


def test_get_matcher_target_supports_instruction_loaded_role_id() -> None:
    event_input = InstructionsLoadedInput(
        event_name=HookEventName.INSTRUCTIONS_LOADED,
        session_id="session",
        run_id="run",
        trace_id="trace",
        role_id="writer",
        instruction_source_count=2,
    )

    assert get_matcher_target(event_input) == "writer"


def test_get_matcher_target_prefers_instruction_loaded_load_reason() -> None:
    event_input = InstructionsLoadedInput(
        event_name=HookEventName.INSTRUCTIONS_LOADED,
        session_id="session",
        run_id="run",
        trace_id="trace",
        role_id="writer",
        mode="source",
        load_reason="initial",
        instruction_source_count=1,
    )

    assert get_matcher_target(event_input) == "initial"


def test_get_matcher_target_supports_notification_type_fallback() -> None:
    typed_event_input = NotificationInput(
        event_name=HookEventName.NOTIFICATION,
        session_id="session",
        run_id="run",
        trace_id="trace",
        notification_type="run_failed",
        title="Run failed",
        body="Run failed.",
        channels=("browser",),
        dedupe_key="run_failed:run",
    )
    fallback_event_input = typed_event_input.model_copy(
        update={"notification_type": ""}
    )

    assert get_matcher_target(typed_event_input) == "run_failed"
    assert get_matcher_target(fallback_event_input) == HookEventName.NOTIFICATION.value


def test_get_matcher_target_uses_start_reason_for_session_start() -> None:
    event_input = SessionStartInput(
        event_name=HookEventName.SESSION_START,
        session_id="session",
        run_id="run",
        trace_id="trace",
        workspace_id="workspace",
        start_reason="resume",
    )

    assert get_matcher_target(event_input) == "resume"


def test_get_matcher_target_falls_back_to_event_name_for_session_start_without_reason() -> (
    None
):
    event_input = SessionStartInput(
        event_name=HookEventName.SESSION_START,
        session_id="session",
        run_id="run",
        trace_id="trace",
        workspace_id="workspace",
    )

    assert get_matcher_target(event_input) == HookEventName.SESSION_START.value


def test_get_matcher_target_falls_back_to_event_name_for_compaction_without_trigger() -> (
    None
):
    pre_event_input = PreCompactInput(
        event_name=HookEventName.PRE_COMPACT,
        session_id="session",
        run_id="run",
        trace_id="trace",
        conversation_id="conversation",
    )
    post_event_input = PostCompactInput(
        event_name=HookEventName.POST_COMPACT,
        session_id="session",
        run_id="run",
        trace_id="trace",
        conversation_id="conversation",
    )

    assert get_matcher_target(pre_event_input) == HookEventName.PRE_COMPACT.value
    assert get_matcher_target(post_event_input) == HookEventName.POST_COMPACT.value


def test_hook_matches_event_ignores_matcher_for_unsupported_events() -> None:
    group = HookMatcherGroup(
        matcher="manual",
        hooks=(HookHandlerConfig(type=HookHandlerType.COMMAND, command="echo stop"),),
    )
    event_input = StopInput(
        event_name=HookEventName.STOP,
        session_id="session",
        run_id="run",
        trace_id="trace",
    )

    assert hook_matches_event(group, event_input) is False


def test_hook_matches_event_allows_wildcard_for_unsupported_events() -> None:
    group = HookMatcherGroup(
        matcher="*",
        hooks=(HookHandlerConfig(type=HookHandlerType.COMMAND, command="echo stop"),),
    )
    event_input = StopInput(
        event_name=HookEventName.STOP,
        session_id="session",
        run_id="run",
        trace_id="trace",
    )

    assert hook_matches_event(group, event_input) is True


def test_get_matcher_target_uses_end_reason_and_completion_reason() -> None:
    end_reason_input = SessionEndInput(
        event_name=HookEventName.SESSION_END,
        session_id="session",
        run_id="run",
        trace_id="trace",
        end_reason="manual",
    )
    completion_reason_input = SessionEndInput(
        event_name=HookEventName.SESSION_END,
        session_id="session",
        run_id="run",
        trace_id="trace",
        completion_reason="completed",
    )

    assert get_matcher_target(end_reason_input) == "manual"
    assert get_matcher_target(completion_reason_input) == "completed"


def test_get_matcher_target_supports_stop_failure_and_subagent_events() -> None:
    stop_failure = StopFailureInput(
        event_name=HookEventName.STOP_FAILURE,
        session_id="session",
        run_id="run",
        trace_id="trace",
        error_code="tool_timeout",
    )
    subagent_start = SubagentStartInput(
        event_name=HookEventName.SUBAGENT_START,
        session_id="session",
        run_id="run",
        trace_id="trace",
        subagent_run_id="sub-run",
        subagent_task_id="task-1",
        subagent_instance_id="instance-1",
        subagent_role_id="Reviewer",
        subagent_type="verifier",
    )
    subagent_stop = SubagentStopInput(
        event_name=HookEventName.SUBAGENT_STOP,
        session_id="session",
        run_id="run",
        trace_id="trace",
        subagent_run_id="sub-run",
        subagent_task_id="task-1",
        subagent_instance_id="instance-1",
        subagent_role_id="Reviewer",
    )

    assert get_matcher_target(stop_failure) == "tool_timeout"
    assert get_matcher_target(subagent_start) == "verifier"
    assert get_matcher_target(subagent_stop) == "Reviewer"


def test_hook_matches_event_respects_filters_and_matcher_target_presence() -> None:
    group = HookMatcherGroup(
        matcher="edit",
        role_ids=("Reviewer",),
        session_modes=("interactive",),
        run_kinds=("task",),
        hooks=(HookHandlerConfig(type=HookHandlerType.COMMAND, command="echo ok"),),
    )
    event_input = PostToolUseFailureInput(
        event_name=HookEventName.POST_TOOL_USE_FAILURE,
        session_id="session",
        run_id="run",
        trace_id="trace",
        role_id="Reviewer",
        session_mode="interactive",
        run_kind="task",
        tool_name="edit",
        tool_call_id="call",
        tool_input={},
        tool_error={},
    )

    assert hook_matches_event(group, event_input, tool_name="edit") is True
    assert hook_matches_event(group, event_input, tool_name="read") is False
    assert (
        hook_matches_event(
            group,
            event_input.model_copy(update={"role_id": "Writer"}),
            tool_name="edit",
        )
        is False
    )
    assert (
        hook_matches_event(
            group,
            event_input.model_copy(update={"session_mode": "batch"}),
            tool_name="edit",
        )
        is False
    )
    assert (
        hook_matches_event(
            group,
            event_input.model_copy(update={"run_kind": "session"}),
            tool_name="edit",
        )
        is False
    )


def test_hook_matches_event_supports_pipe_alternation() -> None:
    group = HookMatcherGroup(
        matcher="write|edit",
        hooks=(HookHandlerConfig(type=HookHandlerType.COMMAND, command="echo ok"),),
    )
    event_input = PostToolUseInput(
        event_name=HookEventName.POST_TOOL_USE,
        session_id="session",
        run_id="run",
        trace_id="trace",
        tool_name="write",
        tool_call_id="call",
        tool_input={},
        tool_result={},
    )

    assert hook_matches_event(group, event_input, tool_name="write") is True
    assert hook_matches_event(group, event_input, tool_name="edit") is True
    assert hook_matches_event(group, event_input, tool_name="read") is False


def test_hook_matches_event_returns_false_without_matcher_target() -> None:
    group = HookMatcherGroup(
        matcher="resume",
        hooks=(HookHandlerConfig(type=HookHandlerType.COMMAND, command="echo ok"),),
    )
    event_input = SessionEndInput(
        event_name=HookEventName.SESSION_END,
        session_id="session",
        run_id="run",
        trace_id="trace",
    )

    assert hook_matches_event(group, event_input) is False


def test_get_matcher_target_returns_none_for_unhandled_event() -> None:
    event_input = HookEventInput(
        event_name=HookEventName.TASK_CREATED,
        session_id="session",
        run_id="run",
        trace_id="trace",
    )

    assert get_matcher_target(event_input) is None
