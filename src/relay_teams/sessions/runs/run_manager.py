# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future as ThreadFuture
from enum import StrEnum
from json import dumps, loads
from typing import TYPE_CHECKING, Awaitable, Callable, Coroutine, TypeVar, cast

from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai.messages import (
    BinaryContent,
    FilePart,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from relay_teams.agents.orchestration.meta_agent import MetaAgent
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.models import create_subagent_instance
from relay_teams.agents.instances.models import AgentRuntimeRecord
from relay_teams.logger import get_logger, log_event
from relay_teams.media import MediaAssetService, MediaRefContentPart, TextContentPart
from relay_teams.media import content_parts_from_text
from relay_teams.media import content_parts_to_text
from relay_teams.monitors import (
    MonitorAction,
    MonitorActionType,
    MonitorEventEnvelope,
    MonitorRule,
    MonitorService,
    MonitorSourceKind,
    MonitorSubscriptionRecord,
)
from relay_teams.notifications import (
    NotificationContext,
    NotificationService,
    NotificationType,
)
from relay_teams.agents.orchestration.settings_service import (
    OrchestrationSettingsService,
)
from relay_teams.providers.provider_contracts import (
    EchoProvider,
    LLMProvider,
    LLMRequest,
)
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.sessions.runs.active_run_registry import ActiveSessionRunRegistry
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.assistant_errors import (
    RunCompletionReason,
    build_assistant_error_message,
    build_assistant_error_response,
    build_auto_recovery_prompt,
)
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.ids import new_trace_id
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.background_tasks.manager import (
    BackgroundTaskManager,
)
from relay_teams.sessions.runs.background_tasks.models import BackgroundTaskRecord
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
    ApprovalTicketStatus,
    ApprovalTicketStatusConflictError,
)
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.recoverable_pause import RecoverableRunPauseError
from relay_teams.sessions.runs.recoverable_pause import RecoverableRunPausePayload
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.user_question_manager import (
    UserQuestionClosedError,
    UserQuestionManager,
)
from relay_teams.sessions.runs.user_question_models import (
    NONE_OF_THE_ABOVE_OPTION_LABEL,
    UserQuestionAnswer,
    UserQuestionAnswerSubmission,
    UserQuestionPrompt,
    UserQuestionSelection,
    UserQuestionRequestStatus,
)
from relay_teams.sessions.runs.user_question_repository import (
    UserQuestionRepository,
    UserQuestionStatusConflictError,
)
from relay_teams.sessions.runs.run_state_repo import RunStateRepository
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.tools.runtime import ToolApprovalAction, ToolApprovalManager
from relay_teams.tools.workspace_tools.shell_approval_repo import (
    ShellApprovalRepository,
    ShellApprovalScope,
)
from relay_teams.tools.workspace_tools.shell_policy import ShellRuntimeFamily
from relay_teams.trace import bind_trace_context
from relay_teams.agents.tasks.models import TaskRecord
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.ids import new_task_id
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from relay_teams.workspace import build_conversation_id
from relay_teams.hooks import (
    HookDecisionBundle,
    HookDecisionType,
    HookEventName,
    HookService,
    SessionEndInput,
    SessionStartInput,
    StopFailureInput,
    StopInput,
    TaskCreatedInput,
    TaskCompletedInput,
)

if TYPE_CHECKING:
    from relay_teams.sessions.runs.background_tasks import BackgroundTaskService
    from relay_teams.sessions.runs.todo_service import TodoService

logger = get_logger(__name__)
_T = TypeVar("_T")


def _approval_action_is_approved(action: str) -> bool:
    return action in {"approve", "approve_once", "approve_exact", "approve_prefix"}


def _approval_action_requires_shell_grant(action: str) -> bool:
    return action in {"approve_exact", "approve_prefix"}


def _normalize_shell_prefix_candidates(raw_value: object) -> tuple[str, ...]:
    if not isinstance(raw_value, list):
        return ()
    normalized: list[str] = []
    for item in raw_value:
        if not isinstance(item, str):
            continue
        candidate = item.strip()
        if candidate:
            normalized.append(candidate)
    return tuple(normalized)


def _extract_shell_grant_metadata(
    ticket: ApprovalTicketRecord,
) -> tuple[str, ShellRuntimeFamily, str, tuple[str, ...]] | None:
    if ticket.tool_name != "shell":
        return None
    metadata = ticket.metadata
    workspace_key = str(metadata.get("workspace_key") or "").strip()
    runtime_family = str(metadata.get("runtime_family") or "").strip()
    normalized_command = str(metadata.get("normalized_command") or "").strip()
    prefix_candidates = _normalize_shell_prefix_candidates(
        metadata.get("prefix_candidates")
    )
    if not workspace_key or not runtime_family:
        return None
    try:
        resolved_runtime_family = ShellRuntimeFamily(runtime_family)
    except ValueError:
        return None
    return workspace_key, resolved_runtime_family, normalized_command, prefix_candidates


def _validate_user_question_answers(
    *,
    questions: tuple[UserQuestionPrompt, ...],
    answers: UserQuestionAnswerSubmission,
) -> UserQuestionAnswerSubmission:
    if len(questions) != len(answers.answers):
        raise ValueError("answers length must match the number of requested questions")
    validated_answers: list[UserQuestionAnswer] = []
    for index, (question, answer) in enumerate(
        zip(questions, answers.answers, strict=True)
    ):
        allowed_labels = {option.label for option in question.options}
        selections = tuple(
            UserQuestionSelection(
                label=selection.label.strip(),
                supplement=str(selection.supplement or "").strip() or None,
            )
            for selection in answer.selections
            if selection.label.strip()
        )
        labels = tuple(selection.label for selection in selections)
        if not question.multiple and len(labels) > 1:
            raise ValueError(f"Question {index + 1} does not allow multiple choices")
        invalid = [label for label in labels if label not in allowed_labels]
        if invalid:
            joined = ", ".join(invalid)
            raise ValueError(f"Question {index + 1} has unknown options: {joined}")
        if NONE_OF_THE_ABOVE_OPTION_LABEL in labels and len(labels) > 1:
            raise ValueError(
                f"Question {index + 1} cannot combine None of the above with other options"
            )
        validated_answers.append(
            UserQuestionAnswer(
                selections=selections,
            )
        )
    return UserQuestionAnswerSubmission(answers=tuple(validated_answers))


def _user_question_status_conflict_message(
    *,
    question_id: str,
    status: UserQuestionRequestStatus,
) -> str:
    if status == UserQuestionRequestStatus.ANSWERED:
        return f"User question {question_id} was already answered"
    if status == UserQuestionRequestStatus.TIMED_OUT:
        return f"User question {question_id} has timed out"
    if status == UserQuestionRequestStatus.COMPLETED:
        return f"User question {question_id} was already completed"
    return f"User question {question_id} is not pending"


def _approval_ticket_status_conflict_message(
    *,
    tool_call_id: str,
    status: ApprovalTicketStatus,
) -> str:
    if status == ApprovalTicketStatus.APPROVED:
        return f"Tool approval {tool_call_id} was already approved"
    if status == ApprovalTicketStatus.DENIED:
        return f"Tool approval {tool_call_id} was already denied"
    if status == ApprovalTicketStatus.TIMED_OUT:
        return f"Tool approval {tool_call_id} has timed out"
    if status == ApprovalTicketStatus.COMPLETED:
        return f"Tool approval {tool_call_id} was already completed"
    return f"Tool approval {tool_call_id} is not pending"


def _is_run_already_running_conflict(*, run_id: str, error: RuntimeError) -> bool:
    return str(error) == f"Run {run_id} is already running"


class AutoRecoveryReason(StrEnum):
    INVALID_TOOL_ARGS_JSON = "auto_recovery_invalid_tool_args_json"
    NETWORK_STREAM_INTERRUPTED = "auto_recovery_network_stream_interrupted"
    NETWORK_TIMEOUT = "auto_recovery_network_timeout"
    NETWORK_ERROR = "auto_recovery_network_error"


class AutoRecoveryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error_code: str
    reason: AutoRecoveryReason
    max_attempts: int
    prompt: str


def _auto_recovery_policy(
    *,
    error_code: str,
    reason: AutoRecoveryReason,
    max_attempts: int,
) -> AutoRecoveryPolicy:
    prompt = build_auto_recovery_prompt(error_code)
    if prompt is None:
        raise ValueError(f"Missing auto recovery prompt for error_code={error_code}")
    return AutoRecoveryPolicy(
        error_code=error_code,
        reason=reason,
        max_attempts=max_attempts,
        prompt=prompt,
    )


AUTO_RECOVERY_POLICIES = (
    _auto_recovery_policy(
        error_code="model_tool_args_invalid_json",
        reason=AutoRecoveryReason.INVALID_TOOL_ARGS_JSON,
        max_attempts=1,
    ),
    _auto_recovery_policy(
        error_code="network_stream_interrupted",
        reason=AutoRecoveryReason.NETWORK_STREAM_INTERRUPTED,
        max_attempts=5,
    ),
    _auto_recovery_policy(
        error_code="network_timeout",
        reason=AutoRecoveryReason.NETWORK_TIMEOUT,
        max_attempts=5,
    ),
    _auto_recovery_policy(
        error_code="network_error",
        reason=AutoRecoveryReason.NETWORK_ERROR,
        max_attempts=1,
    ),
)


