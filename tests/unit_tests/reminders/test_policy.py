from __future__ import annotations

from relay_teams.reminders import (
    CompletionAttemptObservation,
    IncompleteTodoItem,
    ReminderKind,
    ReminderPolicyConfig,
    SystemReminderPolicy,
    ToolResultObservation,
)
from relay_teams.reminders.state import ReminderRunState, mark_issued


def test_policy_reminds_after_read_only_streak_threshold() -> None:
    policy = SystemReminderPolicy(ReminderPolicyConfig(read_only_streak_threshold=2))
    state = ReminderRunState()

    first, state = policy.evaluate_tool_result(
        observation=_tool_result("read"),
        state=state,
    )
    second, state = policy.evaluate_tool_result(
        observation=_tool_result("grep"),
        state=state,
    )

    assert first.issue is False
    assert second.issue is True
    assert second.kind == ReminderKind.READ_ONLY_STREAK
    assert state.read_only_streak == 2


def test_policy_default_read_only_streak_threshold_is_fifty() -> None:
    policy = SystemReminderPolicy()
    state = ReminderRunState()

    for index in range(49):
        decision, state = policy.evaluate_tool_result(
            observation=_tool_result("read", call_id=f"call-read-{index}"),
            state=state,
        )
        assert decision.issue is False

    decision, state = policy.evaluate_tool_result(
        observation=_tool_result("read", call_id="call-read-49"),
        state=state,
    )

    assert decision.issue is True
    assert decision.kind == ReminderKind.READ_ONLY_STREAK
    assert state.read_only_streak == 50


def test_policy_resets_read_only_streak_after_mutating_tool() -> None:
    policy = SystemReminderPolicy(ReminderPolicyConfig(read_only_streak_threshold=2))
    state = ReminderRunState(read_only_streak=1)

    decision, state = policy.evaluate_tool_result(
        observation=_tool_result("write"),
        state=state,
    )

    assert decision.issue is False
    assert state.read_only_streak == 0


def test_policy_dedupes_tool_failure_with_cooldown() -> None:
    policy = SystemReminderPolicy()
    state = ReminderRunState()
    observation = _tool_result(
        "read",
        ok=False,
        error_type="file_missing",
        error_message="No such file",
    )

    first, state = policy.evaluate_tool_result(
        observation=observation,
        state=state,
    )
    state = mark_issued(state=state, issue_key=first.issue_key)
    second, _ = policy.evaluate_tool_result(
        observation=observation,
        state=state,
    )

    assert first.issue is True
    assert first.kind == ReminderKind.TOOL_FAILURE
    assert second.issue is False


def test_policy_fails_completion_after_retry_limit() -> None:
    policy = SystemReminderPolicy(ReminderPolicyConfig(completion_max_retries=1))
    observation = CompletionAttemptObservation(
        session_id="session-1",
        run_id="run-1",
        trace_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="role-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        incomplete_todos=(
            IncompleteTodoItem(content="finish tests", status="pending"),
        ),
    )
    state = ReminderRunState()

    first, state = policy.evaluate_completion_attempt(
        observation=observation,
        state=state,
    )
    second, state = policy.evaluate_completion_attempt(
        observation=observation,
        state=state,
    )

    assert first.retry_completion is True
    assert second.fail_completion is True
    assert state.completion_retry_count == 2


def _tool_result(
    tool_name: str,
    *,
    call_id: str = "",
    ok: bool = True,
    error_type: str = "",
    error_message: str = "",
) -> ToolResultObservation:
    return ToolResultObservation(
        session_id="session-1",
        run_id="run-1",
        trace_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="role-1",
        tool_name=tool_name,
        tool_call_id=call_id or f"call-{tool_name}",
        ok=ok,
        error_type=error_type,
        error_message=error_message,
    )
