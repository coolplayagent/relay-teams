# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
import logging
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
from relay_teams.memory.event_handler import MemoryEventHandler
from relay_teams.sessions.runs.enums import InjectionSource
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.sessions.session_repository import SessionRepository

LOGGER = logging.getLogger(__name__)


class AppendCoordinatorFollowup(Protocol):
    def __call__(
        self,
        run_id: str,
        content: str,
        *,
        enqueue: bool,
        source: InjectionSource,
    ) -> bool: ...


class TemporaryKnowledgeCaptureServiceLike(Protocol):
    async def capture_all_for_session(
        self,
        *,
        _session_id: str,
        _workspace_id: str,
    ) -> object:
        raise NotImplementedError  # pragma: no cover


class RunHookPipeline:
    def __init__(
        self,
        *,
        get_hook_service: Callable[[], HookService | None],
        session_repo: SessionRepository,
        run_event_hub: RunEventHub,
        append_followup_to_coordinator: AppendCoordinatorFollowup,
        memory_event_handler: MemoryEventHandler | None = None,
    ) -> None:
        self._get_hook_service = get_hook_service
        self._session_repo = session_repo
        self._run_event_hub = run_event_hub
        self._append_followup_to_coordinator = append_followup_to_coordinator
        self._memory_event_handler = memory_event_handler
        self._temporary_knowledge_capture_service: (
            TemporaryKnowledgeCaptureServiceLike | None
        ) = None

    def set_temporary_knowledge_capture_service(
        self,
        service: TemporaryKnowledgeCaptureServiceLike,
    ) -> None:
        """Wire the RP-2 TemporaryRoleKnowledgeCaptureService after construction.

        The pipeline keeps a protocol reference here so session shutdown can
        remain decoupled from the concrete role-memory capture implementation.
        """
        self._temporary_knowledge_capture_service = service

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
        session = await self._session_repo.get_async(session_id)
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
        # Memory bank lifecycle: trigger run and session consolidation.
        if self._memory_event_handler is not None:
            handler = self._memory_event_handler
            workspace_id = await self._resolve_workspace_id_async(session_id)
            if workspace_id is not None:
                try:
                    await handler.on_run_completed_async(
                        workspace_id=workspace_id,
                        session_id=session_id,
                    )
                except (ValueError, OSError, RuntimeError):
                    # Best-effort: memory lifecycle failures must not
                    # block session-end processing.
                    LOGGER.exception(
                        "memory bank on_run_completed failed; "
                        "workspace_id=%s session_id=%s",
                        workspace_id,
                        session_id,
                    )
                try:
                    await handler.on_session_completed_async(
                        workspace_id=workspace_id,
                        session_id=session_id,
                    )
                except (ValueError, OSError, RuntimeError):
                    # Best-effort: memory lifecycle failures must not
                    # block session-end processing.
                    LOGGER.exception(
                        "memory bank on_session_completed failed; "
                        "workspace_id=%s session_id=%s",
                        workspace_id,
                        session_id,
                    )

                # RP-2: capture temporary role knowledge before roles expire.
                capture_service = self._temporary_knowledge_capture_service
                if capture_service is not None:
                    try:
                        await capture_service.capture_all_for_session(
                            _session_id=session_id,
                            _workspace_id=workspace_id,
                        )
                    except (ValueError, OSError, RuntimeError):
                        LOGGER.warning(
                            "temporary role knowledge capture failed; "
                            "workspace_id=%s session_id=%s",
                            workspace_id,
                            session_id,
                            exc_info=True,
                        )

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

    async def _resolve_workspace_id_async(self, session_id: str) -> str | None:
        try:
            session = await self._session_repo.get_async(session_id)
        except KeyError:
            return None
        return session.workspace_id

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
