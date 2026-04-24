from __future__ import annotations

from relay_teams.reminders.models import (
    CompletionAttemptObservation,
    ContextPressureObservation,
    ReminderDecision,
    ToolResultObservation,
)
from relay_teams.reminders.policy import SystemReminderPolicy
from relay_teams.reminders.renderer import render_system_reminder
from relay_teams.reminders.state import (
    ReminderStateRepository,
    mark_issued,
)
from relay_teams.sessions.runs.system_injection import SystemInjectionSink


class SystemReminderService:
    def __init__(
        self,
        *,
        state_repository: ReminderStateRepository,
        injection_sink: SystemInjectionSink,
        policy: SystemReminderPolicy | None = None,
    ) -> None:
        self._state_repository = state_repository
        self._injection_sink = injection_sink
        self._policy = policy or SystemReminderPolicy()

    @property
    def policy(self) -> SystemReminderPolicy:
        return self._policy

    def observe_tool_result(
        self,
        observation: ToolResultObservation,
    ) -> ReminderDecision:
        state = self._state_repository.get_run_state(
            session_id=observation.session_id,
            run_id=observation.run_id,
        )
        decision, next_state = self._policy.evaluate_tool_result(
            observation=observation,
            state=state,
        )
        if decision.issue:
            next_state = mark_issued(state=next_state, issue_key=decision.issue_key)
            content = render_system_reminder(decision.content)
            if content:
                _ = self._injection_sink.enqueue_only(
                    session_id=observation.session_id,
                    run_id=observation.run_id,
                    trace_id=observation.trace_id,
                    task_id=observation.task_id,
                    instance_id=observation.instance_id,
                    role_id=observation.role_id,
                    content=content,
                )
        self._state_repository.save_run_state(
            session_id=observation.session_id,
            run_id=observation.run_id,
            state=next_state,
        )
        return decision

    def evaluate_completion_attempt(
        self,
        observation: CompletionAttemptObservation,
    ) -> ReminderDecision:
        state = self._state_repository.get_run_state(
            session_id=observation.session_id,
            run_id=observation.run_id,
        )
        decision, next_state = self._policy.evaluate_completion_attempt(
            observation=observation,
            state=state,
        )
        if decision.issue and decision.retry_completion:
            next_state = mark_issued(state=next_state, issue_key=decision.issue_key)
            content = render_system_reminder(decision.content)
            if content:
                _ = self._injection_sink.append_and_enqueue(
                    session_id=observation.session_id,
                    run_id=observation.run_id,
                    trace_id=observation.trace_id,
                    task_id=observation.task_id,
                    instance_id=observation.instance_id,
                    role_id=observation.role_id,
                    workspace_id=observation.workspace_id,
                    conversation_id=observation.conversation_id,
                    content=content,
                )
        self._state_repository.save_run_state(
            session_id=observation.session_id,
            run_id=observation.run_id,
            state=next_state,
        )
        return decision

    def observe_context_pressure(
        self,
        observation: ContextPressureObservation,
    ) -> ReminderDecision:
        state = self._state_repository.get_run_state(
            session_id=observation.session_id,
            run_id=observation.run_id,
        )
        decision, next_state = self._policy.evaluate_context_pressure(
            observation=observation,
            state=state,
        )
        if decision.issue:
            next_state = mark_issued(state=next_state, issue_key=decision.issue_key)
            content = render_system_reminder(decision.content)
            if content:
                _ = self._injection_sink.enqueue_only(
                    session_id=observation.session_id,
                    run_id=observation.run_id,
                    trace_id=observation.trace_id,
                    task_id=observation.task_id,
                    instance_id=observation.instance_id,
                    role_id=observation.role_id,
                    content=content,
                )
        self._state_repository.save_run_state(
            session_id=observation.session_id,
            run_id=observation.run_id,
            state=next_state,
        )
        return decision
