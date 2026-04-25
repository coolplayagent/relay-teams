# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from relay_teams.hooks import (
    HookDecisionType,
    HookEventName,
    HookService,
    SessionEndInput,
    SessionStartInput,
    StopFailureInput,
    StopInput,
)
from relay_teams.sessions.runs.enums import InjectionSource
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.sessions.session_repository import SessionRepository


class AppendCoordinatorFollowup(Protocol):
    def __call__(
        self,
        run_id: str,
        content: str,
        *,
        enqueue: bool,
        source: InjectionSource,
    ) -> bool: ...


class RunHookPipeline:
    def __init__(
        self,
        *,
        get_hook_service: Callable[[], HookService | None],
        session_repo: SessionRepository,
        run_event_hub: RunEventHub,
        append_followup_to_coordinator: AppendCoordinatorFollowup,
    ) -> None:
        self._get_hook_service = get_hook_service
        self._session_repo = session_repo
        self._run_event_hub = run_event_hub
        self._append_followup_to_coordinator = append_followup_to_coordinator

    async def execute_session_start_hooks(
        self,
        *,
        run_id: str,
        session_id: str,
        intent: IntentInput,
    ) -> None:
        hook_service = self._get_hook_service()
        if hook_service is None:
            return
        session = self._session_repo.get(session_id)
        _ = await hook_service.execute(
            event_input=SessionStartInput(
                event_name=HookEventName.SESSION_START,
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                session_mode=(
                    intent.session_mode.value if intent.session_mode is not None else ""
                ),
                run_kind=intent.run_kind.value,
                workspace_id=session.workspace_id,
            ),
            run_event_hub=self._run_event_hub,
        )

    async def execute_session_end_hooks(
        self,
        *,
        run_id: str,
        session_id: str,
        status: str,
        completion_reason: str,
        output_text: str,
        root_task_id: str | None = None,
    ) -> None:
        hook_service = self._get_hook_service()
        if hook_service is None:
            return
        _ = await hook_service.execute(
            event_input=SessionEndInput(
                event_name=HookEventName.SESSION_END,
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=root_task_id,
                status=status,
                completion_reason=completion_reason,
                output_text=output_text,
            ),
            run_event_hub=self._run_event_hub,
        )

    async def execute_stop_hooks(
        self,
        *,
        run_id: str,
        session_id: str,
        completion_reason: str,
        output_text: str,
        root_task_id: str | None = None,
    ) -> bool:
        hook_service = self._get_hook_service()
        if hook_service is None:
            return False
        bundle = await hook_service.execute(
            event_input=StopInput(
                event_name=HookEventName.STOP,
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=root_task_id,
                completion_reason=completion_reason,
                output_text=output_text,
            ),
            run_event_hub=self._run_event_hub,
        )
        if bundle.additional_context:
            self._append_followup_to_coordinator(
                run_id,
                "\n\n".join(bundle.additional_context),
                enqueue=True,
                source=InjectionSource.SYSTEM,
            )
        return bundle.decision == HookDecisionType.RETRY

    async def execute_stop_failure_hooks(
        self,
        *,
        run_id: str,
        session_id: str,
        completion_reason: str,
        error_code: str,
        error_message: str,
        root_task_id: str | None = None,
    ) -> None:
        hook_service = self._get_hook_service()
        if hook_service is None:
            return
        _ = await hook_service.execute(
            event_input=StopFailureInput(
                event_name=HookEventName.STOP_FAILURE,
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=root_task_id,
                completion_reason=completion_reason,
                error_code=error_code,
                error_message=error_message,
            ),
            run_event_hub=self._run_event_hub,
        )
