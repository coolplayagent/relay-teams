from __future__ import annotations

from pathlib import Path
from typing import cast

from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.reminders import (
    CompletionAttemptObservation,
    ContextPressureObservation,
    IncompleteTodoItem,
    ReminderKind,
    ReminderPolicyConfig,
    ReminderStateRepository,
    SystemReminderPolicy,
    SystemReminderService,
    ToolResultObservation,
)
from relay_teams.sessions.runs.system_injection import (
    SystemInjectionResult,
    SystemInjectionSink,
)


class _CapturingSink:
    def __init__(self) -> None:
        self.enqueued: list[str] = []
        self.appended: list[str] = []

    def enqueue_only(self, **kwargs: object) -> SystemInjectionResult:
        self.enqueued.append(str(kwargs["content"]))
        return SystemInjectionResult(enqueued=True)

    def append_and_enqueue(self, **kwargs: object) -> SystemInjectionResult:
        self.appended.append(str(kwargs["content"]))
        return SystemInjectionResult(appended=True, enqueued=True)

    def append_only(self, **kwargs: object) -> SystemInjectionResult:
        self.appended.append(str(kwargs["content"]))
        return SystemInjectionResult(appended=True)


def test_service_enqueues_tool_failure_reminder_once(tmp_path: Path) -> None:
    sink = _CapturingSink()
    service = _service(tmp_path, sink)
    observation = ToolResultObservation(
        session_id="session-1",
        run_id="run-1",
        trace_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="role-1",
        tool_name="read",
        tool_call_id="call-1",
        ok=False,
        error_type="file_missing",
        error_message="No such file",
    )

    first = service.observe_tool_result(observation)
    second = service.observe_tool_result(observation)

    assert first.issue is True
    assert second.issue is False
    assert len(sink.enqueued) == 1
    assert "<system-reminder>" in sink.enqueued[0]
    assert "No such file" in sink.enqueued[0]


def test_service_appends_completion_retry_reminder(tmp_path: Path) -> None:
    sink = _CapturingSink()
    service = _service(
        tmp_path,
        sink,
        policy=SystemReminderPolicy(ReminderPolicyConfig(completion_max_retries=2)),
    )

    decision = service.evaluate_completion_attempt(
        CompletionAttemptObservation(
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
    )

    assert decision.retry_completion is True
    assert len(sink.appended) == 1
    assert sink.enqueued == []
    assert "finish tests" in sink.appended[0]


def test_service_enqueues_context_pressure_reminder(tmp_path: Path) -> None:
    sink = _CapturingSink()
    service = _service(tmp_path, sink)

    decision = service.observe_context_pressure(
        ContextPressureObservation(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            task_id="task-1",
            instance_id="inst-1",
            role_id="role-1",
            conversation_id="conversation-1",
            kind=ReminderKind.POST_COMPACTION,
            message_count_before=20,
            message_count_after=8,
            estimated_tokens_before=1000,
            estimated_tokens_after=400,
            threshold_tokens=800,
            target_tokens=300,
        )
    )

    assert decision.issue is True
    assert len(sink.enqueued) == 1
    assert "Conversation history was compacted" in sink.enqueued[0]


def _service(
    tmp_path: Path,
    sink: _CapturingSink,
    *,
    policy: SystemReminderPolicy | None = None,
) -> SystemReminderService:
    return SystemReminderService(
        state_repository=ReminderStateRepository(
            SharedStateRepository(tmp_path / "state.db")
        ),
        injection_sink=cast(SystemInjectionSink, sink),
        policy=policy,
    )
