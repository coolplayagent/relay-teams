# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from pydantic import JsonValue
from pydantic_ai.messages import ModelRequest, UserPromptPart

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.instances.models import AgentRuntimeRecord
from relay_teams.agents.tasks.models import TaskRecord
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.logger import get_logger, log_event
from relay_teams.media import TextContentPart
from relay_teams.monitors import (
    MonitorActionType,
    MonitorEventEnvelope,
    MonitorSubscriptionRecord,
)
from relay_teams.sessions.runs.active_run_registry import ActiveSessionRunRegistry
from relay_teams.sessions.runs.background_tasks.manager import BackgroundTaskManager
from relay_teams.sessions.runs.background_tasks.models import BackgroundTaskRecord
from relay_teams.sessions.runs.background_tasks.service import BackgroundTaskService
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub, publish_run_event_async
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_models import IntentInput, RunEvent
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.user_question_repository import UserQuestionRepository
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.trace import bind_trace_context

logger = get_logger(__name__)


def assert_runtime_auto_attach_phase_allowed(
    run_id: str,
    runtime: RunRuntimeRecord,
) -> None:
    if runtime.phase == RunRuntimePhase.AWAITING_MANUAL_ACTION:
        raise RuntimeError(
            f"Run {run_id} is waiting for manual action. Resolve the manual gate before continuing."
        )


