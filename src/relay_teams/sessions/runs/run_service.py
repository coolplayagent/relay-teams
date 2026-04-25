# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future as ThreadFuture
from json import dumps
from typing import Awaitable, Callable, TypeVar

from pydantic import JsonValue

from relay_teams.agents.orchestration.meta_agent import MetaAgent
from relay_teams.logger import get_logger, log_event
from relay_teams.media import MediaAssetService
from relay_teams.monitors import (
    MonitorActionType,
    MonitorEventEnvelope,
    MonitorRule,
    MonitorService,
    MonitorSourceKind,
    MonitorSubscriptionRecord,
)
from relay_teams.notifications import NotificationService, NotificationType
from relay_teams.agents.orchestration.settings_service import (
    OrchestrationSettingsService,
)
from relay_teams.providers.provider_contracts import (
    EchoProvider,
    LLMProvider,
)
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.sessions.runs.active_run_registry import ActiveSessionRunRegistry
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.assistant_errors import RunCompletionReason
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.media_run_executor import MediaRunExecutor
from relay_teams.sessions.runs.run_auxiliary import RunAuxiliaryService
from relay_teams.sessions.runs.run_event_publisher import RunEventPublisher
from relay_teams.sessions.runs.run_followups import RunFollowupRouter
from relay_teams.sessions.runs.run_hook_pipeline import RunHookPipeline
from relay_teams.sessions.runs.ids import new_trace_id
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_interactions import RunInteractionService
from relay_teams.sessions.runs.background_tasks.manager import (
    BackgroundTaskManager,
)
from relay_teams.sessions.runs.background_tasks.models import BackgroundTaskRecord
from relay_teams.sessions.runs.background_tasks.service import BackgroundTaskService
from relay_teams.sessions.runs.run_models import (
    IntentInput,
    RunEvent,
    RunKind,
    RunResult,
)
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.tools.runtime.approval_ticket_repo import (
    ApprovalTicketRecord,
    ApprovalTicketRepository,
)
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.recoverable_pause import RecoverableRunPauseError
from relay_teams.sessions.runs.run_recovery import (
    RunRecoveryService,
)
from relay_teams.sessions.runs.run_terminal_results import RunTerminalResultService
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.run_scheduler import RunScheduler
from relay_teams.sessions.runs.todo_service import TodoService
from relay_teams.sessions.runs.user_question_manager import UserQuestionManager
from relay_teams.sessions.runs.user_question_models import UserQuestionAnswerSubmission
from relay_teams.sessions.runs.user_question_repository import UserQuestionRepository
from relay_teams.sessions.runs.run_state_repo import RunStateRepository
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.tools.runtime.approval_state import ToolApprovalManager
from relay_teams.tools.workspace_tools.shell_approval_repo import (
    ShellApprovalRepository,
)
from relay_teams.trace import bind_trace_context
from relay_teams.agents.tasks.models import TaskRecord
from relay_teams.hooks import HookService

logger = get_logger(__name__)
_T = TypeVar("_T")


def _is_run_already_running_conflict(*, run_id: str, error: RuntimeError) -> bool:
    return str(error) == f"Run {run_id} is already running"


