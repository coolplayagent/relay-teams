from __future__ import annotations

import logging

from relay_teams.logger import get_logger, log_event
from relay_teams.reminders.models import (
    CompletionAttemptObservation,
    ContextPressureObservation,
    ReminderDecision,
    ToolResultObservation,
)
from relay_teams.reminders.policy import SystemReminderPolicy
from relay_teams.reminders.renderer import render_system_reminder
from relay_teams.reminders.state import (
    ReminderRunState,
    ReminderStateRepository,
    mark_issued,
)
from relay_teams.sessions.runs.system_injection import SystemInjectionSink

LOGGER = get_logger(__name__)


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
        self._fallback_states: dict[tuple[str, str], ReminderRunState] = {}
        self._read_degraded_keys: set[tuple[str, str]] = set()
        self._write_degraded_keys: set[tuple[str, str]] = set()

    @property
    def policy(self) -> SystemReminderPolicy:
        return self._policy

    def observe_tool_result(
        self,
        observation: ToolResultObservation,
    ) -> ReminderDecision:
        state = self._load_state(
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
        self._save_state(
            session_id=observation.session_id,
            run_id=observation.run_id,
            state=next_state,
        )
        return decision

    async def observe_tool_result_async(
        self,
        observation: ToolResultObservation,
    ) -> ReminderDecision:
        state = await self._load_state_async(
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
                _ = await self._injection_sink.enqueue_only_async(
                    session_id=observation.session_id,
                    run_id=observation.run_id,
                    trace_id=observation.trace_id,
                    task_id=observation.task_id,
                    instance_id=observation.instance_id,
                    role_id=observation.role_id,
                    content=content,
                )
        await self._save_state_async(
            session_id=observation.session_id,
            run_id=observation.run_id,
            state=next_state,
        )
        return decision

    def evaluate_completion_attempt(
        self,
        observation: CompletionAttemptObservation,
    ) -> ReminderDecision:
        state = self._load_state(
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
                _ = self._injection_sink.append_only(
                    session_id=observation.session_id,
                    trace_id=observation.trace_id,
                    task_id=observation.task_id,
                    instance_id=observation.instance_id,
                    role_id=observation.role_id,
                    workspace_id=observation.workspace_id,
                    conversation_id=observation.conversation_id,
                    content=content,
                )
        self._save_state(
            session_id=observation.session_id,
            run_id=observation.run_id,
            state=next_state,
        )
        return decision

    async def evaluate_completion_attempt_async(
        self,
        observation: CompletionAttemptObservation,
    ) -> ReminderDecision:
        state = await self._load_state_async(
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
                _ = await self._injection_sink.append_only_async(
                    session_id=observation.session_id,
                    trace_id=observation.trace_id,
                    task_id=observation.task_id,
                    instance_id=observation.instance_id,
                    role_id=observation.role_id,
                    workspace_id=observation.workspace_id,
                    conversation_id=observation.conversation_id,
                    content=content,
                )
        await self._save_state_async(
            session_id=observation.session_id,
            run_id=observation.run_id,
            state=next_state,
        )
        return decision

    def observe_context_pressure(
        self,
        observation: ContextPressureObservation,
    ) -> ReminderDecision:
        state = self._load_state(
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
        self._save_state(
            session_id=observation.session_id,
            run_id=observation.run_id,
            state=next_state,
        )
        return decision

    async def observe_context_pressure_async(
        self,
        observation: ContextPressureObservation,
    ) -> ReminderDecision:
        state = await self._load_state_async(
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
                _ = await self._injection_sink.enqueue_only_async(
                    session_id=observation.session_id,
                    run_id=observation.run_id,
                    trace_id=observation.trace_id,
                    task_id=observation.task_id,
                    instance_id=observation.instance_id,
                    role_id=observation.role_id,
                    content=content,
                )
        await self._save_state_async(
            session_id=observation.session_id,
            run_id=observation.run_id,
            state=next_state,
        )
        return decision

    def _load_state(self, *, session_id: str, run_id: str) -> ReminderRunState:
        key = _state_cache_key(session_id=session_id, run_id=run_id)
        if key in self._write_degraded_keys:
            fallback_state = self._fallback_states.get(key)
            if fallback_state is not None:
                return fallback_state
        try:
            state = self._state_repository.get_run_state(
                session_id=session_id,
                run_id=run_id,
            )
            self._read_degraded_keys.discard(key)
            self._fallback_states.pop(key, None)
            return state
        except Exception as exc:
            fallback_state = self._fallback_states.get(key)
            if fallback_state is None:
                fallback_state = ReminderRunState()
                self._fallback_states[key] = fallback_state
            self._read_degraded_keys.add(key)
            log_event(
                LOGGER,
                logging.WARNING,
                event="reminders.state.load_failed",
                message="Falling back to in-memory reminder state",
                payload={"session_id": session_id, "run_id": run_id},
                exc_info=exc,
            )
            return fallback_state

    async def _load_state_async(
        self, *, session_id: str, run_id: str
    ) -> ReminderRunState:
        key = _state_cache_key(session_id=session_id, run_id=run_id)
        if key in self._write_degraded_keys:
            fallback_state = self._fallback_states.get(key)
            if fallback_state is not None:
                return fallback_state
        try:
            state = await self._state_repository.get_run_state_async(
                session_id=session_id,
                run_id=run_id,
            )
            self._read_degraded_keys.discard(key)
            self._fallback_states.pop(key, None)
            return state
        except Exception as exc:
            fallback_state = self._fallback_states.get(key)
            if fallback_state is None:
                fallback_state = ReminderRunState()
                self._fallback_states[key] = fallback_state
            self._read_degraded_keys.add(key)
            log_event(
                LOGGER,
                logging.WARNING,
                event="reminders.state.load_failed",
                message="Falling back to in-memory reminder state",
                payload={"session_id": session_id, "run_id": run_id},
                exc_info=exc,
            )
            return fallback_state

    def _save_state(
        self,
        *,
        session_id: str,
        run_id: str,
        state: ReminderRunState,
    ) -> None:
        key = _state_cache_key(session_id=session_id, run_id=run_id)
        try:
            self._state_repository.save_run_state(
                session_id=session_id,
                run_id=run_id,
                state=state,
            )
            self._write_degraded_keys.discard(key)
            if key in self._read_degraded_keys:
                self._fallback_states[key] = state
            else:
                self._fallback_states.pop(key, None)
        except Exception as exc:
            self._fallback_states[key] = state
            self._write_degraded_keys.add(key)
            log_event(
                LOGGER,
                logging.WARNING,
                event="reminders.state.save_failed",
                message="Ignoring reminder state persistence failure",
                payload={"session_id": session_id, "run_id": run_id},
                exc_info=exc,
            )

    async def _save_state_async(
        self,
        *,
        session_id: str,
        run_id: str,
        state: ReminderRunState,
    ) -> None:
        key = _state_cache_key(session_id=session_id, run_id=run_id)
        try:
            await self._state_repository.save_run_state_async(
                session_id=session_id,
                run_id=run_id,
                state=state,
            )
            self._write_degraded_keys.discard(key)
            if key in self._read_degraded_keys:
                self._fallback_states[key] = state
            else:
                self._fallback_states.pop(key, None)
        except Exception as exc:
            self._fallback_states[key] = state
            self._write_degraded_keys.add(key)
            log_event(
                LOGGER,
                logging.WARNING,
                event="reminders.state.save_failed",
                message="Ignoring reminder state persistence failure",
                payload={"session_id": session_id, "run_id": run_id},
                exc_info=exc,
            )


def _state_cache_key(*, session_id: str, run_id: str) -> tuple[str, str]:
    return session_id, run_id