class RunFollowupRouter:
    def __init__(
        self,
        *,
        injection_manager: RunInjectionManager,
        run_control_manager: RunControlManager,
        active_run_registry: ActiveSessionRunRegistry,
        session_repo: SessionRepository,
        run_event_hub: RunEventHub,
        get_background_task_manager: Callable[[], BackgroundTaskManager | None],
        get_background_task_service: Callable[[], BackgroundTaskService | None],
        get_run_intent_repo: Callable[[], RunIntentRepository | None],
        get_approval_ticket_repo: Callable[[], ApprovalTicketRepository | None],
        get_agent_repo: Callable[[], AgentInstanceRepository | None],
        get_user_question_repo: Callable[[], UserQuestionRepository | None],
        require_agent_repo: Callable[[], AgentInstanceRepository],
        require_message_repo: Callable[[], MessageRepository],
        require_task_repo: Callable[[], TaskRepository],
        runtime_for_run: Callable[[str], RunRuntimeRecord | None],
        ensure_session: Callable[[str], str],
        create_run: Callable[[IntentInput, InjectionSource], tuple[str, str]],
        ensure_run_started: Callable[[str], None],
        remember_active_run: Callable[[str, str], None],
        create_run_async: Callable[
            [IntentInput, InjectionSource], Awaitable[tuple[str, str]]
        ]
        | None = None,
        ensure_run_started_async: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._injection_manager = injection_manager
        self._run_control_manager = run_control_manager
        self._active_run_registry = active_run_registry
        self._session_repo = session_repo
        self._run_event_hub = run_event_hub
        self._get_background_task_manager = get_background_task_manager
        self._get_background_task_service = get_background_task_service
        self._get_run_intent_repo = get_run_intent_repo
        self._get_approval_ticket_repo = get_approval_ticket_repo
        self._get_agent_repo = get_agent_repo
        self._get_user_question_repo = get_user_question_repo
        self._require_agent_repo = require_agent_repo
        self._require_message_repo = require_message_repo
        self._require_task_repo = require_task_repo
        self._runtime_for_run = runtime_for_run
        self._ensure_session = ensure_session
        self._create_run = create_run
        self._create_run_async = create_run_async
        self._ensure_run_started = ensure_run_started
        self._ensure_run_started_async = ensure_run_started_async
        self._remember_active_run = remember_active_run

    def handle_background_task_completion(
        self,
        *,
        record: BackgroundTaskRecord,
        message: str,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "background_task_id": record.background_task_id,
        }
        self.route_system_message(
            source_run_id=record.run_id,
            session_id=record.session_id,
            preferred_instance_id=record.instance_id,
            role_id=record.role_id,
            task_id_fallback="background-task-notification",
            message=message,
            allow_coordinator=True,
            event_prefix="background_task.notification",
            payload=payload,
            spawn_if_unroutable=False,
        )

    async def handle_background_task_completion_async(
        self,
        *,
        record: BackgroundTaskRecord,
        message: str,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "background_task_id": record.background_task_id,
        }
        await self.route_system_message_async(
            source_run_id=record.run_id,
            session_id=record.session_id,
            preferred_instance_id=record.instance_id,
            role_id=record.role_id,
            task_id_fallback="background-task-notification",
            message=message,
            allow_coordinator=True,
            event_prefix="background_task.notification",
            payload=payload,
            spawn_if_unroutable=False,
        )

    def handle_monitor_trigger(
        self,
        *,
        subscription: MonitorSubscriptionRecord,
        envelope: MonitorEventEnvelope,
        message: str,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "monitor_id": subscription.monitor_id,
            "event_name": envelope.event_name,
            "source_key": envelope.source_key,
        }
        if subscription.action.action_type == MonitorActionType.START_FOLLOWUP_RUN:
            self.spawn_system_followup_run(
                source_run_id=subscription.run_id,
                session_id=subscription.session_id,
                message=message,
                event_prefix="monitor.trigger",
                payload=payload,
            )
            return
        self.route_system_message(
            source_run_id=subscription.run_id,
            session_id=subscription.session_id,
            preferred_instance_id=(
                subscription.created_by_instance_id
                if subscription.action.action_type == MonitorActionType.WAKE_INSTANCE
                else None
            ),
            role_id=subscription.created_by_role_id,
            task_id_fallback="monitor-trigger-notification",
            message=message,
            allow_coordinator=subscription.action.action_type
            in {
                MonitorActionType.WAKE_INSTANCE,
                MonitorActionType.WAKE_COORDINATOR,
            },
            event_prefix="monitor.trigger",
            payload=payload,
        )

    def route_system_message(
        self,
        *,
        source_run_id: str,
        session_id: str,
        preferred_instance_id: str | None,
        role_id: str | None,
        task_id_fallback: str,
        message: str,
        allow_coordinator: bool,
        event_prefix: str,
        payload: dict[str, JsonValue],
        spawn_if_unroutable: bool = True,
    ) -> None:
        task_id = self.find_task_for_instance(
            run_id=source_run_id,
            instance_id=preferred_instance_id or "",
        )
        if (
            preferred_instance_id
            and self.can_enqueue_followup_to_instance(
                run_id=source_run_id,
                instance_id=preferred_instance_id,
            )
            and self.append_followup_to_instance(
                run_id=source_run_id,
                instance_id=preferred_instance_id,
                task_id=task_id or task_id_fallback,
                content=message,
                enqueue=True,
                source=InjectionSource.SYSTEM,
            )
        ):
            with bind_trace_context(
                trace_id=source_run_id,
                run_id=source_run_id,
                session_id=session_id,
                instance_id=preferred_instance_id,
                role_id=role_id,
            ):
                log_event(
                    logger,
                    logging.INFO,
                    event=f"{event_prefix}.enqueued",
                    message="System follow-up enqueued to originating instance",
                    payload=payload,
                )
            return
        if allow_coordinator and self.can_enqueue_followup_to_coordinator(
            source_run_id
        ):
            if self.append_followup_to_coordinator(
                source_run_id,
                message,
                enqueue=True,
                source=InjectionSource.SYSTEM,
            ):
                with bind_trace_context(
                    trace_id=source_run_id,
                    run_id=source_run_id,
                    session_id=session_id,
                ):
                    log_event(
                        logger,
                        logging.INFO,
                        event=f"{event_prefix}.enqueued",
                        message="System follow-up enqueued to coordinator",
                        payload=payload,
                    )
                return
        active_run_id = self._active_run_registry.get_active_run_id(session_id)
        if (
            allow_coordinator
            and active_run_id
            and active_run_id != source_run_id
            and self.can_enqueue_followup_to_coordinator(active_run_id)
            and self.append_followup_to_coordinator(
                active_run_id,
                message,
                enqueue=True,
                source=InjectionSource.SYSTEM,
            )
        ):
            with bind_trace_context(
                trace_id=active_run_id,
                run_id=active_run_id,
                session_id=session_id,
            ):
                log_event(
                    logger,
                    logging.INFO,
                    event=f"{event_prefix}.enqueued",
                    message="System follow-up enqueued to active coordinator",
                    payload={
                        **payload,
                        "source_run_id": source_run_id,
                        "target_run_id": active_run_id,
                    },
                )
            return
        if not spawn_if_unroutable:
            self._log_unroutable_system_message(
                source_run_id=source_run_id,
                session_id=session_id,
                event_prefix=event_prefix,
                payload=payload,
            )
            return
        self.spawn_system_followup_run(
            source_run_id=source_run_id,
            session_id=session_id,
            message=message,
            event_prefix=event_prefix,
            payload=payload,
        )

    async def route_system_message_async(
        self,
        *,
        source_run_id: str,
        session_id: str,
        preferred_instance_id: str | None,
        role_id: str | None,
        task_id_fallback: str,
        message: str,
        allow_coordinator: bool,
        event_prefix: str,
        payload: dict[str, JsonValue],
        spawn_if_unroutable: bool = True,
    ) -> None:
        task_id = await asyncio.to_thread(
            self.find_task_for_instance,
            run_id=source_run_id,
            instance_id=preferred_instance_id or "",
        )
        can_append_to_instance = preferred_instance_id and await asyncio.to_thread(
            self.can_enqueue_followup_to_instance,
            run_id=source_run_id,
            instance_id=preferred_instance_id,
        )
        if can_append_to_instance and preferred_instance_id:
            appended_to_instance = await asyncio.to_thread(
                self.append_followup_to_instance,
                run_id=source_run_id,
                instance_id=preferred_instance_id,
                task_id=task_id or task_id_fallback,
                content=message,
                enqueue=True,
                source=InjectionSource.SYSTEM,
            )
            if appended_to_instance:
                with bind_trace_context(
                    trace_id=source_run_id,
                    run_id=source_run_id,
                    session_id=session_id,
                    instance_id=preferred_instance_id,
                    role_id=role_id,
                ):
                    log_event(
                        logger,
                        logging.INFO,
                        event=f"{event_prefix}.enqueued",
                        message="System follow-up enqueued to originating instance",
                        payload=payload,
                    )
                return
        can_append_to_coordinator = allow_coordinator and await asyncio.to_thread(
            self.can_enqueue_followup_to_coordinator,
            source_run_id,
        )
        if can_append_to_coordinator:
            appended_to_coordinator = await asyncio.to_thread(
                self.append_followup_to_coordinator,
                source_run_id,
                message,
                enqueue=True,
                source=InjectionSource.SYSTEM,
            )
            if appended_to_coordinator:
                with bind_trace_context(
                    trace_id=source_run_id,
                    run_id=source_run_id,
                    session_id=session_id,
                ):
                    log_event(
                        logger,
                        logging.INFO,
                        event=f"{event_prefix}.enqueued",
                        message="System follow-up enqueued to coordinator",
                        payload=payload,
                    )
                return
        active_run_id = self._active_run_registry.get_active_run_id(session_id)
        can_append_to_active = (
            allow_coordinator
            and active_run_id
            and active_run_id != source_run_id
            and await asyncio.to_thread(
                self.can_enqueue_followup_to_coordinator,
                active_run_id,
            )
        )
        if can_append_to_active and active_run_id:
            appended_to_active = await asyncio.to_thread(
                self.append_followup_to_coordinator,
                active_run_id,
                message,
                enqueue=True,
                source=InjectionSource.SYSTEM,
            )
            if appended_to_active:
                with bind_trace_context(
                    trace_id=active_run_id,
                    run_id=active_run_id,
                    session_id=session_id,
                ):
                    log_event(
                        logger,
                        logging.INFO,
                        event=f"{event_prefix}.enqueued",
                        message="System follow-up enqueued to active coordinator",
                        payload={
                            **payload,
                            "source_run_id": source_run_id,
                            "target_run_id": active_run_id,
                        },
                    )
                return
        if not spawn_if_unroutable:
            self._log_unroutable_system_message(
                source_run_id=source_run_id,
                session_id=session_id,
                event_prefix=event_prefix,
                payload=payload,
            )
            return
        await self.spawn_system_followup_run_async(
            source_run_id=source_run_id,
            session_id=session_id,
            message=message,
            event_prefix=event_prefix,
            payload=payload,
        )

    @staticmethod
    def _log_unroutable_system_message(
        *,
        source_run_id: str,
        session_id: str,
        event_prefix: str,
        payload: dict[str, JsonValue],
    ) -> None:
        with bind_trace_context(
            trace_id=source_run_id,
            run_id=source_run_id,
            session_id=session_id,
        ):
            log_event(
                logger,
                logging.INFO,
                event=f"{event_prefix}.skipped",
                message="System follow-up skipped because the source run is no longer accepting injections",
                payload=payload,
            )

    def spawn_system_followup_run(
        self,
        *,
        source_run_id: str,
        session_id: str,
        message: str,
        event_prefix: str,
        payload: dict[str, JsonValue],
    ) -> str:
        normalized_session_id = self._ensure_session(session_id)
        active_run_before = self._active_run_registry.get_active_run_id(
            normalized_session_id
        )
        self._run_control_manager.assert_session_allows_main_input(
            normalized_session_id
        )
        _ = self._session_repo.mark_started(normalized_session_id)
        intent = IntentInput(
            session_id=normalized_session_id,
            input=(TextContentPart(text=message),),
        )
        new_run_id, _ = self._create_run(intent, InjectionSource.SYSTEM)
        self._ensure_run_started(new_run_id)
        if active_run_before in {
            None,
            source_run_id,
        } and self.has_active_background_tasks(source_run_id):
            self._remember_active_run(normalized_session_id, source_run_id)
            with bind_trace_context(
                trace_id=source_run_id,
                run_id=source_run_id,
                session_id=normalized_session_id,
            ):
                log_event(
                    logger,
                    logging.INFO,
                    event=f"{event_prefix}.source_run_retained",
                    message="Source run remains active while sibling background tasks are still running",
                    payload={
                        **payload,
                        "source_run_id": source_run_id,
                        "target_run_id": new_run_id,
                    },
                )
        with bind_trace_context(
            trace_id=new_run_id,
            run_id=new_run_id,
            session_id=normalized_session_id,
        ):
            log_event(
                logger,
                logging.INFO,
                event=f"{event_prefix}.spawned",
                message="System follow-up routed through create_run",
                payload={
                    **payload,
                    "source_run_id": source_run_id,
                    "target_run_id": new_run_id,
                },
            )
        return new_run_id

    async def spawn_system_followup_run_async(
        self,
        *,
        source_run_id: str,
        session_id: str,
        message: str,
        event_prefix: str,
        payload: dict[str, JsonValue],
    ) -> str:
        normalized_session_id = await asyncio.to_thread(
            self._ensure_session,
            session_id,
        )
        active_run_before = self._active_run_registry.get_active_run_id(
            normalized_session_id
        )
        await asyncio.to_thread(
            self._run_control_manager.assert_session_allows_main_input,
            normalized_session_id,
        )
        _ = await asyncio.to_thread(
            self._session_repo.mark_started,
            normalized_session_id,
        )
        intent = IntentInput(
            session_id=normalized_session_id,
            input=(TextContentPart(text=message),),
        )
        if self._create_run_async is None:
            new_run_id, _ = await asyncio.to_thread(
                self._create_run,
                intent,
                InjectionSource.SYSTEM,
            )
        else:
            new_run_id, _ = await self._create_run_async(
                intent,
                InjectionSource.SYSTEM,
            )
        if self._ensure_run_started_async is None:
            await asyncio.to_thread(self._ensure_run_started, new_run_id)
        else:
            await self._ensure_run_started_async(new_run_id)
        if active_run_before in {
            None,
            source_run_id,
        } and await asyncio.to_thread(self.has_active_background_tasks, source_run_id):
            await asyncio.to_thread(
                self._remember_active_run,
                normalized_session_id,
                source_run_id,
            )
            with bind_trace_context(
                trace_id=source_run_id,
                run_id=source_run_id,
                session_id=normalized_session_id,
            ):
                log_event(
                    logger,
                    logging.INFO,
                    event=f"{event_prefix}.source_run_retained",
                    message="Source run remains active while sibling background tasks are still running",
                    payload={
                        **payload,
                        "source_run_id": source_run_id,
                        "target_run_id": new_run_id,
                    },
                )
        with bind_trace_context(
            trace_id=new_run_id,
            run_id=new_run_id,
            session_id=normalized_session_id,
        ):
            log_event(
                logger,
                logging.INFO,
                event=f"{event_prefix}.spawned",
                message="System follow-up routed through create_run",
                payload={
                    **payload,
                    "source_run_id": source_run_id,
                    "target_run_id": new_run_id,
                },
            )
        return new_run_id

    def has_active_background_tasks(self, run_id: str) -> bool:
        records: tuple[BackgroundTaskRecord, ...]
        background_task_service = self._get_background_task_service()
        background_task_manager = self._get_background_task_manager()
        if background_task_service is not None:
            records = background_task_service.list_for_run(run_id)
        elif background_task_manager is not None:
            records = background_task_manager.list_for_run(run_id)
        else:
            return False
        return any(record.is_active for record in records)

    def assert_auto_attach_allowed(
        self, run_id: str, runtime: RunRuntimeRecord | None
    ) -> None:
        if runtime is None:
            return
        approval_ticket_repo = self._get_approval_ticket_repo()
        if approval_ticket_repo is not None and approval_ticket_repo.list_open_by_run(
            run_id
        ):
            raise RuntimeError(
                f"Run {run_id} is waiting for tool approval. Resolve the pending approval before continuing."
            )
        user_question_repo = self._get_user_question_repo()
        if (
            user_question_repo is not None
            and self.has_pending_resolvable_question_for_session(runtime.session_id)
        ):
            raise RuntimeError(
                f"Run {run_id} is waiting for manual action. Answer the pending question before continuing."
            )
        if runtime.status == RunRuntimeStatus.STOPPING:
            raise RuntimeError(
                f"Run {run_id} is stopping. Wait for it to stop before continuing."
            )
        assert_runtime_auto_attach_phase_allowed(run_id, runtime)
        if (
            runtime.phase == RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP
            and runtime.active_subagent_instance_id
        ):
            instance_id = runtime.active_subagent_instance_id
            role_id = instance_id
            agent_repo = self._get_agent_repo()
            if agent_repo is not None:
                try:
                    role_id = agent_repo.get_instance(instance_id).role_id
                except KeyError:
                    role_id = instance_id
            raise RuntimeError(
                f"Subagent {role_id} ({instance_id}) is paused in run {run_id}. "
                "Please send a follow-up message to that subagent first."
            )

    def root_task_for_run(self, run_id: str) -> TaskRecord:
        task_repo = self._require_task_repo()
        for record in task_repo.list_by_trace(run_id):
            if record.envelope.parent_task_id is None:
                return record
        raise KeyError(f"No root task found for run_id={run_id}")

    def find_task_for_instance(self, *, run_id: str, instance_id: str) -> str | None:
        if not instance_id:
            return None
        task_repo = self._require_task_repo()
        for record in task_repo.list_by_trace(run_id):
            if record.assigned_instance_id == instance_id:
                return record.envelope.task_id
        return None

    def can_enqueue_followup_to_instance(
        self, *, run_id: str, instance_id: str
    ) -> bool:
        if not self._injection_manager.is_active(run_id):
            return False
        try:
            record = self._require_agent_repo().get_instance(instance_id)
        except KeyError:
            return False
        return record.run_id == run_id and record.status == InstanceStatus.RUNNING

    def has_running_agents_for_run(self, run_id: str) -> bool:
        agent_repo = self._get_agent_repo()
        if agent_repo is None:
            return False
        return any(True for _ in agent_repo.list_running(run_id))

    async def has_running_agents_for_run_async(self, run_id: str) -> bool:
        agent_repo = self._get_agent_repo()
        if agent_repo is None:
            return False
        return any(True for _ in await agent_repo.list_running_async(run_id))

    def has_pending_resolvable_question_for_session(self, session_id: str) -> bool:
        user_question_repo = self._get_user_question_repo()
        if user_question_repo is None:
            return False
        for record in user_question_repo.list_by_session(session_id):
            if self._runtime_for_run(record.run_id) is not None:
                return True
        return False

    def can_enqueue_followup_to_coordinator(self, run_id: str) -> bool:
        if not self._injection_manager.is_active(run_id):
            return False
        try:
            root = self.root_task_for_run(run_id)
            session_id = root.envelope.session_id
            instance_id = self._run_control_manager.get_coordinator_instance_id(
                run_id=run_id,
                session_id=session_id,
            )
            if not instance_id:
                return False
            record = self._require_agent_repo().get_instance(instance_id)
        except KeyError:
            return False
        return record.run_id == run_id and record.status == InstanceStatus.RUNNING

    def append_followup_to_instance(
        self,
        *,
        run_id: str,
        instance_id: str,
        task_id: str,
        content: str,
        enqueue: bool,
        source: InjectionSource,
    ) -> bool:
        try:
            record = self._require_agent_repo().get_instance(instance_id)
            if record.run_id != run_id:
                raise KeyError(
                    f"Instance {instance_id} does not belong to run {run_id}"
                )
            appended = self._require_message_repo().append_user_prompt_if_missing(
                session_id=record.session_id,
                workspace_id=record.workspace_id,
                conversation_id=record.conversation_id,
                agent_role_id=record.role_id,
                instance_id=instance_id,
                task_id=task_id,
                trace_id=run_id,
                content=content,
            )
            if enqueue and self._injection_manager.is_active(run_id):
                created = self._injection_manager.enqueue(
                    run_id=run_id,
                    recipient_instance_id=instance_id,
                    source=source,
                    content=content,
                )
                self.publish_injection_event(
                    run_id=run_id,
                    record=record,
                    payload=created.model_dump_json(),
                )
            with bind_trace_context(
                trace_id=run_id,
                run_id=run_id,
                session_id=record.session_id,
                instance_id=instance_id,
                role_id=record.role_id,
            ):
                log_event(
                    logger,
                    logging.INFO,
                    event="run.followup.attached",
                    message="Follow-up appended to agent conversation",
                    payload={
                        "enqueue": enqueue,
                        "source": source.value,
                        "length": len(content),
                        "task_id": task_id,
                        "appended": appended,
                    },
                )
            return True
        except KeyError:
            return False

    def append_followup_to_coordinator(
        self,
        run_id: str,
        content: str,
        *,
        enqueue: bool,
        source: InjectionSource = InjectionSource.USER,
    ) -> bool:
        try:
            root = self.root_task_for_run(run_id)
            session_id = root.envelope.session_id
            instance_id = self._run_control_manager.get_coordinator_instance_id(
                run_id=run_id,
                session_id=session_id,
            )
            if not instance_id:
                raise KeyError(f"No root agent instance found for session {session_id}")
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
                    source=source,
                    content=content,
                )
                self.publish_injection_event(
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
                    message="Follow-up appended to root agent conversation",
                    payload={
                        "enqueue": enqueue,
                        "source": source.value,
                        "length": len(content),
                    },
                )
            return True
        except KeyError:
            run_intent_repo = self._get_run_intent_repo()
            if run_intent_repo is None:
                raise
            run_intent_repo.append_followup(run_id=run_id, content=content)
            return False

    def update_run_yolo(
        self,
        *,
        run_id: str,
        session_id: str,
        yolo: bool,
    ) -> None:
        run_intent_repo = self._get_run_intent_repo()
        if run_intent_repo is None:
            return
        try:
            intent = run_intent_repo.get(run_id, fallback_session_id=session_id)
        except KeyError:
            return
        if intent.yolo == yolo:
            return
        intent.session_id = session_id
        intent.yolo = yolo
        run_intent_repo.upsert(
            run_id=run_id,
            session_id=session_id,
            intent=intent,
        )

    def publish_injection_event(
        self,
        *,
        run_id: str,
        record: AgentRuntimeRecord,
        payload: str,
    ) -> None:
        event = RunEvent(
            session_id=record.session_id,
            run_id=run_id,
            trace_id=run_id,
            task_id=None,
            instance_id=record.instance_id,
            role_id=record.role_id,
            event_type=RunEventType.INJECTION_ENQUEUED,
            payload_json=payload,
        )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._run_event_hub.publish(event)
            return
        _ = asyncio.create_task(self._publish_injection_event_async(event))

    async def _publish_injection_event_async(self, event: RunEvent) -> None:
        try:
            await publish_run_event_async(self._run_event_hub, event)
        except Exception as exc:
            with bind_trace_context(
                trace_id=event.trace_id,
                run_id=event.run_id,
                session_id=event.session_id,
                instance_id=event.instance_id,
                role_id=event.role_id,
            ):
                log_event(
                    logger,
                    logging.ERROR,
                    event="run.followup.injection_event_publish_failed",
                    message="Failed to publish follow-up injection event",
                    payload={
                        "event_type": event.event_type.value,
                        "error": str(exc),
                    },
                    exc_info=exc,
                )
