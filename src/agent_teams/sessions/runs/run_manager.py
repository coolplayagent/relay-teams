# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from json import dumps
from typing import Awaitable, Callable, cast

from pydantic_ai.messages import ModelRequest, UserPromptPart

from agent_teams.agents.orchestration.meta_agent import MetaAgent
from agent_teams.agents.instances.models import AgentRuntimeRecord
from agent_teams.logger import get_logger, log_event
from agent_teams.notifications import (
    NotificationContext,
    NotificationService,
    NotificationType,
)
from agent_teams.sessions.runs.active_run_registry import ActiveSessionRunRegistry
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.enums import ApprovalMode, InjectionSource, RunEventType
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.ids import new_trace_id
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.run_models import IntentInput, RunEvent, RunResult
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.tools.runtime.approval_ticket_repo import (
    ApprovalTicketRepository,
    ApprovalTicketStatus,
)
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.sessions.runs.run_state_repo import RunStateRepository
from agent_teams.sessions.session_repository import SessionRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.tools.runtime import ToolApprovalAction, ToolApprovalManager
from agent_teams.trace import bind_trace_context
from agent_teams.agents.tasks.models import TaskRecord

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
        active_run_registry: ActiveSessionRunRegistry,
        event_log: EventLog | None = None,
        task_repo: TaskRepository | None = None,
        agent_repo: AgentInstanceRepository | None = None,
        message_repo: MessageRepository | None = None,
        approval_ticket_repo: ApprovalTicketRepository | None = None,
        run_runtime_repo: RunRuntimeRepository | None = None,
        run_intent_repo: RunIntentRepository | None = None,
        run_state_repo: RunStateRepository | None = None,
        notification_service: NotificationService | None = None,
    ) -> None:
        self._meta_agent: MetaAgent = meta_agent
        self._injection_manager: RunInjectionManager = injection_manager
        self._run_event_hub: RunEventHub = run_event_hub
        self._run_control_manager: RunControlManager = run_control_manager
        self._tool_approval_manager: ToolApprovalManager = tool_approval_manager
        self._session_repo: SessionRepository = session_repo
        self._active_run_registry: ActiveSessionRunRegistry = active_run_registry
        self._event_log: EventLog | None = event_log
        self._task_repo: TaskRepository | None = task_repo
        self._agent_repo: AgentInstanceRepository | None = agent_repo
        self._message_repo: MessageRepository | None = message_repo
        self._approval_ticket_repo: ApprovalTicketRepository | None = (
            approval_ticket_repo
        )
        self._run_runtime_repo: RunRuntimeRepository | None = run_runtime_repo
        self._run_intent_repo: RunIntentRepository | None = run_intent_repo
        self._run_state_repo: RunStateRepository | None = run_state_repo
        self._notification_service: NotificationService | None = notification_service
        self._pending_runs: dict[str, IntentInput] = {}
        self._running_run_ids: set[str] = set()
        self._resume_requested_runs: set[str] = set()

    def _ensure_session(self, session_id: str) -> str:
        _ = self._session_repo.get(session_id)
        return session_id

    def _runtime_for_run(self, run_id: str) -> RunRuntimeRecord | None:
        if self._run_runtime_repo is not None:
            runtime = self._run_runtime_repo.get(run_id)
            if runtime is not None:
                return runtime
        return None

    def _active_recoverable_run(
        self, session_id: str
    ) -> tuple[str, RunRuntimeRecord | None] | None:
        run_id = self._active_run_registry.get_active_run_id(session_id)
        if not run_id:
            return None
        return run_id, self._runtime_for_run(run_id)

    def _remember_active_run(self, session_id: str, run_id: str) -> None:
        self._active_run_registry.remember_active_run(
            session_id=session_id,
            run_id=run_id,
        )

    def _drop_active_run(self, session_id: str, run_id: str) -> None:
        self._active_run_registry.drop_active_run(
            session_id=session_id,
            run_id=run_id,
        )

    async def run_intent(self, intent: IntentInput) -> RunResult:
        session_id = self._ensure_session(intent.session_id)
        intent.session_id = session_id
        self._run_control_manager.assert_session_allows_main_input(session_id)
        run_id = new_trace_id().value
        if self._run_runtime_repo is not None:
            self._run_runtime_repo.ensure(
                run_id=run_id,
                session_id=session_id,
                status=RunRuntimeStatus.RUNNING,
                phase=RunRuntimePhase.COORDINATOR_RUNNING,
            )
        if self._run_intent_repo is not None:
            self._run_intent_repo.upsert(
                run_id=run_id,
                session_id=session_id,
                intent=intent,
            )
        self._remember_active_run(session_id, run_id)
        with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=session_id):
            log_event(
                logger,
                logging.INFO,
                event="run.started.direct",
                message="Direct run started",
            )
            self._injection_manager.activate(run_id)
            self._running_run_ids.add(run_id)
            try:
                result = await self._meta_agent.handle_intent(intent, trace_id=run_id)
                if self._run_runtime_repo is not None:
                    self._run_runtime_repo.update(
                        run_id,
                        root_task_id=result.root_task_id,
                        status=RunRuntimeStatus.COMPLETED,
                        phase=RunRuntimePhase.TERMINAL,
                        active_instance_id=None,
                        active_task_id=None,
                        active_role_id=None,
                        active_subagent_instance_id=None,
                        last_error=None,
                    )
                log_event(
                    logger,
                    logging.INFO,
                    event="run.completed.direct",
                    message="Direct run completed",
                    payload={"root_task_id": result.root_task_id},
                )
                return result
            except Exception as exc:
                if self._run_runtime_repo is not None:
                    self._run_runtime_repo.update(
                        run_id,
                        status=RunRuntimeStatus.FAILED,
                        phase=RunRuntimePhase.TERMINAL,
                        active_instance_id=None,
                        active_task_id=None,
                        active_role_id=None,
                        active_subagent_instance_id=None,
                        last_error=str(exc),
                    )
                raise
            finally:
                self._injection_manager.deactivate(run_id)
                self._running_run_ids.discard(run_id)

    def create_run(self, intent: IntentInput) -> tuple[str, str]:
        session_id = self._ensure_session(intent.session_id)
        intent.session_id = session_id
        self._run_control_manager.assert_session_allows_main_input(session_id)

        existing = self._active_recoverable_run(session_id)
        if existing is not None:
            active_run_id, runtime = existing
            self._assert_auto_attach_allowed(active_run_id, runtime)
            if (
                active_run_id in self._pending_runs
                and active_run_id not in self._running_run_ids
            ):
                pending = self._pending_runs[active_run_id]
                pending.intent = self._merge_intent(pending.intent, intent.intent)
                pending.approval_mode = intent.approval_mode
                if self._run_intent_repo is not None:
                    self._run_intent_repo.upsert(
                        run_id=active_run_id,
                        session_id=session_id,
                        intent=pending,
                    )
                with bind_trace_context(
                    trace_id=active_run_id,
                    run_id=active_run_id,
                    session_id=session_id,
                ):
                    log_event(
                        logger,
                        logging.INFO,
                        event="run.followup.attached",
                        message="Follow-up merged into pending run",
                        payload={"mode": "pending_merge"},
                    )
                return active_run_id, session_id
            if (
                active_run_id in self._running_run_ids
                or self._injection_manager.is_active(active_run_id)
            ):
                self._append_followup_to_coordinator(
                    active_run_id, intent.intent, enqueue=True
                )
                with bind_trace_context(
                    trace_id=active_run_id,
                    run_id=active_run_id,
                    session_id=session_id,
                ):
                    log_event(
                        logger,
                        logging.INFO,
                        event="run.followup.attached",
                        message="Follow-up enqueued to active coordinator",
                        payload={"mode": "active_enqueue"},
                    )
                return active_run_id, session_id
            if runtime is not None and runtime.is_recoverable:
                self._append_followup_to_coordinator(
                    active_run_id, intent.intent, enqueue=False
                )
                self._update_run_approval_mode(
                    run_id=active_run_id,
                    session_id=session_id,
                    approval_mode=intent.approval_mode,
                )
                self._resume_requested_runs.add(active_run_id)
                with bind_trace_context(
                    trace_id=active_run_id,
                    run_id=active_run_id,
                    session_id=session_id,
                ):
                    log_event(
                        logger,
                        logging.INFO,
                        event="run.followup.attached",
                        message="Follow-up queued for recoverable run",
                        payload={"mode": "recoverable_resume"},
                    )
                return active_run_id, session_id

        run_id = new_trace_id().value
        self._pending_runs[run_id] = intent
        if self._run_runtime_repo is not None:
            self._run_runtime_repo.ensure(
                run_id=run_id,
                session_id=session_id,
                status=RunRuntimeStatus.QUEUED,
                phase=RunRuntimePhase.IDLE,
            )
        if self._run_intent_repo is not None:
            self._run_intent_repo.upsert(
                run_id=run_id, session_id=session_id, intent=intent
            )
        self._remember_active_run(session_id, run_id)
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
        if run_id in self._pending_runs:
            self._start_new_run_worker(run_id)
            return
        if run_id in self._resume_requested_runs:
            self._start_resume_worker(run_id)
            return
        raise KeyError(f"Run {run_id} not found")

    def _start_new_run_worker(self, run_id: str) -> None:
        intent = self._pending_runs.get(run_id)
        if intent is None:
            raise KeyError(f"Run {run_id} not found")
        session_id = intent.session_id
        if session_id is None:
            raise RuntimeError(f"Run {run_id} is missing session id")
        self._running_run_ids.add(run_id)
        self._injection_manager.activate(run_id)
        if self._run_runtime_repo is not None:
            self._run_runtime_repo.ensure(
                run_id=run_id,
                session_id=session_id,
                status=RunRuntimeStatus.RUNNING,
                phase=RunRuntimePhase.COORDINATOR_RUNNING,
            )
            self._run_runtime_repo.update(
                run_id,
                status=RunRuntimeStatus.RUNNING,
                phase=RunRuntimePhase.COORDINATOR_RUNNING,
                last_error=None,
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
        task = asyncio.create_task(
            self._worker(
                run_id=run_id,
                session_id=session_id,
                runner=lambda: self._meta_agent.handle_intent(intent, trace_id=run_id),
            )
        )
        self._run_control_manager.register_run_task(
            run_id=run_id,
            session_id=session_id,
            task=task,
        )

    def _start_resume_worker(self, run_id: str) -> None:
        runtime = self._runtime_for_run(run_id)
        if runtime is None:
            raise KeyError(f"Run {run_id} not found")
        session_id = runtime.session_id
        self._running_run_ids.add(run_id)
        self._resume_requested_runs.discard(run_id)
        self._injection_manager.activate(run_id)
        if self._run_runtime_repo is not None:
            self._run_runtime_repo.update(
                run_id,
                status=RunRuntimeStatus.RUNNING,
                phase=(
                    runtime.phase
                    if runtime.phase != RunRuntimePhase.TERMINAL
                    else RunRuntimePhase.COORDINATOR_RUNNING
                ),
                last_error=None,
            )
        self._run_event_hub.publish(
            RunEvent(
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=None,
                event_type=RunEventType.RUN_RESUMED,
                payload_json=dumps({"session_id": session_id, "reason": "resume"}),
            )
        )
        task = asyncio.create_task(
            self._worker(
                run_id=run_id,
                session_id=session_id,
                runner=lambda: self._resume_existing_run(run_id),
            )
        )
        self._run_control_manager.register_run_task(
            run_id=run_id,
            session_id=session_id,
            task=task,
        )
        with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=session_id):
            log_event(
                logger,
                logging.INFO,
                event="run.resumed",
                message="Recoverable run resumed",
            )

    async def _resume_existing_run(self, run_id: str) -> RunResult:
        try:
            _ = self._root_task_for_run(run_id)
        except KeyError:
            if self._run_intent_repo is None:
                raise
            intent = self._run_intent_repo.get(run_id)
            return await self._meta_agent.handle_intent(intent, trace_id=run_id)
        return await self._meta_agent.resume_run(trace_id=run_id)

    async def _worker(
        self,
        *,
        run_id: str,
        session_id: str,
        runner: Callable[[], Awaitable[RunResult]],
    ) -> None:
        with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=session_id):
            log_event(
                logger,
                logging.INFO,
                event="run.started",
                message="Run worker started",
            )
        try:
            result = await runner()
            terminal_status = (
                RunRuntimeStatus.COMPLETED
                if result.status == "completed"
                else RunRuntimeStatus.FAILED
            )
            terminal_event_type = (
                RunEventType.RUN_COMPLETED
                if result.status == "completed"
                else RunEventType.RUN_FAILED
            )
            terminal_log_event = (
                "run.completed" if result.status == "completed" else "run.failed"
            )
            terminal_log_level = (
                logging.INFO if result.status == "completed" else logging.WARNING
            )
            notification_type = (
                NotificationType.RUN_COMPLETED
                if result.status == "completed"
                else NotificationType.RUN_FAILED
            )
            notification_title = (
                "Run Completed" if result.status == "completed" else "Run Failed"
            )
            notification_body = (
                f"Run {run_id} completed successfully."
                if result.status == "completed"
                else (
                    f"Run {run_id} failed: {result.output}"
                    if result.output
                    else f"Run {run_id} failed."
                )
            )
            self._safe_runtime_update(
                run_id,
                root_task_id=result.root_task_id,
                status=terminal_status,
                phase=RunRuntimePhase.TERMINAL,
                active_instance_id=None,
                active_task_id=None,
                active_role_id=None,
                active_subagent_instance_id=None,
                last_error=result.output
                if terminal_status == RunRuntimeStatus.FAILED
                else None,
            )
            self._safe_publish_run_event(
                RunEvent(
                    session_id=session_id,
                    run_id=run_id,
                    trace_id=result.trace_id,
                    task_id=result.root_task_id,
                    event_type=terminal_event_type,
                    payload_json=dumps(result.model_dump()),
                ),
                failure_event="run.event.publish_failed",
            )
            with bind_trace_context(
                trace_id=run_id, run_id=run_id, session_id=session_id
            ):
                log_event(
                    logger,
                    terminal_log_level,
                    event=terminal_log_event,
                    message="Run completed"
                    if result.status == "completed"
                    else "Run failed",
                    payload={
                        "root_task_id": result.root_task_id,
                        "status": result.status,
                    },
                )
            self._emit_notification(
                notification_type=notification_type,
                session_id=session_id,
                run_id=run_id,
                trace_id=result.trace_id,
                title=notification_title,
                body=notification_body,
            )
        except asyncio.CancelledError:
            self._safe_runtime_update(
                run_id,
                status=RunRuntimeStatus.STOPPED,
                phase=RunRuntimePhase.IDLE,
                active_instance_id=None,
                active_task_id=None,
                active_role_id=None,
                active_subagent_instance_id=None,
                last_error="stopped_by_user",
            )
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
            self._safe_runtime_update(
                run_id,
                status=RunRuntimeStatus.FAILED,
                phase=RunRuntimePhase.TERMINAL,
                active_instance_id=None,
                active_task_id=None,
                active_role_id=None,
                active_subagent_instance_id=None,
                last_error=str(exc),
            )
            self._safe_publish_run_event(
                RunEvent(
                    session_id=session_id,
                    run_id=run_id,
                    trace_id=run_id,
                    task_id=None,
                    event_type=RunEventType.RUN_FAILED,
                    payload_json=dumps({"error": str(exc)}),
                ),
                failure_event="run.event.publish_failed",
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
            self._safe_finalize_run(run_id=run_id, session_id=session_id)

    def _finalize_run(self, *, run_id: str, session_id: str) -> None:
        self._injection_manager.deactivate(run_id)
        self._run_control_manager.unregister_run_task(run_id)
        self._running_run_ids.discard(run_id)
        _ = self._pending_runs.pop(run_id, None)
        self._resume_requested_runs.discard(run_id)
        runtime = self._runtime_for_run(run_id)
        if runtime is not None and runtime.is_recoverable:
            self._remember_active_run(session_id, run_id)
            return
        self._drop_active_run(session_id, run_id)

    def _safe_finalize_run(self, *, run_id: str, session_id: str) -> None:
        try:
            self._finalize_run(run_id=run_id, session_id=session_id)
        except Exception as exc:
            with bind_trace_context(
                trace_id=run_id,
                run_id=run_id,
                session_id=session_id,
            ):
                log_event(
                    logger,
                    logging.ERROR,
                    event="run.finalize.failed",
                    message="Run finalization failed",
                    exc_info=exc,
                )
            self._injection_manager.deactivate(run_id)
            self._run_control_manager.unregister_run_task(run_id)
            self._running_run_ids.discard(run_id)
            _ = self._pending_runs.pop(run_id, None)
            self._resume_requested_runs.discard(run_id)

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
            if self._run_runtime_repo is not None:
                self._run_runtime_repo.update(
                    run_id,
                    status=RunRuntimeStatus.STOPPED,
                    phase=RunRuntimePhase.IDLE,
                    active_instance_id=None,
                    active_task_id=None,
                    active_role_id=None,
                    active_subagent_instance_id=None,
                    last_error="stopped_before_start",
                )
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
            with bind_trace_context(
                trace_id=run_id, run_id=run_id, session_id=session_id
            ):
                log_event(
                    logger,
                    logging.WARNING,
                    event="run.stopped",
                    message="Pending run stopped before worker start",
                    payload={"reason": "stopped_before_start"},
                )
            return

        requested = self._run_control_manager.request_run_stop(run_id)
        if not requested and run_id not in self._running_run_ids:
            raise KeyError(f"Run {run_id} not found")
        if self._run_runtime_repo is not None and requested:
            runtime = self._run_runtime_repo.get(run_id)
            if runtime is not None:
                self._run_runtime_repo.update(
                    run_id,
                    status=RunRuntimeStatus.STOPPED,
                    phase=runtime.phase,
                    last_error="stop_requested",
                )
        with bind_trace_context(trace_id=run_id, run_id=run_id):
            log_event(
                logger,
                logging.WARNING,
                event="run.stop.requested",
                message="Run stop requested",
                payload={"was_running": requested},
            )

    def resume_run(self, run_id: str) -> str:
        if run_id in self._running_run_ids:
            runtime = self._runtime_for_run(run_id)
            if runtime is None:
                raise KeyError(f"Run {run_id} not found")
            with bind_trace_context(
                trace_id=run_id, run_id=run_id, session_id=runtime.session_id
            ):
                log_event(
                    logger,
                    logging.INFO,
                    event="run.resume.skipped",
                    message="Resume requested for already running run",
                )
            return runtime.session_id
        if run_id in self._pending_runs:
            pending = self._pending_runs[run_id]
            if pending.session_id is None:
                raise RuntimeError(f"Run {run_id} is missing session id")
            self._resume_requested_runs.add(run_id)
            self._remember_active_run(pending.session_id, run_id)
            with bind_trace_context(
                trace_id=run_id, run_id=run_id, session_id=pending.session_id
            ):
                log_event(
                    logger,
                    logging.INFO,
                    event="run.resume.requested",
                    message="Resume requested for pending run",
                )
            return pending.session_id

        runtime = self._runtime_for_run(run_id)
        if runtime is None:
            raise KeyError(f"Run {run_id} not found")
        if not runtime.is_recoverable:
            raise RuntimeError(f"Run {run_id} is not recoverable")
        self._resume_requested_runs.add(run_id)
        self._remember_active_run(runtime.session_id, run_id)
        with bind_trace_context(
            trace_id=run_id, run_id=run_id, session_id=runtime.session_id
        ):
            log_event(
                logger,
                logging.INFO,
                event="run.resume.requested",
                message="Resume requested for recoverable run",
            )
        return runtime.session_id

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
        runtime = self._runtime_for_run(run_id)
        if (
            run_id not in self._running_run_ids
            and runtime is not None
            and runtime.is_recoverable
            and runtime.status == RunRuntimeStatus.STOPPED
        ):
            raise RuntimeError(
                f"Run {run_id} is stopped. Resume the run before resolving tool approval."
            )
        approval = self._tool_approval_manager.get_approval(
            run_id=run_id,
            tool_call_id=tool_call_id,
        )
        if self._approval_ticket_repo is not None:
            self._approval_ticket_repo.resolve(
                tool_call_id=tool_call_id,
                status=(
                    ApprovalTicketStatus.APPROVED
                    if action == "approve"
                    else ApprovalTicketStatus.DENIED
                ),
                feedback=feedback,
            )
        if approval is not None:
            self._tool_approval_manager.resolve_approval(
                run_id=run_id,
                tool_call_id=tool_call_id,
                action=cast(ToolApprovalAction, action),
                feedback=feedback,
            )
        if run_id in self._running_run_ids or runtime is None:
            return

        instance_id = approval["instance_id"] if approval is not None else None
        role_id = approval["role_id"] if approval is not None else None
        tool_name = approval["tool_name"] if approval is not None else ""
        self._run_event_hub.publish(
            RunEvent(
                session_id=runtime.session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=None,
                instance_id=instance_id or None,
                role_id=role_id or None,
                event_type=RunEventType.TOOL_APPROVAL_RESOLVED,
                payload_json=dumps(
                    {
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "action": action,
                        "feedback": feedback,
                        "instance_id": instance_id,
                        "role_id": role_id,
                    }
                ),
            )
        )

    def list_open_tool_approvals(self, run_id: str) -> list[dict[str, str]]:
        if self._approval_ticket_repo is None:
            return self._tool_approval_manager.list_open_approvals(run_id=run_id)
        return [
            {
                "tool_call_id": item.tool_call_id,
                "instance_id": item.instance_id,
                "role_id": item.role_id,
                "tool_name": item.tool_name,
                "args_preview": item.args_preview,
            }
            for item in self._approval_ticket_repo.list_open_by_run(run_id)
        ]

    def _merge_intent(self, current: str, followup: str) -> str:
        return f"{current}\n\n{followup}" if current.strip() else followup

    def _assert_auto_attach_allowed(
        self, run_id: str, runtime: RunRuntimeRecord | None
    ) -> None:
        if runtime is None:
            return
        if (
            self._approval_ticket_repo is not None
            and self._approval_ticket_repo.list_open_by_run(run_id)
        ):
            raise RuntimeError(
                f"Run {run_id} is waiting for tool approval. Resolve the pending approval before continuing."
            )
        if (
            runtime.phase == RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP
            and runtime.active_subagent_instance_id
        ):
            instance_id = runtime.active_subagent_instance_id
            role_id = instance_id
            if self._agent_repo is not None:
                try:
                    role_id = self._agent_repo.get_instance(instance_id).role_id
                except KeyError:
                    role_id = instance_id
            raise RuntimeError(
                f"Subagent {role_id} ({instance_id}) is paused in run {run_id}. "
                "Please send a follow-up message to that subagent first."
            )

    def _root_task_for_run(self, run_id: str) -> TaskRecord:
        task_repo = self._require_task_repo()
        for record in task_repo.list_by_trace(run_id):
            if record.envelope.parent_task_id is None:
                return record
        raise KeyError(f"No root task found for run_id={run_id}")

    def _append_followup_to_coordinator(
        self,
        run_id: str,
        content: str,
        *,
        enqueue: bool,
    ) -> None:
        try:
            root = self._root_task_for_run(run_id)
            session_id = root.envelope.session_id
            instance_id = self._run_control_manager.get_coordinator_instance_id(
                run_id=run_id,
                session_id=session_id,
            )
            if not instance_id:
                raise KeyError(
                    f"No coordinator instance found for session {session_id}"
                )
            record = self._require_agent_repo().get_instance(instance_id)
            self._require_message_repo().append(
                session_id=session_id,
                workspace_id=record.workspace_id,
                conversation_id=record.conversation_id,
                agent_role_id=record.role_id,
                instance_id=instance_id,
                task_id=root.envelope.task_id,
                trace_id=run_id,
                messages=[ModelRequest(parts=[UserPromptPart(content=content)])],
            )
            if enqueue and self._injection_manager.is_active(run_id):
                created = self._injection_manager.enqueue(
                    run_id=run_id,
                    recipient_instance_id=instance_id,
                    source=InjectionSource.USER,
                    content=content,
                )
                self._publish_injection_event(
                    run_id=run_id,
                    record=record,
                    payload=created.model_dump_json(),
                )
            with bind_trace_context(
                trace_id=run_id,
                run_id=run_id,
                session_id=session_id,
                instance_id=instance_id,
                role_id=record.role_id,
            ):
                log_event(
                    logger,
                    logging.INFO,
                    event="run.followup.attached",
                    message="Follow-up appended to coordinator conversation",
                    payload={
                        "enqueue": enqueue,
                        "length": len(content),
                    },
                )
            return
        except KeyError:
            if self._run_intent_repo is None:
                raise
            self._run_intent_repo.append_followup(run_id=run_id, content=content)

    def _update_run_approval_mode(
        self,
        *,
        run_id: str,
        session_id: str,
        approval_mode: ApprovalMode,
    ) -> None:
        if self._run_intent_repo is None:
            return
        try:
            intent = self._run_intent_repo.get(run_id)
        except KeyError:
            return
        if intent.approval_mode == approval_mode:
            return
        intent.session_id = session_id
        intent.approval_mode = approval_mode
        self._run_intent_repo.upsert(
            run_id=run_id,
            session_id=session_id,
            intent=intent,
        )

    def _publish_injection_event(
        self,
        *,
        run_id: str,
        record: AgentRuntimeRecord,
        payload: str,
    ) -> None:
        self._run_event_hub.publish(
            RunEvent(
                session_id=record.session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=None,
                instance_id=record.instance_id,
                role_id=record.role_id,
                event_type=RunEventType.INJECTION_ENQUEUED,
                payload_json=payload,
            )
        )

    def _require_task_repo(self) -> TaskRepository:
        if self._task_repo is None:
            raise RuntimeError("RunManager requires task_repo for recovery")
        return self._task_repo

    def _require_message_repo(self) -> MessageRepository:
        if self._message_repo is None:
            raise RuntimeError("RunManager requires message_repo for recovery")
        return self._message_repo

    def _require_agent_repo(self) -> AgentInstanceRepository:
        if self._agent_repo is None:
            raise RuntimeError("RunManager requires agent_repo for recovery")
        return self._agent_repo

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
        try:
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
        except Exception as exc:
            with bind_trace_context(
                trace_id=trace_id,
                run_id=run_id,
                session_id=session_id,
            ):
                log_event(
                    logger,
                    logging.ERROR,
                    event="run.notification.failed",
                    message="Run notification failed",
                    payload={"notification_type": notification_type.value},
                    exc_info=exc,
                )

    def _safe_runtime_update(self, run_id: str, **changes: object) -> None:
        if self._run_runtime_repo is None:
            return
        try:
            self._run_runtime_repo.update(run_id, **changes)
        except Exception as exc:
            session_id = ""
            try:
                runtime = self._runtime_for_run(run_id)
                session_id = runtime.session_id if runtime is not None else ""
            except Exception:
                session_id = ""
            with bind_trace_context(
                trace_id=run_id,
                run_id=run_id,
                session_id=session_id,
            ):
                log_event(
                    logger,
                    logging.ERROR,
                    event="run.runtime.update_failed",
                    message="Run runtime update failed",
                    payload={
                        "change_count": len(changes),
                        "change_keys": ",".join(sorted(changes.keys())),
                    },
                    exc_info=exc,
                )

    def _safe_publish_run_event(
        self,
        event: RunEvent,
        *,
        failure_event: str,
    ) -> None:
        try:
            self._run_event_hub.publish(event)
        except Exception as exc:
            with bind_trace_context(
                trace_id=event.trace_id,
                run_id=event.run_id,
                session_id=event.session_id,
            ):
                log_event(
                    logger,
                    logging.ERROR,
                    event=failure_event,
                    message="Run event publish failed",
                    payload={"event_type": event.event_type.value},
                    exc_info=exc,
                )
