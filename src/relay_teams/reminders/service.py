from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from threading import Lock

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
        self._state_locks = _RunStateLockRegistry()
        self._async_state_locks = _AsyncRunStateLockRegistry()

    @property
    def policy(self) -> SystemReminderPolicy:
        return self._policy

    def observe_tool_result(
        self,
        observation: ToolResultObservation,
    ) -> ReminderDecision:
        with self._state_locks.hold(
            session_id=observation.session_id,
            run_id=observation.run_id,
        ):
            return self._observe_tool_result_locked(observation)

    def _observe_tool_result_locked(
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
                    visibility="internal",
                    internal_kind=_decision_kind(decision),
                    internal_delivery_mode=decision.delivery_mode.value,
                    internal_issue_key=decision.issue_key,
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
        async with self._async_state_locks.hold(
            session_id=observation.session_id,
            run_id=observation.run_id,
        ):
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
                        visibility="internal",
                        internal_kind=_decision_kind(decision),
                        internal_delivery_mode=decision.delivery_mode.value,
                        internal_issue_key=decision.issue_key,
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
        with self._state_locks.hold(
            session_id=observation.session_id,
            run_id=observation.run_id,
        ):
            return self._evaluate_completion_attempt_locked(observation)

    def _evaluate_completion_attempt_locked(
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
                    visibility="internal",
                    internal_kind=_decision_kind(decision),
                    internal_delivery_mode=decision.delivery_mode.value,
                    internal_issue_key=decision.issue_key,
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
        async with self._async_state_locks.hold(
            session_id=observation.session_id,
            run_id=observation.run_id,
        ):
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
                    _ = await self._injection_sink.append_and_enqueue_async(
                        session_id=observation.session_id,
                        run_id=observation.run_id,
                        trace_id=observation.trace_id,
                        task_id=observation.task_id,
                        instance_id=observation.instance_id,
                        role_id=observation.role_id,
                        workspace_id=observation.workspace_id,
                        conversation_id=observation.conversation_id,
                        content=content,
                        visibility="internal",
                        internal_kind=_decision_kind(decision),
                        internal_delivery_mode=decision.delivery_mode.value,
                        internal_issue_key=decision.issue_key,
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
        with self._state_locks.hold(
            session_id=observation.session_id,
            run_id=observation.run_id,
        ):
            return self._observe_context_pressure_locked(observation)

    def _observe_context_pressure_locked(
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
                    visibility="internal",
                    internal_kind=_decision_kind(decision),
                    internal_delivery_mode=decision.delivery_mode.value,
                    internal_issue_key=decision.issue_key,
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
        async with self._async_state_locks.hold(
            session_id=observation.session_id,
            run_id=observation.run_id,
        ):
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
                        visibility="internal",
                        internal_kind=_decision_kind(decision),
                        internal_delivery_mode=decision.delivery_mode.value,
                        internal_issue_key=decision.issue_key,
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


class _RunStateLockSlot:
    def __init__(self) -> None:
        self.lock = Lock()
        self.references = 0


class _AsyncRunStateLockSlot:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.references = 0


class _RunStateLockRegistry:
    def __init__(self) -> None:
        self._guard = Lock()
        self._slots: dict[tuple[str, str], _RunStateLockSlot] = {}

    @contextmanager
    def hold(self, *, session_id: str, run_id: str) -> Iterator[None]:
        key = _state_cache_key(session_id=session_id, run_id=run_id)
        slot = self._retain(key)
        slot.lock.acquire()
        try:
            yield
        finally:
            slot.lock.release()
            self._release(key=key, slot=slot)

    def _retain(self, key: tuple[str, str]) -> _RunStateLockSlot:
        with self._guard:
            slot = self._slots.get(key)
            if slot is None:
                slot = _RunStateLockSlot()
                self._slots[key] = slot
            slot.references += 1
            return slot

    def _release(self, *, key: tuple[str, str], slot: _RunStateLockSlot) -> None:
        with self._guard:
            slot.references -= 1
            if slot.references == 0 and self._slots.get(key) is slot:
                del self._slots[key]

    @property
    def active_lock_count(self) -> int:
        with self._guard:
            return len(self._slots)

    @property
    def active_reference_count(self) -> int:
        with self._guard:
            return sum(slot.references for slot in self._slots.values())


class _AsyncRunStateLockRegistry:
    def __init__(self) -> None:
        self._guard = Lock()
        self._slots: dict[tuple[str, str], _AsyncRunStateLockSlot] = {}

    @asynccontextmanager
    async def hold(self, *, session_id: str, run_id: str) -> AsyncIterator[None]:
        key = _state_cache_key(session_id=session_id, run_id=run_id)
        slot = self._retain(key)
        acquired = False
        try:
            await slot.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                slot.lock.release()
            self._release(key=key, slot=slot)

    def _retain(self, key: tuple[str, str]) -> _AsyncRunStateLockSlot:
        with self._guard:
            slot = self._slots.get(key)
            if slot is None:
                slot = _AsyncRunStateLockSlot()
                self._slots[key] = slot
            slot.references += 1
            return slot

    def _release(self, *, key: tuple[str, str], slot: _AsyncRunStateLockSlot) -> None:
        with self._guard:
            slot.references -= 1
            if slot.references == 0 and self._slots.get(key) is slot:
                del self._slots[key]

    @property
    def active_lock_count(self) -> int:
        with self._guard:
            return len(self._slots)

    @property
    def active_reference_count(self) -> int:
        with self._guard:
            return sum(slot.references for slot in self._slots.values())


def _decision_kind(decision: ReminderDecision) -> str:
    if decision.kind is None:
        return ""
    return decision.kind.value
