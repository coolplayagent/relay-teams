from __future__ import annotations

from pydantic_ai.messages import (
    ModelResponse,
    ModelRequest,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)

from relay_teams.agents.execution.spec_checkpoint import (
    build_spec_checkpoint_decision,
    count_completed_tool_calls,
    is_spec_checkpoint_content,
    latest_spec_checkpoint_position,
    render_spec_checkpoint,
    spec_checkpoint_reason,
    spec_checkpoint_marker,
)
from relay_teams.agents.tasks.enums import TaskSpecStrictness
from relay_teams.agents.tasks.models import (
    SpecCheckpointPolicy,
    TaskEnvelope,
    TaskLifecyclePolicy,
    TaskSpec,
    VerificationPlan,
)


def test_spec_checkpoint_triggers_after_tool_call_interval() -> None:
    task = _task(
        policy=SpecCheckpointPolicy(
            refresh_interval_tool_calls=2,
            refresh_interval_messages=99,
            refresh_interval_history_tokens=999_999,
        )
    )
    history = [
        ModelRequest(parts=[UserPromptPart(content="initial task")]),
        _tool_result("call-1"),
        _tool_result("call-2"),
    ]

    decision = build_spec_checkpoint_decision(
        task=task,
        role_id="Crafter",
        history=history,
    )

    assert decision.should_inject is True
    assert decision.sequence == 1
    assert decision.reason == "tool_calls>=2"
    assert "## Spec Checkpoint" in decision.content
    assert "- Summary: Build the endpoint" in decision.content
    assert "  - return HTTP 201" in decision.content
    assert "  - do not change the public route" in decision.content
    assert "  - pytest tests/unit_tests/api" in decision.content


def test_spec_checkpoint_counts_from_latest_checkpoint() -> None:
    task = _task(
        policy=SpecCheckpointPolicy(
            refresh_interval_tool_calls=2,
            refresh_interval_messages=99,
            refresh_interval_history_tokens=999_999,
        )
    )
    checkpoint = ModelRequest(
        parts=[
            SystemPromptPart(
                content=spec_checkpoint_marker(task_id=task.task_id, sequence=3)
            )
        ]
    )
    history = [
        _tool_result("call-1"),
        checkpoint,
        _tool_result("call-2"),
    ]

    decision = build_spec_checkpoint_decision(
        task=task,
        role_id="Crafter",
        history=history,
    )

    assert decision.should_inject is False
    assert decision.tool_calls_since_last_checkpoint == 1
    assert latest_spec_checkpoint_position(history=history, task_id=task.task_id) == (
        1,
        3,
    )
    assert is_spec_checkpoint_content(
        spec_checkpoint_marker(task_id=task.task_id, sequence=3),
        task_id=task.task_id,
    )


def test_spec_checkpoint_can_trigger_on_message_interval_without_tools() -> None:
    task = _task(
        policy=SpecCheckpointPolicy(
            refresh_interval_tool_calls=99,
            refresh_interval_messages=2,
            refresh_interval_history_tokens=999_999,
        )
    )

    decision = build_spec_checkpoint_decision(
        task=task,
        role_id="Crafter",
        history=[
            ModelRequest(parts=[UserPromptPart(content="first")]),
            ModelRequest(parts=[UserPromptPart(content="second")]),
        ],
    )

    assert decision.should_inject is True
    assert decision.reason == "messages>=2"


def test_spec_checkpoint_can_trigger_on_history_token_interval() -> None:
    task = _task(
        policy=SpecCheckpointPolicy(
            refresh_interval_tool_calls=99,
            refresh_interval_messages=99,
            refresh_interval_history_tokens=1,
        )
    )

    decision = build_spec_checkpoint_decision(
        task=task,
        role_id="Crafter",
        history=[ModelRequest(parts=[UserPromptPart(content="large context")])],
    )

    assert decision.should_inject is True
    assert decision.reason == "history_tokens>=1"


def test_spec_checkpoint_reason_combines_thresholds() -> None:
    reason = spec_checkpoint_reason(
        policy=SpecCheckpointPolicy(
            refresh_interval_tool_calls=2,
            refresh_interval_messages=3,
            refresh_interval_history_tokens=4,
        ),
        tool_calls_since_checkpoint=2,
        messages_since_checkpoint=3,
        tokens_since_checkpoint=4,
    )

    assert reason == "tool_calls>=2, messages>=3, history_tokens>=4"


def test_spec_checkpoint_skips_tasks_without_spec_content() -> None:
    task = _task(spec=TaskSpec())

    decision = build_spec_checkpoint_decision(
        task=task,
        role_id="Crafter",
        history=[_tool_result("call-1"), _tool_result("call-2")],
    )

    assert decision.should_inject is False
    assert count_completed_tool_calls([_tool_result("call-1")]) == 1


