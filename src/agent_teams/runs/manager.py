from __future__ import annotations

import asyncio
from json import dumps
import logging
import uuid
from typing import cast

from agent_teams.agents.core.meta_agent import MetaAgent
from agent_teams.logger import get_logger, log_event
from agent_teams.notifications import (
    NotificationContext,
    NotificationService,
    NotificationType,
)
from agent_teams.runs.enums import InjectionSource, RunEventType
from agent_teams.runs.injection_queue import RunInjectionManager
from agent_teams.runs.control import RunControlManager
from agent_teams.runs.event_stream import RunEventHub
from agent_teams.runs.ids import new_trace_id
from agent_teams.runs.models import IntentInput, RunEvent, RunResult
from agent_teams.state.session_repo import SessionRepository
from agent_teams.tools.runtime import ToolApprovalAction, ToolApprovalManager
from agent_teams.trace import bind_trace_context

logger = get_logger(__name__)


class RunManager:
    def __init__(
        self,
        *,
        meta_agent: MetaAgent,
        injection_manager: RunInjectionManager,
        run_event_hub: RunEventHub,
        run_control_manager: RunControlManager,
        tool_approval_manager: ToolApprovalManager,
        session_repo: SessionRepository,
        notification_service: NotificationService | None = None,
    ) -> None:
        self._meta_agent: MetaAgent = meta_agent
        self._injection_manager: RunInjectionManager = injection_manager
        self._run_event_hub: RunEventHub = run_event_hub
        self._run_control_manager: RunControlManager = run_control_manager
        self._tool_approval_manager: ToolApprovalManager = tool_approval_manager
        self._session_repo: SessionRepository = session_repo
        self._notification_service: NotificationService | None = notification_service
        self._pending_runs: dict[str, IntentInput] = {}
        self._running_run_ids: set[str] = set()

    def _ensure_session(self, session_id: str | None) -> str:
        if not session_id:
            new_id = f"session-{uuid.uuid4().hex[:8]}"
            _ = self._session_repo.create(session_id=new_id)
            return new_id
        try:
            _ = self._session_repo.get(session_id)
            return session_id
        except KeyError:
            _ = self._session_repo.create(session_id=session_id)
            return session_id

    async def run_intent(self, intent: IntentInput) -> RunResult:
        session_id = self._ensure_session(intent.session_id)
        intent.session_id = session_id
        self._run_control_manager.assert_session_allows_main_input(session_id)
        run_id = new_trace_id().value
        with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=session_id):
            log_event(
                logger,
                logging.INFO,
                event="run.started.direct",
                message="Direct run started",
            )
            self._injection_manager.activate(run_id)
            try:
                result = await self._meta_agent.handle_intent(intent, trace_id=run_id)
                log_event(
                    logger,
                    logging.INFO,
                    event="run.completed.direct",
                    message="Direct run completed",
                    payload={"root_task_id": result.root_task_id},
                )
                return result
            finally:
                self._injection_manager.deactivate(run_id)

    def create_run(self, intent: IntentInput) -> tuple[str, str]:
        session_id = self._ensure_session(intent.session_id)
        intent.session_id = session_id
        self._run_control_manager.assert_session_allows_main_input(session_id)
        run_id = new_trace_id().value
        self._pending_runs[run_id] = intent
        with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=session_id):
            log_event(
                logger,
                logging.INFO,
                event="run.queued",
                message="Run queued for streaming execution",
            )
        return run_id, session_id

    def ensure_run_started(self, run_id: str) -> None:
        if run_id in self._running_run_ids:
            return
        intent = self._pending_runs.get(run_id)
        if intent is None:
            raise KeyError(f"Run {run_id} not found")

        self._running_run_ids.add(run_id)
        self._injection_manager.activate(run_id)
        session_id = intent.session_id
        if session_id is None:
            raise RuntimeError(f"Run {run_id} is missing session id")
        with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=session_id):
            log_event(
                logger, logging.INFO, event="run.started", message="Run worker started"
            )
        self._run_event_hub.publish(
            RunEvent(
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=None,
                event_type=RunEventType.RUN_STARTED,
                payload_json=dumps({"session_id": session_id}),
            )
        )

        async def _worker() -> None:
            try:
                result = await self._meta_agent.handle_intent(intent, trace_id=run_id)
                self._run_event_hub.publish(
                    RunEvent(
                        session_id=session_id,
                        run_id=run_id,
                        trace_id=result.trace_id,
                        task_id=result.root_task_id,
                        event_type=RunEventType.RUN_COMPLETED,
                        payload_json=dumps(result.model_dump()),
                    )
                )
                with bind_trace_context(
                    trace_id=run_id, run_id=run_id, session_id=session_id
                ):
                    log_event(
                        logger,
                        logging.INFO,
                        event="run.completed",
                        message="Run completed",
                        payload={"root_task_id": result.root_task_id},
                    )
                self._emit_notification(
                    notification_type=NotificationType.RUN_COMPLETED,
                    session_id=session_id,
                    run_id=run_id,
                    trace_id=result.trace_id,
                    title="Run Completed",
                    body=f"Run {run_id} completed successfully.",
                )
            except asyncio.CancelledError:
                self._run_control_manager.publish_run_stopped(
                    session_id=session_id,
                    run_id=run_id,
                    reason="stopped_by_user",
                )
                with bind_trace_context(
                    trace_id=run_id, run_id=run_id, session_id=session_id
                ):
                    log_event(
                        logger,
                        logging.WARNING,
                        event="run.stopped",
                        message="Run cancelled",
                        payload={"reason": "stopped_by_user"},
                    )
                self._emit_notification(
                    notification_type=NotificationType.RUN_STOPPED,
                    session_id=session_id,
                    run_id=run_id,
                    trace_id=run_id,
                    title="Run Stopped",
                    body=f"Run {run_id} was stopped by user.",
                )
            except Exception as exc:
                self._run_event_hub.publish(
                    RunEvent(
                        session_id=session_id,
                        run_id=run_id,
                        trace_id=run_id,
                        task_id=None,
                        event_type=RunEventType.RUN_FAILED,
                        payload_json=dumps({"error": str(exc)}),
                    )
                )
                with bind_trace_context(
                    trace_id=run_id, run_id=run_id, session_id=session_id
                ):
                    log_event(
                        logger,
                        logging.ERROR,
                        event="run.failed",
                        message="Run failed",
                        exc_info=exc,
                    )
                self._emit_notification(
                    notification_type=NotificationType.RUN_FAILED,
                    session_id=session_id,
                    run_id=run_id,
                    trace_id=run_id,
                    title="Run Failed",
                    body=f"Run {run_id} failed: {exc}",
                )
            finally:
                self._injection_manager.deactivate(run_id)
                self._run_control_manager.unregister_run_task(run_id)
                self._running_run_ids.discard(run_id)
                _ = self._pending_runs.pop(run_id, None)

        task = asyncio.create_task(_worker())
        self._run_control_manager.register_run_task(
            run_id=run_id,
            session_id=session_id,
            task=task,
        )

    async def stream_run_events(self, run_id: str):
        queue = self._run_event_hub.subscribe(run_id)
        self.ensure_run_started(run_id)

        while True:
            event = await queue.get()
            yield event
            if event.event_type in (
                RunEventType.RUN_COMPLETED,
                RunEventType.RUN_FAILED,
                RunEventType.RUN_STOPPED,
            ):
                self._run_event_hub.unsubscribe_all(run_id)
                break

    async def run_intent_stream(self, intent: IntentInput):
        run_id, _ = self.create_run(intent)
        async for event in self.stream_run_events(run_id):
            yield event

    def inject_message(
        self,
        run_id: str,
        source: InjectionSource,
        content: str,
    ):
        return self._run_control_manager.inject_to_running_agents(
            run_id=run_id,
            source=source,
            content=content,
        )

    def stop_run(self, run_id: str) -> None:
        self._run_control_manager.clear_paused_subagent_for_run(run_id)
        if run_id in self._pending_runs and run_id not in self._running_run_ids:
            intent = self._pending_runs.pop(run_id)
            session_id = intent.session_id
            if session_id is None:
                raise RuntimeError(f"Run {run_id} is missing session id")
            self._run_control_manager.publish_run_stopped(
                session_id=session_id,
                run_id=run_id,
                reason="stopped_before_start",
            )
            self._emit_notification(
                notification_type=NotificationType.RUN_STOPPED,
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                title="Run Stopped",
                body=f"Run {run_id} was stopped before start.",
            )
            return

        requested = self._run_control_manager.request_run_stop(run_id)
        if not requested and run_id not in self._running_run_ids:
            raise KeyError(f"Run {run_id} not found")

    def stop_subagent(self, run_id: str, instance_id: str) -> dict[str, str]:
        return self._run_control_manager.stop_subagent(
            run_id=run_id,
            instance_id=instance_id,
        )

    def inject_subagent_message(
        self,
        *,
        run_id: str,
        instance_id: str,
        content: str,
    ) -> None:
        self._run_control_manager.resume_subagent_with_message(
            run_id=run_id,
            instance_id=instance_id,
            content=content,
        )

    def resolve_tool_approval(
        self,
        run_id: str,
        tool_call_id: str,
        action: str,
        feedback: str = "",
    ) -> None:
        if action not in {"approve", "deny"}:
            raise ValueError(f"Unsupported action: {action}")
        self._tool_approval_manager.resolve_approval(
            run_id=run_id,
            tool_call_id=tool_call_id,
            action=cast(ToolApprovalAction, action),
            feedback=feedback,
        )

    def list_open_tool_approvals(self, run_id: str) -> list[dict[str, str]]:
        return self._tool_approval_manager.list_open_approvals(run_id=run_id)

    def _emit_notification(
        self,
        *,
        notification_type: NotificationType,
        session_id: str,
        run_id: str,
        trace_id: str,
        title: str,
        body: str,
    ) -> None:
        if self._notification_service is None:
            return
        _ = self._notification_service.emit(
            notification_type=notification_type,
            title=title,
            body=body,
            context=NotificationContext(
                session_id=session_id,
                run_id=run_id,
                trace_id=trace_id,
            ),
        )