class SessionRunService:
    def __init__(
        self,
        *,
        meta_agent: MetaAgent,
        provider_factory: Callable[[RoleDefinition, str | None], LLMProvider]
        | None = None,
        role_registry: RoleRegistry | None = None,
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
        user_question_repo: UserQuestionRepository | None = None,
        run_runtime_repo: RunRuntimeRepository | None = None,
        run_intent_repo: RunIntentRepository | None = None,
        run_state_repo: RunStateRepository | None = None,
        background_task_manager: BackgroundTaskManager | None = None,
        background_task_service: BackgroundTaskService | None = None,
        todo_service: TodoService | None = None,
        monitor_service: MonitorService | None = None,
        notification_service: NotificationService | None = None,
        orchestration_settings_service: OrchestrationSettingsService | None = None,
        media_asset_service: MediaAssetService | None = None,
        runtime_role_resolver: RuntimeRoleResolver | None = None,
        shell_approval_repo: ShellApprovalRepository | None = None,
        user_question_manager: UserQuestionManager | None = None,
        hook_service: HookService | None = None,
    ) -> None:
        self._meta_agent: MetaAgent = meta_agent
        self._provider_factory = provider_factory or (
            lambda _role, _session_id: EchoProvider()
        )
        self._role_registry = role_registry
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
        self._user_question_repo: UserQuestionRepository | None = user_question_repo
        self._run_runtime_repo: RunRuntimeRepository | None = run_runtime_repo
        self._run_intent_repo: RunIntentRepository | None = run_intent_repo
        self._run_state_repo: RunStateRepository | None = run_state_repo
        self._background_task_manager = background_task_manager
        self._background_task_service = background_task_service
        self._todo_service = todo_service
        self._monitor_service = monitor_service
        self._notification_service: NotificationService | None = notification_service
        self._orchestration_settings_service = orchestration_settings_service
        self._media_asset_service = media_asset_service
        self._runtime_role_resolver = runtime_role_resolver
        self._shell_approval_repo = shell_approval_repo
        self._user_question_manager: UserQuestionManager | None = user_question_manager
        self._hook_service = hook_service
        self._event_publisher = RunEventPublisher(
            run_event_hub=self._run_event_hub,
            get_runtime=lambda run_id: self._runtime_for_run(run_id),
            get_run_runtime_repo=lambda: self._run_runtime_repo,
            get_notification_service=lambda: self._notification_service,
        )
        self._auxiliary_service = RunAuxiliaryService(
            get_monitor_service=lambda: self._monitor_service,
            get_background_task_manager=lambda: self._background_task_manager,
            get_background_task_service=lambda: self._background_task_service,
            get_todo_service=lambda: self._todo_service,
            get_run_session_id=self._run_session_id,
        )
        self._recovery_service = RunRecoveryService(
            get_event_log=lambda: self._event_log,
            get_runtime=lambda run_id: self._runtime_for_run(run_id),
            event_publisher=self._event_publisher,
            append_followup_to_instance=(
                lambda **kwargs: self._append_followup_to_instance(**kwargs)
            ),
            append_followup_to_coordinator=(
                lambda run_id, content, **kwargs: self._append_followup_to_coordinator(
                    run_id,
                    content,
                    **kwargs,
                )
            ),
            resume_existing_run=lambda run_id: self._resume_existing_run(run_id),
        )
        self._hook_pipeline = RunHookPipeline(
            get_hook_service=lambda: self._hook_service,
            session_repo=self._session_repo,
            run_event_hub=self._run_event_hub,
            append_followup_to_coordinator=(
                lambda run_id, content, **kwargs: self._append_followup_to_coordinator(
                    run_id,
                    content,
                    **kwargs,
                )
            ),
        )
        self._terminal_results = RunTerminalResultService(
            session_repo=self._session_repo,
            get_runtime=lambda run_id: self._runtime_for_run(run_id),
            get_agent_repo=lambda: self._agent_repo,
            require_message_repo=self._require_message_repo,
            event_publisher=self._event_publisher,
        )
        self._media_executor = MediaRunExecutor(
            session_repo=self._session_repo,
            get_role_registry=lambda: self._role_registry,
            provider_factory=lambda role, session_id: self._provider_factory(
                role,
                session_id,
            ),
            require_agent_repo=self._require_agent_repo,
            require_task_repo=self._require_task_repo,
            require_message_repo=self._require_message_repo,
            require_media_asset_service=self._require_media_asset_service,
            event_publisher=self._event_publisher,
            terminal_results=self._terminal_results,
        )
        self._followup_router = RunFollowupRouter(
            injection_manager=self._injection_manager,
            run_control_manager=self._run_control_manager,
            active_run_registry=self._active_run_registry,
            session_repo=self._session_repo,
            run_event_hub=self._run_event_hub,
            get_background_task_manager=lambda: self._background_task_manager,
            get_background_task_service=lambda: self._background_task_service,
            get_run_intent_repo=lambda: self._run_intent_repo,
            get_approval_ticket_repo=lambda: self._approval_ticket_repo,
            get_agent_repo=lambda: self._agent_repo,
            get_user_question_repo=lambda: self._user_question_repo,
            require_agent_repo=self._require_agent_repo,
            require_message_repo=self._require_message_repo,
            require_task_repo=self._require_task_repo,
            runtime_for_run=lambda run_id: self._runtime_for_run(run_id),
            ensure_session=self._ensure_session,
            create_run=lambda intent, source: self.create_run(intent, source=source),
            ensure_run_started=lambda run_id: self.ensure_run_started(run_id),
            remember_active_run=lambda session_id, run_id: self._remember_active_run(
                session_id,
                run_id,
            ),
        )
        self._interaction_service = RunInteractionService(
            run_control_manager=self._run_control_manager,
            tool_approval_manager=self._tool_approval_manager,
            get_approval_ticket_repo=lambda: self._approval_ticket_repo,
            get_shell_approval_repo=lambda: self._shell_approval_repo,
            require_user_question_repo=self._require_user_question_repo,
            get_user_question_repo=lambda: self._user_question_repo,
            get_user_question_manager=lambda: self._user_question_manager,
            get_runtime=lambda run_id: self._runtime_for_run(run_id),
            is_running_run=lambda run_id: run_id in self._running_run_ids,
            has_pending_resolvable_question_for_session=(
                self._has_pending_resolvable_question_for_session
            ),
            has_running_agents_for_run=self._has_running_agents_for_run,
            resume_run=lambda run_id: self.resume_run(run_id),
            ensure_run_started=lambda run_id: self.ensure_run_started(run_id),
            event_publisher=self._event_publisher,
        )
        self._pending_runs: dict[str, IntentInput] = {}
        self._running_run_ids: set[str] = set()
        self._resume_requested_runs: set[str] = set()
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._scheduler = RunScheduler(
            meta_agent=self._meta_agent,
            injection_manager=self._injection_manager,
            run_event_hub=self._run_event_hub,
            run_control_manager=self._run_control_manager,
            session_repo=self._session_repo,
            media_executor=self._media_executor,
            recovery_service=self._recovery_service,
            get_hook_service=lambda: self._hook_service,
            get_run_runtime_repo=lambda: self._run_runtime_repo,
            get_run_intent_repo=lambda: self._run_intent_repo,
            pending_runs=self._pending_runs,
            running_run_ids=self._running_run_ids,
            resume_requested_runs=self._resume_requested_runs,
            should_delegate_to_bound_loop=self._should_delegate_to_bound_loop,
            call_in_bound_loop=self._call_in_bound_loop,
            ensure_session=self._ensure_session,
            prepare_intent=self._prepare_intent,
            active_recoverable_run=self._active_recoverable_run,
            run_accepts_followups=self._run_accepts_followups,
            assert_auto_attach_allowed=self._assert_auto_attach_allowed,
            merge_intent=self._merge_intent,
            append_followup_to_coordinator=(
                lambda run_id, content, enqueue, source: (
                    self._append_followup_to_coordinator(
                        run_id,
                        content,
                        enqueue=enqueue,
                        source=source,
                    )
                )
            ),
            update_run_yolo=(
                lambda run_id, session_id, yolo: self._followup_router.update_run_yolo(
                    run_id=run_id,
                    session_id=session_id,
                    yolo=yolo,
                )
            ),
            remember_active_run=self._remember_active_run,
            runtime_for_run=lambda run_id: self._runtime_for_run(run_id),
            worker=lambda run_id, session_id, runner: asyncio.create_task(
                self._worker(run_id=run_id, session_id=session_id, runner=runner)
            ),
            resume_existing_run=lambda run_id: self._resume_existing_run(run_id),
            complete_pending_user_questions=(
                lambda run_id, reason: self._complete_pending_user_questions(
                    run_id=run_id,
                    reason=reason,
                )
            ),
            emit_notification=(
                lambda notification_type, session_id, run_id, trace_id, title, body: (
                    self._emit_notification(
                        notification_type=notification_type,
                        session_id=session_id,
                        run_id=run_id,
                        trace_id=trace_id,
                        title=title,
                        body=body,
                    )
                )
            ),
        )

    def replace_runtime_dependencies(
        self,
        *,
        role_registry: RoleRegistry | None,
        provider_factory: Callable[[RoleDefinition, str | None], LLMProvider],
        runtime_role_resolver: RuntimeRoleResolver | None,
    ) -> None:
        self._role_registry = role_registry
        self._provider_factory = provider_factory
        self._runtime_role_resolver = runtime_role_resolver

    def bind_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._event_loop = loop

    @property
    def bound_event_loop(self) -> asyncio.AbstractEventLoop | None:
        return self._event_loop

    async def _run_result_through_stop_hooks(
        self,
        *,
        run_id: str,
        session_id: str,
        result: RunResult,
    ) -> RunResult:
        current_result = self._terminal_results.normalize_terminal_run_result(result)
        if current_result.completion_reason == RunCompletionReason.ASSISTANT_ERROR:
            await self._hook_pipeline.execute_stop_failure_hooks(
                run_id=run_id,
                session_id=session_id,
                completion_reason=current_result.completion_reason.value,
                error_code=current_result.error_code or "assistant_error",
                error_message=(
                    current_result.error_message or current_result.output_text
                ),
                root_task_id=current_result.root_task_id,
            )
            return current_result
        while (
            current_result.completion_reason == RunCompletionReason.ASSISTANT_RESPONSE
        ):
            should_retry = await self._hook_pipeline.execute_stop_hooks(
                run_id=run_id,
                session_id=session_id,
                completion_reason=current_result.completion_reason.value,
                output_text=current_result.output_text,
                root_task_id=current_result.root_task_id,
            )
            if not should_retry:
                return current_result
            current_result = self._terminal_results.normalize_terminal_run_result(
                await self._recovery_service.run_with_auto_recovery(
                    run_id=run_id,
                    session_id=session_id,
                    runner=lambda: self._resume_existing_run(run_id),
                )
            )
            if current_result.completion_reason == RunCompletionReason.ASSISTANT_ERROR:
                await self._hook_pipeline.execute_stop_failure_hooks(
                    run_id=run_id,
                    session_id=session_id,
                    completion_reason=current_result.completion_reason.value,
                    error_code=current_result.error_code or "assistant_error",
                    error_message=(
                        current_result.error_message or current_result.output_text
                    ),
                    root_task_id=current_result.root_task_id,
                )
                return current_result
        return current_result

    def _ensure_session(self, session_id: str) -> str:
        _ = self._session_repo.get(session_id)
        return session_id

    def _prepare_intent(self, intent: IntentInput) -> IntentInput:
        session = self._session_repo.get(intent.session_id)
        target_role_id = str(intent.target_role_id or "").strip() or None
        skills = tuple(str(skill or "").strip() for skill in (intent.skills or ()))
        skills = tuple(skill for skill in skills if skill) or None
        if self._orchestration_settings_service is None:
            return intent.model_copy(
                update={
                    "session_mode": session.session_mode,
                    "target_role_id": target_role_id,
                    "skills": skills,
                }
            )
        topology = self._orchestration_settings_service.resolve_run_topology(session)
        return intent.model_copy(
            update={
                "session_mode": session.session_mode,
                "target_role_id": target_role_id,
                "skills": skills,
                "topology": topology,
            }
        )

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
        intent = self._prepare_intent(intent)
        self._run_control_manager.assert_session_allows_main_input(session_id)
        _ = self._session_repo.mark_started(session_id)
        run_id = new_trace_id().value
        if self._hook_service is not None:
            self._hook_service.snapshot_run(run_id)
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
                await self._hook_pipeline.execute_session_start_hooks(
                    run_id=run_id,
                    session_id=session_id,
                    intent=intent,
                )
                result = await self._run_result_through_stop_hooks(
                    run_id=run_id,
                    session_id=session_id,
                    result=(
                        await self._media_executor.run_media_generation(
                            run_id=run_id,
                            intent=intent,
                        )
                        if intent.run_kind != RunKind.CONVERSATION
                        else await self._recovery_service.run_with_auto_recovery(
                            run_id=run_id,
                            session_id=session_id,
                            runner=lambda: self._meta_agent.handle_intent(
                                intent, trace_id=run_id
                            ),
                        )
                    ),
                )
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
                await self._hook_pipeline.execute_session_end_hooks(
                    run_id=run_id,
                    session_id=session_id,
                    status=result.status,
                    completion_reason=result.completion_reason.value,
                    output_text=result.output_text,
                    root_task_id=result.root_task_id,
                )
                return result
            except Exception as exc:
                if isinstance(exc, RecoverableRunPauseError):
                    payload = exc.payload
                    if self._run_runtime_repo is not None:
                        self._run_runtime_repo.update(
                            run_id,
                            root_task_id=payload.task_id,
                            status=RunRuntimeStatus.PAUSED,
                            phase=RunRuntimePhase.AWAITING_RECOVERY,
                            active_instance_id=payload.instance_id,
                            active_task_id=payload.task_id,
                            active_role_id=payload.role_id,
                            active_subagent_instance_id=None,
                            last_error=payload.error_message,
                        )
                    raise
                result = await self._run_result_through_stop_hooks(
                    run_id=run_id,
                    session_id=session_id,
                    result=self._terminal_results.build_completed_error_run_result(
                        run_id=run_id,
                        session_id=session_id,
                        error_code="run_start_failed",
                        error_message=str(exc),
                    ),
                )
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
                        last_error=result.error_message,
                    )
                await self._hook_pipeline.execute_session_end_hooks(
                    run_id=run_id,
                    session_id=session_id,
                    status=result.status,
                    completion_reason=result.completion_reason.value,
                    output_text=result.output_text,
                    root_task_id=result.root_task_id,
                )
                return result
            finally:
                self._safe_finalize_run(run_id=run_id, session_id=session_id)

    def create_run(
        self,
        intent: IntentInput,
        *,
        source: InjectionSource = InjectionSource.USER,
    ) -> tuple[str, str]:
        return self._scheduler.create_run(intent, source=source)

    def create_detached_run(self, intent: IntentInput) -> tuple[str, str]:
        return self._scheduler.create_detached_run(intent)

    def _create_run_local(
        self,
        intent: IntentInput,
        *,
        allow_active_run_attach: bool,
        source: InjectionSource,
    ) -> tuple[str, str]:
        return self._scheduler.create_run_local(
            intent,
            allow_active_run_attach=allow_active_run_attach,
            source=source,
        )

    def _queue_new_run(
        self,
        *,
        session_id: str,
        intent: IntentInput,
    ) -> tuple[str, str]:
        return self._scheduler.queue_new_run(session_id=session_id, intent=intent)

    def ensure_run_started(self, run_id: str) -> None:
        self._scheduler.ensure_run_started(run_id)

    def _ensure_run_started_local(self, run_id: str) -> None:
        self._scheduler.ensure_run_started_local(run_id)

    def _start_new_run_worker(self, run_id: str) -> None:
        self._scheduler.start_new_run_worker(run_id)

    def _start_resume_worker(self, run_id: str) -> None:
        self._scheduler.start_resume_worker(run_id)

    async def _resume_existing_run(self, run_id: str) -> RunResult:
        try:
            _ = self._root_task_for_run(run_id)
        except KeyError:
            if self._run_intent_repo is None:
                raise
            runtime_repo = self._run_runtime_repo
            runtime = runtime_repo.get(run_id) if runtime_repo is not None else None
            intent = self._run_intent_repo.get(
                run_id,
                fallback_session_id=runtime.session_id if runtime is not None else None,
            )
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
            runtime_intent = None
            if self._run_intent_repo is not None:
                try:
                    runtime_intent = self._run_intent_repo.get(
                        run_id,
                        fallback_session_id=session_id,
                    )
                except KeyError:
                    runtime_intent = None
            if runtime_intent is not None:
                await self._hook_pipeline.execute_session_start_hooks(
                    run_id=run_id,
                    session_id=session_id,
                    intent=runtime_intent,
                )
            result = await self._run_result_through_stop_hooks(
                run_id=run_id,
                session_id=session_id,
                result=await self._recovery_service.run_with_auto_recovery(
                    run_id=run_id,
                    session_id=session_id,
                    runner=runner,
                ),
            )
            completion_reason = result.completion_reason
            failed = result.status == "failed"
            terminal_status = (
                RunRuntimeStatus.FAILED if failed else RunRuntimeStatus.COMPLETED
            )
            terminal_event_type = (
                RunEventType.RUN_FAILED if failed else RunEventType.RUN_COMPLETED
            )
            terminal_log_event = "run.failed" if failed else "run.completed"
            terminal_log_level = logging.ERROR if failed else logging.INFO
            notification_type = (
                NotificationType.RUN_FAILED
                if failed
                else NotificationType.RUN_COMPLETED
            )
            notification_title = "Run Failed" if failed else "Run Completed"
            output_text = result.output_text or str(result.error_message or "").strip()
            notification_body = (
                output_text
                if output_text
                else (
                    f"Run {run_id} failed."
                    if failed
                    else f"Run {run_id} completed successfully."
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
                last_error=((result.error_message or output_text) if failed else None),
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
                    message="Run failed" if failed else "Run completed",
                    payload={
                        "root_task_id": result.root_task_id,
                        "status": result.status,
                        "completion_reason": completion_reason.value,
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
            await self._hook_pipeline.execute_session_end_hooks(
                run_id=run_id,
                session_id=session_id,
                status=result.status,
                completion_reason=completion_reason.value,
                output_text=output_text,
                root_task_id=result.root_task_id,
            )
        except RecoverableRunPauseError as exc:
            payload = exc.payload
            paused_payload = self._recovery_service.build_run_paused_payload(payload)
            self._safe_runtime_update(
                run_id,
                root_task_id=payload.task_id,
                status=RunRuntimeStatus.PAUSED,
                phase=RunRuntimePhase.AWAITING_RECOVERY,
                active_instance_id=payload.instance_id,
                active_task_id=payload.task_id,
                active_role_id=payload.role_id,
                active_subagent_instance_id=None,
                last_error=payload.error_message,
            )
            self._safe_publish_run_event(
                RunEvent(
                    session_id=session_id,
                    run_id=run_id,
                    trace_id=payload.trace_id,
                    task_id=payload.task_id,
                    instance_id=payload.instance_id,
                    role_id=payload.role_id,
                    event_type=RunEventType.RUN_PAUSED,
                    payload_json=dumps(paused_payload),
                ),
                failure_event="run.event.publish_failed",
            )
            with bind_trace_context(
                trace_id=run_id, run_id=run_id, session_id=session_id
            ):
                log_event(
                    logger,
                    logging.WARNING,
                    event="run.paused",
                    message="Run paused awaiting recovery",
                    payload=paused_payload,
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
            await self._hook_pipeline.execute_session_end_hooks(
                run_id=run_id,
                session_id=session_id,
                status="stopped",
                completion_reason="stopped_by_user",
                output_text="",
            )
        except Exception as exc:
            result = await self._run_result_through_stop_hooks(
                run_id=run_id,
                session_id=session_id,
                result=self._terminal_results.build_completed_error_run_result(
                    run_id=run_id,
                    session_id=session_id,
                    error_code="run_worker_failed",
                    error_message=str(exc),
                ),
            )
            failed = result.status == "failed"
            output_text = result.output_text or str(result.error_message or "").strip()
            self._safe_runtime_update(
                run_id,
                root_task_id=result.root_task_id,
                status=RunRuntimeStatus.FAILED
                if failed
                else RunRuntimeStatus.COMPLETED,
                phase=RunRuntimePhase.TERMINAL,
                active_instance_id=None,
                active_task_id=None,
                active_role_id=None,
                active_subagent_instance_id=None,
                last_error=((result.error_message or output_text) if failed else None),
            )
            self._safe_publish_run_event(
                RunEvent(
                    session_id=session_id,
                    run_id=run_id,
                    trace_id=result.trace_id,
                    task_id=result.root_task_id,
                    event_type=(
                        RunEventType.RUN_FAILED
                        if failed
                        else RunEventType.RUN_COMPLETED
                    ),
                    payload_json=dumps(result.model_dump()),
                ),
                failure_event="run.event.publish_failed",
            )
            with bind_trace_context(
                trace_id=run_id, run_id=run_id, session_id=session_id
            ):
                log_event(
                    logger,
                    logging.ERROR if failed else logging.INFO,
                    event="run.failed" if failed else "run.completed",
                    message="Run failed" if failed else "Run completed",
                    exc_info=exc,
                    payload={
                        "root_task_id": result.root_task_id,
                        "status": result.status,
                        "completion_reason": result.completion_reason.value,
                    },
                )
            self._emit_notification(
                notification_type=(
                    NotificationType.RUN_FAILED
                    if failed
                    else NotificationType.RUN_COMPLETED
                ),
                session_id=session_id,
                run_id=run_id,
                trace_id=result.trace_id,
                title="Run Failed" if failed else "Run Completed",
                body=(
                    output_text
                    if output_text
                    else (f"Run {run_id} failed." if failed else "")
                ),
            )
        finally:
            if self._background_task_manager is not None:
                try:
                    await self._background_task_manager.stop_all_for_run(
                        run_id=run_id,
                        reason="run_finalized",
                        execution_mode="foreground",
                    )
                except Exception as exc:
                    with bind_trace_context(
                        trace_id=run_id,
                        run_id=run_id,
                        session_id=session_id,
                    ):
                        log_event(
                            logger,
                            logging.ERROR,
                            event="background_task.cleanup_failed",
                            message="Failed to clean up background tasks",
                            exc_info=exc,
                        )
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
        if self._hook_service is not None:
            self._hook_service.clear_run(run_id)
        self._recovery_service.clear_attempts(run_id)
        if self._runtime_role_resolver is not None:
            self._runtime_role_resolver.cleanup_run(run_id=run_id)
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

    async def stream_run_events(self, run_id: str, after_event_id: int = 0):
        queue = self._run_event_hub.subscribe(run_id)
        terminal_reached = False
        try:
            replay_high_watermark = 0
            if after_event_id >= 0 and self._event_log is not None:
                for row in self._event_log.list_by_trace_after_id(
                    run_id, after_event_id
                ):
                    row_id = row.get("id")
                    if not isinstance(row_id, int):
                        continue
                    try:
                        event_type = RunEventType(str(row["event_type"]))
                    except ValueError:
                        continue
                    replay_event = RunEvent(
                        session_id=str(row["session_id"]),
                        run_id=str(row["trace_id"]),
                        trace_id=str(row["trace_id"]),
                        task_id=(
                            str(row["task_id"]) if row["task_id"] is not None else None
                        ),
                        instance_id=(
                            str(row["instance_id"])
                            if row["instance_id"] is not None
                            else None
                        ),
                        event_type=event_type,
                        payload_json=str(row["payload_json"]),
                        event_id=row_id,
                    )
                    replay_high_watermark = max(replay_high_watermark, row_id)
                    yield replay_event
                    if event_type in (
                        RunEventType.RUN_PAUSED,
                        RunEventType.RUN_COMPLETED,
                        RunEventType.RUN_FAILED,
                        RunEventType.RUN_STOPPED,
                    ):
                        terminal_reached = True
                        return

            while True:
                event = await queue.get()
                if (
                    replay_high_watermark > 0
                    and event.event_id is not None
                    and event.event_id <= replay_high_watermark
                ):
                    continue
                yield event
                if event.event_type in (
                    RunEventType.RUN_PAUSED,
                    RunEventType.RUN_COMPLETED,
                    RunEventType.RUN_FAILED,
                    RunEventType.RUN_STOPPED,
                ):
                    terminal_reached = True
                    break
        finally:
            self._run_event_hub.unsubscribe(run_id, queue)
            if terminal_reached:
                self._run_event_hub.unsubscribe_all(run_id)

    async def run_intent_stream(self, intent: IntentInput):
        run_id, _ = self.create_run(intent)
        self.ensure_run_started(run_id)
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

    def handle_background_task_completion(
        self,
        *,
        record: "BackgroundTaskRecord",
        message: str,
    ) -> None:
        if self._should_delegate_to_bound_loop():
            record_copy = record.model_copy(deep=True)
            self._call_in_bound_loop(
                lambda: self._handle_background_task_completion_local(
                    record=record_copy,
                    message=message,
                )
            )
            return
        self._handle_background_task_completion_local(record=record, message=message)

    def handle_monitor_trigger(
        self,
        *,
        subscription: MonitorSubscriptionRecord,
        envelope: MonitorEventEnvelope,
        message: str,
    ) -> None:
        if self._should_delegate_to_bound_loop():
            subscription_copy = subscription.model_copy(deep=True)
            envelope_copy = envelope.model_copy(deep=True)
            self._call_in_bound_loop(
                lambda: self._handle_monitor_trigger_local(
                    subscription=subscription_copy,
                    envelope=envelope_copy,
                    message=message,
                )
            )
            return
        self._handle_monitor_trigger_local(
            subscription=subscription,
            envelope=envelope,
            message=message,
        )

    def stop_run(self, run_id: str) -> None:
        self._scheduler.stop_run(run_id)

    def _stop_run_local(self, run_id: str) -> None:
        self._scheduler.stop_run_local(run_id)

    def _handle_background_task_completion_local(
        self,
        *,
        record: "BackgroundTaskRecord",
        message: str,
    ) -> None:
        self._followup_router.handle_background_task_completion(
            record=record,
            message=message,
        )

    def _handle_monitor_trigger_local(
        self,
        *,
        subscription: MonitorSubscriptionRecord,
        envelope: MonitorEventEnvelope,
        message: str,
    ) -> None:
        self._followup_router.handle_monitor_trigger(
            subscription=subscription,
            envelope=envelope,
            message=message,
        )

    def _should_delegate_to_bound_loop(self) -> bool:
        loop = self._event_loop
        if loop is None:
            return False
        try:
            return asyncio.get_running_loop() is not loop
        except RuntimeError:
            return True

    def _call_in_bound_loop(self, callback: Callable[[], _T]) -> _T:
        loop = self._event_loop
        if loop is None:
            return callback()
        result: ThreadFuture[_T] = ThreadFuture()

        def runner() -> None:
            try:
                result.set_result(callback())
            except Exception as exc:
                result.set_exception(exc)

        loop.call_soon_threadsafe(runner)
        return result.result(timeout=30)

    def resume_run(self, run_id: str) -> str:
        return self._scheduler.resume_run(run_id)

    def stop_subagent(self, run_id: str, instance_id: str) -> dict[str, str]:
        return self._interaction_service.stop_subagent(run_id, instance_id)

    def _complete_pending_user_questions(
        self,
        *,
        run_id: str,
        instance_id: str | None = None,
        reason: str,
    ) -> None:
        self._interaction_service.complete_pending_user_questions(
            run_id=run_id,
            instance_id=instance_id,
            reason=reason,
        )

    def create_monitor(
        self,
        *,
        run_id: str,
        source_kind: MonitorSourceKind,
        source_key: str,
        rule: MonitorRule,
        action_type: MonitorActionType,
        created_by_instance_id: str | None = None,
        created_by_role_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, object]:
        return self._auxiliary_service.create_monitor(
            run_id=run_id,
            source_kind=source_kind,
            source_key=source_key,
            rule=rule,
            action_type=action_type,
            created_by_instance_id=created_by_instance_id,
            created_by_role_id=created_by_role_id,
            tool_call_id=tool_call_id,
        )

    def list_monitors(self, run_id: str) -> tuple[dict[str, object], ...]:
        return self._auxiliary_service.list_monitors(run_id)

    def stop_monitor(
        self,
        *,
        run_id: str,
        monitor_id: str,
    ) -> dict[str, object]:
        return self._auxiliary_service.stop_monitor(
            run_id=run_id,
            monitor_id=monitor_id,
        )

    def list_background_tasks(self, run_id: str) -> tuple[dict[str, object], ...]:
        return self._auxiliary_service.list_background_tasks(run_id)

    def get_background_task(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> dict[str, object]:
        return self._auxiliary_service.get_background_task(
            run_id=run_id,
            background_task_id=background_task_id,
        )

    def get_todo(self, run_id: str) -> dict[str, object]:
        return self._auxiliary_service.get_todo(run_id)

    async def stop_background_task(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> dict[str, object]:
        return await self._auxiliary_service.stop_background_task(
            run_id=run_id,
            background_task_id=background_task_id,
        )

    def _run_session_id(self, run_id: str) -> str:
        runtime = self._runtime_for_run(run_id)
        if runtime is not None:
            return runtime.session_id
        if self._run_intent_repo is not None:
            try:
                return self._run_intent_repo.get(run_id).session_id
            except KeyError:
                pass
        raise KeyError(f"Run {run_id} not found")

    def inject_subagent_message(
        self,
        *,
        run_id: str,
        instance_id: str,
        content: str,
    ) -> None:
        self._interaction_service.inject_subagent_message(
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
        self._interaction_service.resolve_tool_approval(
            run_id,
            tool_call_id,
            action,
            feedback,
        )

    def _persist_shell_approval_grants(
        self,
        *,
        ticket: ApprovalTicketRecord | None,
        action: str,
    ) -> None:
        self._interaction_service.persist_shell_approval_grants(
            ticket=ticket,
            action=action,
        )

    def list_open_tool_approvals(self, run_id: str) -> list[dict[str, str]]:
        return self._interaction_service.list_open_tool_approvals(run_id)

    def list_user_questions(self, run_id: str) -> list[dict[str, JsonValue]]:
        return self._interaction_service.list_user_questions(run_id)

    def answer_user_question(
        self,
        *,
        run_id: str,
        question_id: str,
        answers: UserQuestionAnswerSubmission,
    ) -> dict[str, JsonValue]:
        return self._interaction_service.answer_user_question(
            run_id=run_id,
            question_id=question_id,
            answers=answers,
        )

    @staticmethod
    def _merge_intent(current: str, followup: str) -> str:
        return f"{current}\n\n{followup}" if current.strip() else followup

    def _assert_auto_attach_allowed(
        self, run_id: str, runtime: RunRuntimeRecord | None
    ) -> None:
        self._followup_router.assert_auto_attach_allowed(run_id, runtime)

    def _root_task_for_run(self, run_id: str) -> TaskRecord:
        return self._followup_router.root_task_for_run(run_id)

    def _has_running_agents_for_run(self, run_id: str) -> bool:
        return self._followup_router.has_running_agents_for_run(run_id)

    def _has_pending_resolvable_question_for_session(self, session_id: str) -> bool:
        return self._followup_router.has_pending_resolvable_question_for_session(
            session_id
        )

    def _append_followup_to_instance(
        self,
        *,
        run_id: str,
        instance_id: str,
        task_id: str,
        content: str,
        enqueue: bool,
        source: InjectionSource,
    ) -> bool:
        return self._followup_router.append_followup_to_instance(
            run_id=run_id,
            instance_id=instance_id,
            task_id=task_id,
            content=content,
            enqueue=enqueue,
            source=source,
        )

    def _append_followup_to_coordinator(
        self,
        run_id: str,
        content: str,
        *,
        enqueue: bool,
        source: InjectionSource = InjectionSource.USER,
    ) -> bool:
        return self._followup_router.append_followup_to_coordinator(
            run_id,
            content,
            enqueue=enqueue,
            source=source,
        )

    def _run_accepts_followups(self, run_id: str, next_intent: IntentInput) -> bool:
        if next_intent.run_kind != RunKind.CONVERSATION:
            return False
        current_intent = self._pending_runs.get(run_id)
        if current_intent is None and self._run_intent_repo is not None:
            try:
                runtime_repo = self._run_runtime_repo
                runtime = runtime_repo.get(run_id) if runtime_repo is not None else None
                current_intent = self._run_intent_repo.get(
                    run_id,
                    fallback_session_id=(
                        runtime.session_id if runtime is not None else None
                    ),
                )
            except KeyError:
                current_intent = None
        if current_intent is None:
            return True
        return current_intent.run_kind == RunKind.CONVERSATION

    def _require_task_repo(self) -> TaskRepository:
        if self._task_repo is None:
            raise RuntimeError("SessionRunService requires task_repo for recovery")
        return self._task_repo

    def _require_message_repo(self) -> MessageRepository:
        if self._message_repo is None:
            raise RuntimeError("SessionRunService requires message_repo for recovery")
        return self._message_repo

    def _require_agent_repo(self) -> AgentInstanceRepository:
        if self._agent_repo is None:
            raise RuntimeError("SessionRunService requires agent_repo for recovery")
        return self._agent_repo

    def _require_user_question_repo(self) -> UserQuestionRepository:
        if self._user_question_repo is None:
            raise RuntimeError("SessionRunService requires user_question_repo")
        return self._user_question_repo

    def _require_media_asset_service(self) -> MediaAssetService:
        if self._media_asset_service is None:
            raise RuntimeError(
                "SessionRunService requires media_asset_service for media runs"
            )
        return self._media_asset_service

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
        self._event_publisher.emit_notification(
            notification_type=notification_type,
            session_id=session_id,
            run_id=run_id,
            trace_id=trace_id,
            title=title,
            body=body,
        )

    def _safe_runtime_update(self, run_id: str, **changes: object) -> None:
        self._event_publisher.safe_runtime_update(run_id, **changes)

    def _safe_publish_run_event(
        self,
        event: RunEvent,
        *,
        failure_event: str,
    ) -> None:
        self._event_publisher.safe_publish_run_event(
            event,
            failure_event=failure_event,
        )