def test_render_spec_checkpoint_returns_empty_without_spec() -> None:
    task = _task().model_copy(update={"spec": None})

    content = render_spec_checkpoint(
        task=task,
        role_id="Crafter",
        sequence=1,
        reason="messages>=1",
        policy=SpecCheckpointPolicy(),
        tool_calls_since_checkpoint=0,
        messages_since_checkpoint=1,
        tokens_since_checkpoint=12,
    )

    assert content == ""


def test_spec_checkpoint_marker_clamps_negative_sequence() -> None:
    marker = spec_checkpoint_marker(task_id="task-1", sequence=-5)

    assert 'sequence="0"' in marker


def test_latest_spec_checkpoint_position_ignores_unrelated_content() -> None:
    history = [
        ModelResponse(
            parts=[
                TextPart(content=spec_checkpoint_marker(task_id="task-1", sequence=9))
            ],
            model_name="fake-model",
        ),
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=spec_checkpoint_marker(task_id="task-1", sequence=8)
                )
            ]
        ),
        ModelRequest(
            parts=[
                SystemPromptPart(
                    content=spec_checkpoint_marker(task_id="other-task", sequence=7)
                )
            ]
        ),
        ModelRequest(
            parts=[
                SystemPromptPart(
                    content='<!-- relay-spec-checkpoint task_id="task-1" -->'
                )
            ]
        ),
        ModelRequest(
            parts=[
                SystemPromptPart(
                    content=(
                        '<!-- relay-spec-checkpoint task_id="task-1" '
                        'sequence="not-a-number" -->'
                    )
                )
            ]
        ),
        ModelRequest(
            parts=[
                SystemPromptPart(
                    content=('<!-- relay-spec-checkpoint task_id="task-1" sequence="')
                )
            ]
        ),
    ]

    assert latest_spec_checkpoint_position(history=history, task_id="task-1") == (
        5,
        0,
    )


def test_spec_checkpoint_content_detection_filters_plain_and_task_mismatch() -> None:
    marker = spec_checkpoint_marker(task_id="task-1", sequence=1)

    assert is_spec_checkpoint_content("plain text") is False
    assert is_spec_checkpoint_content(marker) is True
    assert is_spec_checkpoint_content(marker, task_id="other-task") is False


def test_count_completed_tool_calls_includes_retry_prompts_with_tool_names() -> None:
    history = [
        ModelResponse(
            parts=[TextPart(content="provider text")], model_name="fake-model"
        ),
        ModelRequest(
            parts=[
                RetryPromptPart(
                    content="retry",
                    tool_name="shell",
                    tool_call_id="call-1",
                ),
                RetryPromptPart(content="retry", tool_call_id="call-2"),
                UserPromptPart(content="manual follow-up"),
            ]
        ),
    ]

    assert count_completed_tool_calls(history) == 1


def test_render_spec_checkpoint_clips_long_spec_items() -> None:
    content = render_spec_checkpoint(
        task=_task(
            policy=SpecCheckpointPolicy(max_summary_chars=2000),
            spec=TaskSpec(
                summary="Long requirement task",
                requirements=("x" * 800,),
                strictness=TaskSpecStrictness.HIGH,
            ),
        ),
        role_id="Crafter",
        sequence=1,
        reason="messages>=1",
        policy=SpecCheckpointPolicy(max_summary_chars=2000),
        tool_calls_since_checkpoint=0,
        messages_since_checkpoint=1,
        tokens_since_checkpoint=12,
    )

    assert "[truncated]" in content


def test_render_spec_checkpoint_clips_full_checkpoint_text() -> None:
    policy = SpecCheckpointPolicy(max_summary_chars=500)
    content = render_spec_checkpoint(
        task=_task(
            policy=policy,
            spec=TaskSpec(
                summary="Large task",
                requirements=tuple(f"requirement {index}" for index in range(80)),
                strictness=TaskSpecStrictness.HIGH,
            ),
        ),
        role_id="Crafter",
        sequence=1,
        reason="messages>=1",
        policy=policy,
        tool_calls_since_checkpoint=0,
        messages_since_checkpoint=1,
        tokens_since_checkpoint=12,
    )

    assert content.endswith("[spec checkpoint truncated]")
    assert len(content) <= policy.max_summary_chars


def _task(
    *,
    policy: SpecCheckpointPolicy | None = None,
    spec: TaskSpec | None = None,
) -> TaskEnvelope:
    return TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        role_id="Crafter",
        objective="Build endpoint",
        verification=VerificationPlan(),
        spec=spec
        or TaskSpec(
            summary="Build the endpoint",
            requirements=("return HTTP 201",),
            constraints=("do not change the public route",),
            acceptance_criteria=("new API test passes",),
            verification_commands=("pytest tests/unit_tests/api",),
            evidence_expectations=("pytest output",),
            strictness=TaskSpecStrictness.HIGH,
        ),
        lifecycle=TaskLifecyclePolicy(spec_checkpoint=policy or SpecCheckpointPolicy()),
    )


def _tool_result(tool_call_id: str) -> ModelRequest:
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="shell",
                tool_call_id=tool_call_id,
                content="ok",
            )
        ]
    )