class RunManager:
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
        self._pending_runs: dict[str, IntentInput] = {}
        self._running_run_ids: set[str] = set()
        self._resume_requested_runs: set[str] = set()
        self._auto_recovery_attempts: dict[tuple[str, AutoRecoveryReason], int] = {}
        self._event_loop: asyncio.AbstractEventLoop | None = None

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

    def _run_hook_sync(
        self,
        coroutine: Coroutine[object, object, HookDecisionBundle],
    ) -> HookDecisionBundle:
        current_loop: asyncio.AbstractEventLoop | None = None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if (
            self._event_loop is not None
            and self._event_loop.is_running()
            and self._event_loop is not current_loop
        ):
            future = asyncio.run_coroutine_threadsafe(coroutine, self._event_loop)
            return future.result()
        if current_loop is None:
            return asyncio.run(coroutine)
        raise RuntimeError(
            "Cannot synchronously execute runtime hooks on the active event loop thread"
        )

    async def _execute_session_start_hooks(
        self,
        *,
        run_id: str,
        session_id: str,
        intent: IntentInput,
        source: str,
    ) -> None:
        if self._hook_service is None:
            return
        session = self._session_repo.get(session_id)
        agent_type = self._session_start_agent_type(intent)
        model = self._session_start_model(agent_type)
        _ = await self._hook_service.execute(
            event_input=SessionStartInput(
                event_name=HookEventName.SESSION_START,
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                workspace_id=session.workspace_id,
                session_mode=(
                    intent.session_mode.value if intent.session_mode is not None else ""
                ),
                run_kind=intent.run_kind.value,
                source=source,
                model=model,
                agent_type=agent_type,
            ),
            run_event_hub=self._run_event_hub,
        )

    def _session_start_agent_type(self, intent: IntentInput) -> str | None:
        role_hint = str(intent.target_role_id or "").strip()
        if role_hint:
            return role_hint
        if intent.run_kind != RunKind.CONVERSATION:
            try:
                return self._resolve_generation_role_id(intent)
            except Exception:
                return None
        topology = intent.topology
        if topology is not None and topology.normal_root_role_id.strip():
            return topology.normal_root_role_id.strip()
        return None

    def _session_start_model(self, agent_type: str | None) -> str:
        if not agent_type or self._role_registry is None:
            return ""
        try:
            return self._role_registry.get(agent_type).model_profile
        except KeyError:
            return ""

    async def _execute_session_end_hooks(
        self,
        *,
        run_id: str,
        session_id: str,
        status: str,
        completion_reason: str,
        output_text: str,
        root_task_id: str | None = None,
    ) -> None:
        if self._hook_service is None:
            return
        _ = await self._hook_service.execute(
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

    async def _execute_stop_hooks(
        self,
        *,
        run_id: str,
        session_id: str,
        completion_reason: str,
        output_text: str,
        root_task_id: str | None = None,
    ) -> bool:
        if self._hook_service is None:
            return False
        bundle = await self._hook_service.execute(
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

    async def _execute_stop_failure_hooks(
        self,
        *,
        run_id: str,
        session_id: str,
        completion_reason: str,
        error_code: str,
        error_message: str,
        root_task_id: str | None = None,
    ) -> None:
        if self._hook_service is None:
            return
        _ = await self._hook_service.execute(
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

    async def _execute_task_completed_hooks(
        self,
        *,
        run_id: str,
        session_id: str,
        task_id: str,
        instance_id: str | None,
        role_id: str | None,
        status: TaskStatus,
        output_text: str,
        error_message: str,
    ) -> HookDecisionBundle:
        if self._hook_service is None:
            return HookDecisionBundle(decision=HookDecisionType.ALLOW)
        return await self._hook_service.execute(
            event_input=TaskCompletedInput(
                event_name=HookEventName.TASK_COMPLETED,
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=task_id,
                instance_id=instance_id,
                role_id=role_id,
                workspace_id=self._session_repo.get(session_id).workspace_id,
                conversation_id=(
                    build_conversation_id(session_id, role_id)
                    if role_id is not None
                    else ""
                ),
                status=status.value,
                output_text=output_text,
                error_message=error_message,
            ),
            run_event_hub=self._run_event_hub,
        )

    async def _execute_task_created_hooks(
        self,
        *,
        run_id: str,
        session_id: str,
        task: TaskEnvelope,
        instance_id: str | None,
        role_id: str | None,
    ) -> HookDecisionBundle:
        if self._hook_service is None:
            return HookDecisionBundle(decision=HookDecisionType.ALLOW)
        return await self._hook_service.execute(
            event_input=TaskCreatedInput(
                event_name=HookEventName.TASK_CREATED,
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=task.task_id,
                instance_id=instance_id,
                role_id=role_id,
                parent_task_id=task.parent_task_id,
                task_title=task.title or "",
                task_objective=task.objective,
            ),
            run_event_hub=self._run_event_hub,
        )

    async def _run_result_through_stop_hooks(
        self,
        *,
        run_id: str,
        session_id: str,
        result: RunResult,
    ) -> RunResult:
        current_result = self._normalize_terminal_run_result(result)
        if current_result.completion_reason == RunCompletionReason.ASSISTANT_ERROR:
            await self._execute_stop_failure_hooks(
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
            should_retry = await self._execute_stop_hooks(
                run_id=run_id,
                session_id=session_id,
                completion_reason=current_result.completion_reason.value,
                output_text=current_result.output_text,
                root_task_id=current_result.root_task_id,
            )
            if not should_retry:
                return current_result
            current_result = self._normalize_terminal_run_result(
                await self._run_with_auto_recovery(
                    run_id=run_id,
                    session_id=session_id,
                    runner=lambda: self._resume_existing_run(run_id),
                )
            )
            if current_result.completion_reason == RunCompletionReason.ASSISTANT_ERROR:
                await self._execute_stop_failure_hooks(
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
        if self._orchestration_settings_service is None:
            return intent.model_copy(
                update={
                    "session_mode": session.session_mode,
                    "target_role_id": target_role_id,
                }
            )
        topology = self._orchestration_settings_service.resolve_run_topology(session)
        return intent.model_copy(
            update={
                "session_mode": session.session_mode,
                "target_role_id": target_role_id,
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
                await self._execute_session_start_hooks(
                    run_id=run_id,
                    session_id=session_id,
                    intent=intent,
                    source="startup",
                )
                result = await self._run_result_through_stop_hooks(
                    run_id=run_id,
                    session_id=session_id,
                    result=(
                        await self._run_media_generation(run_id=run_id, intent=intent)
                        if intent.run_kind != RunKind.CONVERSATION
                        else await self._run_with_auto_recovery(
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
                await self._execute_session_end_hooks(
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
                    result=self._build_completed_error_run_result(
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
                await self._execute_session_end_hooks(
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
        if self._should_delegate_to_bound_loop():
            delegated_intent = intent.model_copy(deep=True)
            return self._call_in_bound_loop(
                lambda: self._create_run_local(
                    delegated_intent,
                    allow_active_run_attach=True,
                    source=source,
                )
            )
        return self._create_run_local(
            intent,
            allow_active_run_attach=True,
            source=source,
        )

    def create_detached_run(self, intent: IntentInput) -> tuple[str, str]:
        if self._should_delegate_to_bound_loop():
            delegated_intent = intent.model_copy(deep=True)
            return self._call_in_bound_loop(
                lambda: self._create_run_local(
                    delegated_intent,
                    allow_active_run_attach=False,
                    source=InjectionSource.USER,
                )
            )
        return self._create_run_local(
            intent,
            allow_active_run_attach=False,
            source=InjectionSource.USER,
        )

    def _create_run_local(
        self,
        intent: IntentInput,
        *,
        allow_active_run_attach: bool,
        source: InjectionSource,
    ) -> tuple[str, str]:
        session_id = self._ensure_session(intent.session_id)
        intent.session_id = session_id
        intent = self._prepare_intent(intent)
        self._run_control_manager.assert_session_allows_main_input(session_id)
        _ = self._session_repo.mark_started(session_id)

        existing = self._active_recoverable_run(session_id)
        if existing is not None:
            active_run_id, runtime = existing
            if not allow_active_run_attach:
                raise RuntimeError(
                    f"Session {session_id} already has active run {active_run_id}"
                )
            if not self._run_accepts_followups(active_run_id, next_intent=intent):
                raise RuntimeError(
                    f"Run {active_run_id} is active and does not accept follow-up input"
                )
            self._assert_auto_attach_allowed(active_run_id, runtime)
            if (
                active_run_id in self._pending_runs
                and active_run_id not in self._running_run_ids
            ):
                pending = self._pending_runs[active_run_id]
                pending.intent = self._merge_intent(pending.intent, intent.intent)
                pending.yolo = intent.yolo
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
                self._update_run_yolo(
                    run_id=active_run_id,
                    session_id=session_id,
                    yolo=intent.yolo,
                )
                self._append_followup_to_coordinator(
                    active_run_id,
                    intent.intent,
                    enqueue=True,
                    source=source,
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
            if (
                runtime is not None
                and runtime.is_recoverable
                and runtime.status
                in {RunRuntimeStatus.PAUSED, RunRuntimeStatus.STOPPED}
            ):
                self._append_followup_to_coordinator(
                    active_run_id, intent.intent, enqueue=False
                )
                self._update_run_yolo(
                    run_id=active_run_id,
                    session_id=session_id,
                    yolo=intent.yolo,
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

        return self._queue_new_run(
            session_id=session_id,
            intent=intent,
        )

    def _queue_new_run(
        self,
        *,
        session_id: str,
        intent: IntentInput,
    ) -> tuple[str, str]:
        run_id = new_trace_id().value
        if self._hook_service is not None:
            self._hook_service.snapshot_run(run_id)
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
                run_id=run_id,
                session_id=session_id,
                intent=intent,
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
        if self._should_delegate_to_bound_loop():
            self._call_in_bound_loop(lambda: self._ensure_run_started_local(run_id))
            return
        self._ensure_run_started_local(run_id)

    def _ensure_run_started_local(self, run_id: str) -> None:
        if run_id in self._running_run_ids:
            return
        if run_id in self._pending_runs:
            self._start_new_run_worker(run_id)
            return
        if run_id in self._resume_requested_runs:
            runtime = self._runtime_for_run(run_id)
            if runtime is None:
                raise KeyError(f"Run {run_id} not found")
            if runtime.status not in {
                RunRuntimeStatus.QUEUED,
                RunRuntimeStatus.PAUSED,
                RunRuntimeStatus.STOPPED,
            }:
                raise RuntimeError(
                    f"Run {run_id} cannot be resumed from status {runtime.status.value}"
                )
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
        runner = (
            (lambda: self._run_media_generation(run_id=run_id, intent=intent))
            if intent.run_kind != RunKind.CONVERSATION
            else (lambda: self._meta_agent.handle_intent(intent, trace_id=run_id))
        )
        task = asyncio.create_task(
            self._worker(
                run_id=run_id,
                session_id=session_id,
                runner=runner,
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
        resume_payload = self._transition_run_to_resumed(
            run_id=run_id,
            session_id=session_id,
            reason="resume",
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
                payload=resume_payload,
            )

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

    async def _run_with_auto_recovery(
        self,
        *,
        run_id: str,
        session_id: str,
        runner: Callable[[], Awaitable[RunResult]],
    ) -> RunResult:
        async def _resume_runner() -> RunResult:
            return await self._resume_existing_run(run_id)

        current_runner = runner
        while True:
            try:
                return await current_runner()
            except RecoverableRunPauseError as exc:
                policy = self._auto_recovery_policy_for(exc.payload)
                if policy is None:
                    raise
                attempt = self._next_auto_recovery_attempt(
                    exc.payload,
                    policy=policy,
                )
                if attempt is None:
                    raise
                self._record_auto_recovery_attempt(
                    run_id=run_id,
                    reason=policy.reason,
                    attempt=attempt,
                )
                self._queue_auto_recovery_prompt(payload=exc.payload, policy=policy)
                resume_payload = self._transition_run_to_resumed(
                    run_id=run_id,
                    session_id=session_id,
                    reason=policy.reason,
                    attempt=attempt,
                    max_attempts=policy.max_attempts,
                )
                with bind_trace_context(
                    trace_id=run_id,
                    run_id=run_id,
                    session_id=session_id,
                ):
                    log_event(
                        logger,
                        logging.WARNING,
                        event="run.auto_recovery.resumed",
                        message="Run resumed automatically after recoverable LLM failure",
                        payload={
                            **resume_payload,
                            "error_code": exc.payload.error_code,
                        },
                    )
                current_runner = _resume_runner

    async def _run_media_generation(
        self,
        *,
        run_id: str,
        intent: IntentInput,
    ) -> RunResult:
        session = self._session_repo.get(intent.session_id)
        role_id = self._resolve_generation_role_id(intent)
        role_registry = self._role_registry
        if role_registry is None:
            raise RuntimeError("RunManager requires role_registry for media generation")
        role = role_registry.get(role_id)
        provider = self._provider_factory(role, intent.session_id)
        conversation_id = build_conversation_id(intent.session_id, role_id)
        instance = create_subagent_instance(
            role_id,
            workspace_id=session.workspace_id,
            session_id=intent.session_id,
            conversation_id=conversation_id,
        )
        root_task = TaskEnvelope(
            task_id=new_task_id().value,
            session_id=intent.session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id=role_id,
            objective=intent.intent or intent.run_kind.value,
            verification=VerificationPlan(checklist=("generated_media",)),
        )
        agent_repo = self._require_agent_repo()
        task_repo = self._require_task_repo()
        created_bundle = await self._execute_task_created_hooks(
            run_id=run_id,
            session_id=intent.session_id,
            task=root_task,
            instance_id=instance.instance_id,
            role_id=role_id,
        )
        if created_bundle.decision == HookDecisionType.DENY:
            denial_reason = (
                created_bundle.reason or "Task creation denied by runtime hook"
            )
            return self._build_completed_error_run_result(
                run_id=run_id,
                session_id=intent.session_id,
                root_task_id=root_task.task_id,
                instance_id=instance.instance_id,
                role_id=role_id,
                conversation_id=conversation_id,
                workspace_id=session.workspace_id,
                error_code="task_creation_denied",
                error_message=denial_reason,
            )
        agent_repo.upsert_instance(
            run_id=run_id,
            trace_id=run_id,
            session_id=intent.session_id,
            instance_id=instance.instance_id,
            role_id=role_id,
            workspace_id=session.workspace_id,
            conversation_id=conversation_id,
            status=InstanceStatus.RUNNING,
        )
        _ = task_repo.create(root_task)
        task_repo.update_status(
            root_task.task_id,
            TaskStatus.RUNNING,
            assigned_instance_id=instance.instance_id,
        )
        self._safe_runtime_update(
            run_id,
            root_task_id=root_task.task_id,
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.COORDINATOR_RUNNING,
            active_instance_id=instance.instance_id,
            active_task_id=root_task.task_id,
            active_role_id=role_id,
            active_subagent_instance_id=None,
            last_error=None,
        )
        self._safe_publish_run_event(
            RunEvent(
                session_id=intent.session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=root_task.task_id,
                instance_id=instance.instance_id,
                role_id=role_id,
                event_type=RunEventType.MODEL_STEP_STARTED,
                payload_json=dumps(
                    {"role_id": role_id, "instance_id": instance.instance_id}
                ),
            ),
            failure_event="run.event.publish_failed",
        )
        self._publish_generation_progress(
            run_id=run_id,
            session_id=intent.session_id,
            task_id=root_task.task_id,
            instance_id=instance.instance_id,
            role_id=role_id,
            run_kind=intent.run_kind.value,
            phase="started",
            progress=0.0,
            preview_asset_id=None,
        )
        request = LLMRequest(
            run_id=run_id,
            trace_id=run_id,
            task_id=root_task.task_id,
            session_id=intent.session_id,
            workspace_id=session.workspace_id,
            conversation_id=conversation_id,
            instance_id=instance.instance_id,
            role_id=role_id,
            system_prompt="",
            user_prompt=intent.intent or None,
            input=intent.input,
            run_kind=intent.run_kind,
            generation_config=intent.generation_config,
            thinking=intent.thinking,
        )
        try:
            output = await self._execute_native_generation(
                provider=provider,
                request=request,
            )
            if not output:
                raise RuntimeError("Provider returned no media output")
            self._append_media_output_message(
                request=request,
                output=output,
            )
            self._publish_output_delta(
                run_id=run_id,
                session_id=intent.session_id,
                task_id=root_task.task_id,
                instance_id=instance.instance_id,
                role_id=role_id,
                output=output,
            )
            preview_asset_id = next(
                (
                    part.asset_id
                    for part in output
                    if isinstance(part, MediaRefContentPart)
                ),
                None,
            )
            self._publish_generation_progress(
                run_id=run_id,
                session_id=intent.session_id,
                task_id=root_task.task_id,
                instance_id=instance.instance_id,
                role_id=role_id,
                run_kind=intent.run_kind.value,
                phase="completed",
                progress=1.0,
                preview_asset_id=preview_asset_id,
            )
            self._safe_publish_run_event(
                RunEvent(
                    session_id=intent.session_id,
                    run_id=run_id,
                    trace_id=run_id,
                    task_id=root_task.task_id,
                    instance_id=instance.instance_id,
                    role_id=role_id,
                    event_type=RunEventType.MODEL_STEP_FINISHED,
                    payload_json=dumps(
                        {"role_id": role_id, "instance_id": instance.instance_id}
                    ),
                ),
                failure_event="run.event.publish_failed",
            )
            completion_bundle = await self._execute_task_completed_hooks(
                run_id=run_id,
                session_id=intent.session_id,
                task_id=root_task.task_id,
                instance_id=instance.instance_id,
                role_id=role_id,
                status=TaskStatus.COMPLETED,
                output_text=content_parts_to_text(output),
                error_message="",
            )
            if completion_bundle.decision == HookDecisionType.DENY:
                denial_reason = (
                    completion_bundle.reason or "Task completion denied by runtime hook"
                )
                try:
                    result = self._build_completed_error_run_result(
                        run_id=run_id,
                        session_id=intent.session_id,
                        root_task_id=root_task.task_id,
                        instance_id=instance.instance_id,
                        role_id=role_id,
                        conversation_id=conversation_id,
                        workspace_id=session.workspace_id,
                        error_code="task_completion_denied",
                        error_message=denial_reason,
                    )
                    task_repo.update_status(
                        root_task.task_id,
                        TaskStatus.FAILED,
                        assigned_instance_id=instance.instance_id,
                        result=result.output_text,
                        error_message=result.error_message,
                    )
                    agent_repo.mark_status(instance.instance_id, InstanceStatus.FAILED)
                except Exception as exc:
                    log_event(
                        logger,
                        logging.WARNING,
                        event="run.media_generation.completion_deny_finalize_failed",
                        message=(
                            "Task completion denial fell back after result finalization failed"
                        ),
                        payload={"run_id": run_id, "task_id": root_task.task_id},
                        exc_info=exc,
                    )
                    assistant_message = build_assistant_error_message(
                        error_code="task_completion_denied",
                        error_message=denial_reason,
                    )
                    task_repo.update_status(
                        root_task.task_id,
                        TaskStatus.FAILED,
                        assigned_instance_id=instance.instance_id,
                        result=assistant_message,
                        error_message=denial_reason,
                    )
                    agent_repo.mark_status(instance.instance_id, InstanceStatus.FAILED)
                    result = RunResult(
                        trace_id=run_id,
                        root_task_id=root_task.task_id,
                        status="failed",
                        completion_reason=RunCompletionReason.ASSISTANT_ERROR,
                        error_code="task_completion_denied",
                        error_message=denial_reason,
                        output=content_parts_from_text(assistant_message),
                    )
                return result
            task_repo.update_status(
                root_task.task_id,
                TaskStatus.COMPLETED,
                assigned_instance_id=instance.instance_id,
                result=content_parts_to_text(output),
            )
            agent_repo.mark_status(instance.instance_id, InstanceStatus.COMPLETED)
            return RunResult(
                trace_id=run_id,
                root_task_id=root_task.task_id,
                status="completed",
                completion_reason=RunCompletionReason.ASSISTANT_RESPONSE,
                output=output,
            )
        except Exception as exc:
            result = self._build_completed_error_run_result(
                run_id=run_id,
                session_id=intent.session_id,
                root_task_id=root_task.task_id,
                instance_id=instance.instance_id,
                role_id=role_id,
                conversation_id=conversation_id,
                workspace_id=session.workspace_id,
                error_code="native_generation_failed",
                error_message=str(exc),
            )
            task_repo.update_status(
                root_task.task_id,
                TaskStatus.FAILED,
                assigned_instance_id=instance.instance_id,
                result=result.output_text,
                error_message=result.error_message,
            )
            agent_repo.mark_status(instance.instance_id, InstanceStatus.FAILED)
            self._publish_generation_progress(
                run_id=run_id,
                session_id=intent.session_id,
                task_id=root_task.task_id,
                instance_id=instance.instance_id,
                role_id=role_id,
                run_kind=intent.run_kind.value,
                phase="completed",
                progress=1.0,
                preview_asset_id=None,
            )
            return result

    async def _execute_native_generation(
        self,
        *,
        provider: LLMProvider,
        request: LLMRequest,
    ) -> tuple[TextContentPart | MediaRefContentPart, ...]:
        if request.run_kind == RunKind.GENERATE_IMAGE:
            return cast(
                tuple[TextContentPart | MediaRefContentPart, ...],
                await provider.generate_image(request),
            )
        if request.run_kind == RunKind.GENERATE_AUDIO:
            return cast(
                tuple[TextContentPart | MediaRefContentPart, ...],
                await provider.generate_audio(request),
            )
        if request.run_kind == RunKind.GENERATE_VIDEO:
            return cast(
                tuple[TextContentPart | MediaRefContentPart, ...],
                await provider.generate_video(request),
            )
        raise RuntimeError(
            f"Unsupported native generation run kind: {request.run_kind.value}"
        )

    def _resolve_generation_role_id(self, intent: IntentInput) -> str:
        if intent.target_role_id is not None and intent.target_role_id.strip():
            return intent.target_role_id
        if self._role_registry is None:
            raise RuntimeError("RunManager requires role_registry for media generation")
        if intent.topology is not None and intent.topology.normal_root_role_id.strip():
            return intent.topology.normal_root_role_id
        return self._role_registry.get_main_agent_role_id()

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
                await self._execute_session_start_hooks(
                    run_id=run_id,
                    session_id=session_id,
                    intent=runtime_intent,
                    source="resume",
                )
            result = await self._run_result_through_stop_hooks(
                run_id=run_id,
                session_id=session_id,
                result=await self._run_with_auto_recovery(
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
            await self._execute_session_end_hooks(
                run_id=run_id,
                session_id=session_id,
                status=result.status,
                completion_reason=completion_reason.value,
                output_text=output_text,
                root_task_id=result.root_task_id,
            )
        except RecoverableRunPauseError as exc:
            payload = exc.payload
            paused_payload = self._build_run_paused_payload(payload)
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
            await self._execute_session_end_hooks(
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
                result=self._build_completed_error_run_result(
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
        self._auto_recovery_attempts = {
            key: value
            for key, value in self._auto_recovery_attempts.items()
            if key[0] != run_id
        }
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
        if self._should_delegate_to_bound_loop():
            self._call_in_bound_loop(lambda: self._stop_run_local(run_id))
            return
        self._stop_run_local(run_id)

    def _stop_run_local(self, run_id: str) -> None:
        self._run_control_manager.clear_paused_subagent_for_run(run_id)
        if run_id in self._pending_runs and run_id not in self._running_run_ids:
            self._complete_pending_user_questions(
                run_id=run_id,
                reason="run_stopped",
            )
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
        if requested:
            self._complete_pending_user_questions(
                run_id=run_id,
                reason="run_stopped",
            )
        if self._run_runtime_repo is not None and requested:
            runtime = self._run_runtime_repo.get(run_id)
            if runtime is not None:
                self._run_runtime_repo.update(
                    run_id,
                    status=RunRuntimeStatus.STOPPING,
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

    def _handle_background_task_completion_local(
        self,
        *,
        record: "BackgroundTaskRecord",
        message: str,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "background_task_id": record.background_task_id,
        }
        self._route_system_message(
            source_run_id=record.run_id,
            session_id=record.session_id,
            preferred_instance_id=record.instance_id,
            role_id=record.role_id,
            task_id_fallback="background-task-notification",
            message=message,
            allow_coordinator=True,
            event_prefix="background_task.notification",
            payload=payload,
        )

    def _handle_monitor_trigger_local(
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
            self._spawn_system_followup_run(
                source_run_id=subscription.run_id,
                session_id=subscription.session_id,
                message=message,
                event_prefix="monitor.trigger",
                payload=payload,
            )
            return
        self._route_system_message(
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

    def _route_system_message(
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
    ) -> None:
        task_id = self._find_task_for_instance(
            run_id=source_run_id,
            instance_id=preferred_instance_id or "",
        )
        if (
            preferred_instance_id
            and self._can_enqueue_followup_to_instance(
                run_id=source_run_id,
                instance_id=preferred_instance_id,
            )
            and self._append_followup_to_instance(
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
        if allow_coordinator and self._can_enqueue_followup_to_coordinator(
            source_run_id
        ):
            if self._append_followup_to_coordinator(
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
        self._spawn_system_followup_run(
            source_run_id=source_run_id,
            session_id=session_id,
            message=message,
            event_prefix=event_prefix,
            payload=payload,
        )

    def _spawn_system_followup_run(
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
        new_run_id, _ = self.create_run(intent, source=InjectionSource.SYSTEM)
        self.ensure_run_started(new_run_id)
        if active_run_before in {
            None,
            source_run_id,
        } and self._has_active_background_tasks(source_run_id):
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

    def _has_active_background_tasks(self, run_id: str) -> bool:
        records: tuple["BackgroundTaskRecord", ...]
        if self._background_task_service is not None:
            records = self._background_task_service.list_for_run(run_id)
        elif self._background_task_manager is not None:
            records = self._background_task_manager.list_for_run(run_id)
        else:
            return False
        return any(record.is_active for record in records)

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
        if run_id in self._running_run_ids:
            raise RuntimeError(f"Run {run_id} is already running")
        if run_id in self._pending_runs:
            pending = self._pending_runs[run_id]
            if pending.session_id is None:
                raise RuntimeError(f"Run {run_id} is missing session id")
            if run_id in self._resume_requested_runs:
                return pending.session_id
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
        if runtime.status == RunRuntimeStatus.RUNNING:
            raise RuntimeError(f"Run {run_id} is already running")
        if runtime.status == RunRuntimeStatus.STOPPING:
            raise RuntimeError(
                f"Run {run_id} is stopping. Wait for it to stop before resuming."
            )
        if not runtime.is_recoverable:
            raise RuntimeError(f"Run {run_id} is not recoverable")
        if run_id in self._resume_requested_runs:
            return runtime.session_id
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
        stopped = self._run_control_manager.stop_subagent(
            run_id=run_id,
            instance_id=instance_id,
        )
        self._complete_pending_user_questions(
            run_id=run_id,
            instance_id=instance_id,
            reason="subagent_stopped",
        )
        return stopped

    def _complete_pending_user_questions(
        self,
        *,
        run_id: str,
        instance_id: str | None = None,
        reason: str,
    ) -> None:
        repo = self._user_question_repo
        if repo is None:
            return
        records = repo.list_by_run(run_id)
        targets = tuple(
            record
            for record in records
            if instance_id is None or record.instance_id == instance_id
        )
        if not targets:
            if self._user_question_manager is None:
                return
            if instance_id is None:
                self._user_question_manager.mark_questions_closed_for_run(
                    run_id=run_id,
                    reason=reason,
                )
                return
            _ = self._user_question_manager.mark_questions_closed_for_instance(
                run_id=run_id,
                instance_id=instance_id,
                reason=reason,
            )
            return
        for record in targets:
            resolved_record = None
            try:
                resolved_record = repo.resolve(
                    question_id=record.question_id,
                    status=UserQuestionRequestStatus.COMPLETED,
                    answers=record.answers,
                    expected_status=UserQuestionRequestStatus.REQUESTED,
                )
            except UserQuestionStatusConflictError:
                pass
            if resolved_record is not None:
                self._safe_publish_run_event(
                    RunEvent(
                        session_id=resolved_record.session_id,
                        run_id=run_id,
                        trace_id=run_id,
                        task_id=resolved_record.task_id,
                        instance_id=resolved_record.instance_id,
                        role_id=resolved_record.role_id,
                        event_type=RunEventType.USER_QUESTION_ANSWERED,
                        payload_json=dumps(
                            {
                                "question_id": record.question_id,
                                "status": resolved_record.status.value,
                                "instance_id": resolved_record.instance_id,
                                "role_id": resolved_record.role_id,
                            },
                            ensure_ascii=False,
                        ),
                    ),
                    failure_event="run.event.publish_failed",
                )
            if self._user_question_manager is not None:
                self._user_question_manager.mark_question_closed(
                    run_id=run_id,
                    question_id=record.question_id,
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
        service = self._require_monitor_service()
        normalized_source_key = source_key.strip()
        if source_kind == MonitorSourceKind.BACKGROUND_TASK:
            _ = self.get_background_task(
                run_id=run_id,
                background_task_id=normalized_source_key,
            )
        record = service.create_monitor(
            run_id=run_id,
            session_id=self._require_run_session_id(run_id),
            source_kind=source_kind,
            source_key=normalized_source_key,
            rule=rule,
            action=MonitorAction(action_type=action_type),
            created_by_instance_id=created_by_instance_id,
            created_by_role_id=created_by_role_id,
            tool_call_id=tool_call_id,
        )
        return record.model_dump(mode="json")

    def list_monitors(self, run_id: str) -> tuple[dict[str, object], ...]:
        service = self._require_monitor_service()
        return tuple(
            record.model_dump(mode="json") for record in service.list_for_run(run_id)
        )

    def stop_monitor(
        self,
        *,
        run_id: str,
        monitor_id: str,
    ) -> dict[str, object]:
        service = self._require_monitor_service()
        return service.stop_for_run(
            run_id=run_id,
            monitor_id=monitor_id,
        ).model_dump(mode="json")

    def list_background_tasks(self, run_id: str) -> tuple[dict[str, object], ...]:
        if self._background_task_service is not None:
            return tuple(
                record.model_dump(mode="json")
                for record in self._background_task_service.list_for_run(run_id)
            )
        if self._background_task_manager is None:
            return ()
        return tuple(
            record.model_dump(mode="json")
            for record in self._background_task_manager.list_for_run(run_id)
            if record.execution_mode == "background"
        )

    def get_background_task(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> dict[str, object]:
        if self._background_task_service is not None:
            return self._background_task_service.get_for_run(
                run_id=run_id,
                background_task_id=background_task_id,
            ).model_dump(mode="json")
        if self._background_task_manager is None:
            raise KeyError(f"Background task {background_task_id} not found")
        record = self._background_task_manager.get_for_run(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        if record.execution_mode != "background":
            raise KeyError(f"Background task {background_task_id} not found")
        return record.model_dump(mode="json")

    def get_todo(self, run_id: str) -> dict[str, object]:
        if self._todo_service is None:
            raise RuntimeError("Todo service is not configured")
        snapshot = self._todo_service.get_for_run(
            run_id=run_id,
            session_id=self._require_run_session_id(run_id),
        )
        return snapshot.model_dump(mode="json")

    async def stop_background_task(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> dict[str, object]:
        if self._background_task_service is not None:
            record = await self._background_task_service.stop_for_run(
                run_id=run_id,
                background_task_id=background_task_id,
            )
            return record.model_dump(mode="json")
        if self._background_task_manager is None:
            raise KeyError(f"Background task {background_task_id} not found")
        record = self._background_task_manager.get_for_run(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        if record.execution_mode != "background":
            raise KeyError(f"Background task {background_task_id} not found")
        record = await self._background_task_manager.stop_for_run(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        return record.model_dump(mode="json")

    def _require_monitor_service(self) -> MonitorService:
        if self._monitor_service is None:
            raise RuntimeError("Monitor service is not configured")
        return self._monitor_service

    def _require_run_session_id(self, run_id: str) -> str:
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
        if action not in {
            "approve",
            "approve_once",
            "approve_exact",
            "approve_prefix",
            "deny",
        }:
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
        if runtime is not None and runtime.status == RunRuntimeStatus.STOPPING:
            raise RuntimeError(
                f"Run {run_id} is stopping. Wait for it to stop before resolving tool approval."
            )
        approval = self._tool_approval_manager.get_approval(
            run_id=run_id,
            tool_call_id=tool_call_id,
        )
        ticket = (
            self._approval_ticket_repo.get(tool_call_id)
            if self._approval_ticket_repo is not None
            else None
        )
        if ticket is not None and ticket.run_id != run_id:
            raise KeyError(f"Tool approval {tool_call_id} not found for run {run_id}")
        resolved_ticket = ticket
        if self._approval_ticket_repo is not None:
            try:
                resolved_ticket = self._approval_ticket_repo.resolve(
                    tool_call_id=tool_call_id,
                    status=(
                        ApprovalTicketStatus.APPROVED
                        if _approval_action_is_approved(action)
                        else ApprovalTicketStatus.DENIED
                    ),
                    feedback=feedback,
                    expected_status=ApprovalTicketStatus.REQUESTED,
                )
            except ApprovalTicketStatusConflictError as exc:
                raise RuntimeError(
                    _approval_ticket_status_conflict_message(
                        tool_call_id=tool_call_id,
                        status=exc.actual_status,
                    )
                ) from exc
        if _approval_action_requires_shell_grant(action):
            self._persist_shell_approval_grants(ticket=ticket, action=action)
        if approval is not None:
            try:
                self._tool_approval_manager.resolve_approval(
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    action=cast(ToolApprovalAction, action),
                    feedback=feedback,
                )
            except KeyError:
                pass
        if run_id in self._running_run_ids or runtime is None:
            return

        instance_id = (
            approval["instance_id"]
            if approval is not None
            else (resolved_ticket.instance_id if resolved_ticket is not None else None)
        )
        role_id = (
            approval["role_id"]
            if approval is not None
            else (resolved_ticket.role_id if resolved_ticket is not None else None)
        )
        tool_name = (
            approval["tool_name"]
            if approval is not None
            else (resolved_ticket.tool_name if resolved_ticket is not None else "")
        )
        self._safe_publish_run_event(
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
            ),
            failure_event="run.event.publish_failed",
        )

    def _persist_shell_approval_grants(
        self,
        *,
        ticket: ApprovalTicketRecord | None,
        action: str,
    ) -> None:
        if ticket is None or self._shell_approval_repo is None:
            return
        if ticket.status != ApprovalTicketStatus.REQUESTED:
            return
        resolved = _extract_shell_grant_metadata(ticket)
        if resolved is None:
            return
        workspace_key, runtime_family, normalized_command, prefix_candidates = resolved
        if action == "approve_exact" and normalized_command:
            self._shell_approval_repo.grant(
                workspace_key=workspace_key,
                runtime_family=runtime_family,
                scope=ShellApprovalScope.EXACT,
                value=normalized_command,
            )
        if action == "approve_prefix":
            for candidate in prefix_candidates:
                self._shell_approval_repo.grant(
                    workspace_key=workspace_key,
                    runtime_family=runtime_family,
                    scope=ShellApprovalScope.PREFIX,
                    value=candidate,
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

    def list_user_questions(self, run_id: str) -> list[dict[str, JsonValue]]:
        repo = self._require_user_question_repo()
        runtime = self._runtime_for_run(run_id)
        if runtime is None:
            raise KeyError(f"Run {run_id} not found")
        return [
            cast(dict[str, JsonValue], item.model_dump(mode="json"))
            for item in repo.list_by_run(run_id)
        ]

    def answer_user_question(
        self,
        *,
        run_id: str,
        question_id: str,
        answers: UserQuestionAnswerSubmission,
    ) -> dict[str, JsonValue]:
        repo = self._require_user_question_repo()
        runtime = self._runtime_for_run(run_id)
        if runtime is None:
            raise KeyError(f"Run {run_id} not found")
        if runtime.status == RunRuntimeStatus.STOPPING:
            raise RuntimeError(
                f"Run {run_id} is stopping. Wait for it to stop before answering."
            )
        record = repo.get(question_id)
        if record is None or record.run_id != run_id:
            raise KeyError(f"User question {question_id} not found for run {run_id}")
        if record.status != UserQuestionRequestStatus.REQUESTED:
            raise RuntimeError(
                _user_question_status_conflict_message(
                    question_id=question_id,
                    status=record.status,
                )
            )
        validated_answers = _validate_user_question_answers(
            questions=record.questions,
            answers=answers,
        )
        try:
            resolved_record = repo.resolve(
                question_id=question_id,
                status=UserQuestionRequestStatus.ANSWERED,
                answers=validated_answers.answers,
                expected_status=UserQuestionRequestStatus.REQUESTED,
            )
        except UserQuestionStatusConflictError as exc:
            raise RuntimeError(
                _user_question_status_conflict_message(
                    question_id=question_id,
                    status=exc.actual_status,
                )
            ) from exc
        manager_question = (
            self._user_question_manager.get_question(
                run_id=run_id,
                question_id=question_id,
            )
            if self._user_question_manager is not None
            else None
        )
        if self._user_question_manager is not None and manager_question is not None:
            try:
                self._user_question_manager.resolve_question(
                    run_id=run_id,
                    question_id=question_id,
                    answers=validated_answers,
                )
            except (KeyError, UserQuestionClosedError):
                manager_question = None
        self._safe_publish_run_event(
            RunEvent(
                session_id=resolved_record.session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=resolved_record.task_id,
                instance_id=(
                    manager_question["instance_id"]
                    if manager_question is not None
                    else None
                ),
                role_id=manager_question["role_id"]
                if manager_question is not None
                else None,
                event_type=RunEventType.USER_QUESTION_ANSWERED,
                payload_json=dumps(
                    {
                        "question_id": question_id,
                        "answers": [
                            answer.model_dump(mode="json")
                            for answer in validated_answers.answers
                        ],
                        "instance_id": (
                            manager_question["instance_id"]
                            if manager_question is not None
                            else resolved_record.instance_id
                        ),
                        "role_id": (
                            manager_question["role_id"]
                            if manager_question is not None
                            else resolved_record.role_id
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
            failure_event="run.event.publish_failed",
        )
        current_runtime = self._runtime_for_run(run_id)
        if (
            run_id not in self._running_run_ids
            and current_runtime is not None
            and current_runtime.is_recoverable
            and current_runtime.status
            in {RunRuntimeStatus.PAUSED, RunRuntimeStatus.STOPPED}
            and not self._has_pending_resolvable_question_for_session(
                resolved_record.session_id
            )
            and not self._has_running_agents_for_run(run_id)
        ):
            try:
                _ = self.resume_run(run_id)
            except RuntimeError as exc:
                if not _is_run_already_running_conflict(run_id=run_id, error=exc):
                    raise
            else:
                self.ensure_run_started(run_id)
        return cast(dict[str, JsonValue], resolved_record.model_dump(mode="json"))

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
            self._user_question_repo is not None
            and self._has_pending_resolvable_question_for_session(runtime.session_id)
        ):
            raise RuntimeError(
                f"Run {run_id} is waiting for manual action. Answer the pending question before continuing."
            )
        if runtime.status == RunRuntimeStatus.STOPPING:
            raise RuntimeError(
                f"Run {run_id} is stopping. Wait for it to stop before continuing."
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

    def _find_task_for_instance(self, *, run_id: str, instance_id: str) -> str | None:
        if not instance_id:
            return None
        task_repo = self._require_task_repo()
        for record in task_repo.list_by_trace(run_id):
            if record.assigned_instance_id == instance_id:
                return record.envelope.task_id
        return None

    def _can_enqueue_followup_to_instance(
        self, *, run_id: str, instance_id: str
    ) -> bool:
        if not self._injection_manager.is_active(run_id):
            return False
        try:
            record = self._require_agent_repo().get_instance(instance_id)
        except KeyError:
            return False
        return record.run_id == run_id and record.status == InstanceStatus.RUNNING

    def _has_running_agents_for_run(self, run_id: str) -> bool:
        if self._agent_repo is None:
            return False
        return any(True for _ in self._agent_repo.list_running(run_id))

    def _has_pending_resolvable_question_for_session(self, session_id: str) -> bool:
        if self._user_question_repo is None:
            return False
        for record in self._user_question_repo.list_by_session(session_id):
            if self._runtime_for_run(record.run_id) is not None:
                return True
        return False

    def _can_enqueue_followup_to_coordinator(self, run_id: str) -> bool:
        if not self._injection_manager.is_active(run_id):
            return False
        try:
            root = self._root_task_for_run(run_id)
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
                self._publish_injection_event(
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

    def _append_followup_to_coordinator(
        self,
        run_id: str,
        content: str,
        *,
        enqueue: bool,
        source: InjectionSource = InjectionSource.USER,
    ) -> bool:
        try:
            root = self._root_task_for_run(run_id)
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
                    message="Follow-up appended to root agent conversation",
                    payload={
                        "enqueue": enqueue,
                        "source": source.value,
                        "length": len(content),
                    },
                )
            return True
        except KeyError:
            if self._run_intent_repo is None:
                raise
            self._run_intent_repo.append_followup(run_id=run_id, content=content)
            return False

    def _update_run_yolo(
        self,
        *,
        run_id: str,
        session_id: str,
        yolo: bool,
    ) -> None:
        if self._run_intent_repo is None:
            return
        try:
            intent = self._run_intent_repo.get(
                run_id,
                fallback_session_id=session_id,
            )
        except KeyError:
            return
        if intent.yolo == yolo:
            return
        intent.session_id = session_id
        intent.yolo = yolo
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

    def _publish_generation_progress(
        self,
        *,
        run_id: str,
        session_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        run_kind: str,
        phase: str,
        progress: float,
        preview_asset_id: str | None,
    ) -> None:
        self._safe_publish_run_event(
            RunEvent(
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=task_id,
                instance_id=instance_id,
                role_id=role_id,
                event_type=RunEventType.GENERATION_PROGRESS,
                payload_json=dumps(
                    {
                        "run_kind": run_kind,
                        "phase": phase,
                        "progress": progress,
                        "preview_asset_id": preview_asset_id,
                    }
                ),
            ),
            failure_event="run.event.publish_failed",
        )

    def _publish_output_delta(
        self,
        *,
        run_id: str,
        session_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        output: tuple[TextContentPart | MediaRefContentPart, ...],
    ) -> None:
        payload = {
            "output": [part.model_dump(mode="json") for part in output],
            "role_id": role_id,
            "instance_id": instance_id,
        }
        self._safe_publish_run_event(
            RunEvent(
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=task_id,
                instance_id=instance_id,
                role_id=role_id,
                event_type=RunEventType.OUTPUT_DELTA,
                payload_json=dumps(payload),
            ),
            failure_event="run.event.publish_failed",
        )

    def _append_media_output_message(
        self,
        *,
        request: LLMRequest,
        output: tuple[TextContentPart | MediaRefContentPart, ...],
    ) -> None:
        message_repo = self._require_message_repo()
        media_asset_service = self._require_media_asset_service()
        response_parts: list[TextPart | FilePart] = []
        for part in output:
            if isinstance(part, TextContentPart):
                response_parts.append(TextPart(content=part.text))
                continue
            record = media_asset_service.get_asset(part.asset_id)
            try:
                file_path, _media_type = media_asset_service.get_asset_file(
                    session_id=record.session_id,
                    asset_id=record.asset_id,
                )
            except FileNotFoundError:
                response_parts.append(TextPart(content=part.url))
                continue
            response_parts.append(
                FilePart(
                    content=BinaryContent(
                        data=file_path.read_bytes(),
                        media_type=record.mime_type,
                    )
                )
            )
        if not response_parts:
            return
        message_repo.append(
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            conversation_id=request.conversation_id,
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            messages=[
                ModelResponse(parts=response_parts, model_name="media_generation")
            ],
        )

    def _build_completed_error_run_result(
        self,
        *,
        run_id: str,
        session_id: str,
        error_code: str,
        error_message: str,
        root_task_id: str | None = None,
        instance_id: str | None = None,
        role_id: str | None = None,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
    ) -> RunResult:
        assistant_message = build_assistant_error_message(
            error_code=error_code,
            error_message=error_message,
        )
        try:
            runtime = self._runtime_for_run(run_id)
        except Exception:
            runtime = None
        resolved_root_task_id = (
            root_task_id
            or (runtime.root_task_id if runtime is not None else None)
            or (runtime.active_task_id if runtime is not None else None)
            or run_id
        )
        resolved_instance_id = instance_id or (
            runtime.active_instance_id if runtime is not None else None
        )
        resolved_role_id = role_id or (
            runtime.active_role_id if runtime is not None else None
        )
        resolved_conversation_id = conversation_id
        resolved_workspace_id = workspace_id
        if resolved_instance_id and self._agent_repo is not None:
            try:
                instance = self._agent_repo.get_instance(resolved_instance_id)
            except KeyError:
                instance = None
            if instance is not None:
                resolved_conversation_id = (
                    resolved_conversation_id or instance.conversation_id
                )
                resolved_workspace_id = resolved_workspace_id or instance.workspace_id
                resolved_role_id = resolved_role_id or instance.role_id
        if resolved_conversation_id is None and resolved_role_id is not None:
            resolved_conversation_id = build_conversation_id(
                session_id, resolved_role_id
            )
        if resolved_workspace_id is None:
            try:
                resolved_workspace_id = self._session_repo.get(session_id).workspace_id
            except Exception:
                resolved_workspace_id = "default"
        if resolved_conversation_id is not None:
            message_repo = self._require_message_repo()
            message_repo.prune_conversation_history_to_safe_boundary(
                resolved_conversation_id
            )
            message_repo.append(
                session_id=session_id,
                workspace_id=resolved_workspace_id,
                conversation_id=resolved_conversation_id,
                agent_role_id=resolved_role_id or "",
                instance_id=resolved_instance_id or resolved_conversation_id,
                task_id=resolved_root_task_id,
                trace_id=run_id,
                messages=[build_assistant_error_response(assistant_message)],
            )
        if resolved_instance_id is not None and resolved_role_id is not None:
            self._safe_publish_run_event(
                RunEvent(
                    session_id=session_id,
                    run_id=run_id,
                    trace_id=run_id,
                    task_id=resolved_root_task_id,
                    instance_id=resolved_instance_id,
                    role_id=resolved_role_id,
                    event_type=RunEventType.TEXT_DELTA,
                    payload_json=dumps(
                        {
                            "text": assistant_message,
                            "role_id": resolved_role_id,
                            "instance_id": resolved_instance_id,
                        }
                    ),
                ),
                failure_event="run.event.publish_failed",
            )
        return RunResult(
            trace_id=run_id,
            root_task_id=resolved_root_task_id,
            status="failed",
            completion_reason=RunCompletionReason.ASSISTANT_ERROR,
            error_code=error_code,
            error_message=error_message,
            output=content_parts_from_text(assistant_message),
        )

    def _normalize_terminal_run_result(self, result: RunResult) -> RunResult:
        error_text = str(result.error_message or result.output_text or "").strip()
        output = result.output
        if not output and error_text:
            output = content_parts_from_text(error_text)
        if (
            result.status != "failed"
            and result.completion_reason != RunCompletionReason.ASSISTANT_ERROR
        ):
            if output == result.output:
                return result
            return result.model_copy(update={"output": output})
        updates: dict[str, object] = {
            "status": "failed",
            "completion_reason": RunCompletionReason.ASSISTANT_ERROR,
            "output": output,
        }
        if error_text:
            updates["error_message"] = error_text
        return result.model_copy(update=updates)

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

    def _auto_recovery_policy_for(
        self,
        payload: "RecoverableRunPausePayload",
    ) -> AutoRecoveryPolicy | None:
        for policy in AUTO_RECOVERY_POLICIES:
            if policy.error_code != payload.error_code:
                continue
            if not self._auto_recovery_policy_matches_payload(
                policy=policy,
                payload=payload,
            ):
                return None
            return policy
        return None

    def _auto_recovery_policy_matches_payload(
        self,
        *,
        policy: AutoRecoveryPolicy,
        payload: "RecoverableRunPausePayload",
    ) -> bool:
        if policy.error_code != "network_error":
            return True
        return self._is_transient_network_error_message(payload.error_message)

    def _is_transient_network_error_message(self, error_message: str) -> bool:
        normalized = error_message.strip().lower()
        if not normalized:
            return False
        blocking_markers = (
            "no_proxy",
            "proxy authentication",
            "proxy auth",
            "ssl",
            "tls",
            "certificate",
            "cert",
            "connection refused",
            "actively refused",
            "name or service not known",
            "nodename nor servname",
            "temporary failure in name resolution",
            "getaddrinfo",
            "dns",
            "host not found",
            "407",
        )
        if any(marker in normalized for marker in blocking_markers):
            return False
        transient_markers = (
            "connection reset",
            "connection aborted",
            "connection closed",
            "server disconnected",
            "temporarily unavailable",
            "temporary network",
            "temporary failure",
            "eof",
            "broken pipe",
        )
        return any(marker in normalized for marker in transient_markers)

    def _next_auto_recovery_attempt(
        self,
        payload: "RecoverableRunPausePayload",
        *,
        policy: AutoRecoveryPolicy,
    ) -> int | None:
        attempts = self._count_auto_recovery_attempts(
            payload.run_id,
            reason=policy.reason,
        )
        if attempts >= policy.max_attempts:
            return None
        return attempts + 1

    def _count_auto_recovery_attempts(
        self,
        run_id: str,
        *,
        reason: AutoRecoveryReason,
    ) -> int:
        persisted_attempts = self._count_persisted_auto_recovery_attempts(
            run_id,
            reason=reason,
        )
        in_memory_attempts = self._auto_recovery_attempts.get((run_id, reason), 0)
        return max(persisted_attempts, in_memory_attempts)

    def _count_persisted_auto_recovery_attempts(
        self,
        run_id: str,
        *,
        reason: AutoRecoveryReason,
    ) -> int:
        if self._event_log is None:
            return 0
        count = 0
        for event in self._event_log.list_by_trace(run_id):
            if str(event.get("event_type") or "") != RunEventType.RUN_RESUMED.value:
                continue
            raw_payload = event.get("payload_json")
            if not isinstance(raw_payload, str) or not raw_payload.strip():
                continue
            try:
                parsed = loads(raw_payload)
            except ValueError:
                continue
            if not isinstance(parsed, dict):
                continue
            if str(parsed.get("reason") or "") == reason.value:
                count += 1
        return count

    def _record_auto_recovery_attempt(
        self,
        *,
        run_id: str,
        reason: AutoRecoveryReason,
        attempt: int,
    ) -> None:
        key = (run_id, reason)
        current = self._auto_recovery_attempts.get(key, 0)
        self._auto_recovery_attempts[key] = max(current, attempt)

    def _queue_auto_recovery_prompt(
        self,
        *,
        payload: "RecoverableRunPausePayload",
        policy: AutoRecoveryPolicy,
    ) -> None:
        if self._append_followup_to_instance(
            run_id=payload.run_id,
            instance_id=payload.instance_id,
            task_id=payload.task_id,
            content=policy.prompt,
            enqueue=False,
            source=InjectionSource.SYSTEM,
        ):
            return
        self._append_followup_to_coordinator(
            payload.run_id,
            policy.prompt,
            enqueue=False,
            source=InjectionSource.SYSTEM,
        )

    def _build_run_resumed_payload(
        self,
        *,
        session_id: str,
        reason: str,
        attempt: int | None = None,
        max_attempts: int | None = None,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "session_id": session_id,
            "reason": reason,
        }
        if attempt is not None:
            payload["attempt"] = attempt
        if max_attempts is not None:
            payload["max_attempts"] = max_attempts
        return payload

    def _transition_run_to_resumed(
        self,
        *,
        run_id: str,
        session_id: str,
        reason: str,
        attempt: int | None = None,
        max_attempts: int | None = None,
    ) -> dict[str, JsonValue]:
        runtime = self._runtime_for_run(run_id)
        phase = RunRuntimePhase.COORDINATOR_RUNNING
        if runtime is not None and runtime.phase != RunRuntimePhase.TERMINAL:
            phase = runtime.phase
        self._safe_runtime_update(
            run_id,
            status=RunRuntimeStatus.RUNNING,
            phase=phase,
            last_error=None,
        )
        payload = self._build_run_resumed_payload(
            session_id=session_id,
            reason=reason,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        self._safe_publish_run_event(
            RunEvent(
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=None,
                event_type=RunEventType.RUN_RESUMED,
                payload_json=dumps(payload),
            ),
            failure_event="run.event.publish_failed",
        )
        return payload

    def _build_run_paused_payload(
        self, payload: "RecoverableRunPausePayload"
    ) -> dict[str, JsonValue]:
        paused_payload: dict[str, JsonValue] = payload.model_dump(mode="json")
        policy = self._auto_recovery_policy_for(payload)
        if policy is None:
            return paused_payload
        attempts = self._count_auto_recovery_attempts(
            payload.run_id,
            reason=policy.reason,
        )
        paused_payload["auto_recovery_exhausted"] = attempts >= policy.max_attempts
        paused_payload["attempt"] = attempts
        paused_payload["max_attempts"] = policy.max_attempts
        paused_payload["auto_recovery_reason"] = policy.reason.value
        return paused_payload

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

    def _require_user_question_repo(self) -> UserQuestionRepository:
        if self._user_question_repo is None:
            raise RuntimeError("RunManager requires user_question_repo")
        return self._user_question_repo

    def _require_media_asset_service(self) -> MediaAssetService:
        if self._media_asset_service is None:
            raise RuntimeError("RunManager requires media_asset_service for media runs")
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
