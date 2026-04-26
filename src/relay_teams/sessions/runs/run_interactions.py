# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from json import dumps
from typing import cast

from pydantic import JsonValue

from relay_teams.logger import get_logger
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.run_event_publisher import RunEventPublisher
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimeRecord,
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
    UserQuestionRequestStatus,
    UserQuestionSelection,
)
from relay_teams.sessions.runs.user_question_repository import (
    UserQuestionRepository,
    UserQuestionStatusConflictError,
)
from relay_teams.tools.runtime.approval_state import (
    ToolApprovalAction,
    ToolApprovalManager,
)
from relay_teams.tools.runtime.approval_ticket_repo import (
    ApprovalTicketRecord,
    ApprovalTicketRepository,
    ApprovalTicketStatus,
    ApprovalTicketStatusConflictError,
)
from relay_teams.tools.workspace_tools.shell_approval_repo import (
    ShellApprovalRepository,
    ShellApprovalScope,
)
from relay_teams.tools.workspace_tools.shell_policy import ShellRuntimeFamily

LOGGER = get_logger(__name__)

type ShellApprovalGrantSpec = tuple[
    str,
    ShellRuntimeFamily,
    ShellApprovalScope,
    str,
]


def approval_action_is_approved(action: str) -> bool:
    return action in {"approve", "approve_once", "approve_exact", "approve_prefix"}


def approval_action_requires_shell_grant(action: str) -> bool:
    return action in {"approve_exact", "approve_prefix"}


def parse_tool_approval_action(action: str) -> ToolApprovalAction:
    if action == "approve":
        return "approve"
    if action == "approve_once":
        return "approve_once"
    if action == "approve_exact":
        return "approve_exact"
    if action == "approve_prefix":
        return "approve_prefix"
    if action == "deny":
        return "deny"
    raise ValueError(f"Unsupported action: {action}")


def normalize_shell_prefix_candidates(raw_value: object) -> tuple[str, ...]:
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


def extract_shell_grant_metadata(
    ticket: ApprovalTicketRecord,
) -> tuple[str, ShellRuntimeFamily, str, tuple[str, ...]] | None:
    if ticket.tool_name != "shell":
        return None
    metadata = ticket.metadata
    workspace_key = str(metadata.get("workspace_key") or "").strip()
    runtime_family = str(metadata.get("runtime_family") or "").strip()
    normalized_command = str(metadata.get("normalized_command") or "").strip()
    prefix_candidates = normalize_shell_prefix_candidates(
        metadata.get("prefix_candidates")
    )
    if not workspace_key or not runtime_family:
        return None
    try:
        resolved_runtime_family = ShellRuntimeFamily(runtime_family)
    except ValueError:
        return None
    return workspace_key, resolved_runtime_family, normalized_command, prefix_candidates


def shell_approval_grant_specs(
    *,
    ticket: ApprovalTicketRecord | None,
    action: str,
) -> tuple[ShellApprovalGrantSpec, ...]:
    if ticket is None or ticket.status != ApprovalTicketStatus.REQUESTED:
        return ()
    resolved = extract_shell_grant_metadata(ticket)
    if resolved is None:
        return ()
    workspace_key, runtime_family, normalized_command, prefix_candidates = resolved
    if action == "approve_exact" and normalized_command:
        return (
            (
                workspace_key,
                runtime_family,
                ShellApprovalScope.EXACT,
                normalized_command,
            ),
        )
    if action == "approve_prefix":
        return tuple(
            (
                workspace_key,
                runtime_family,
                ShellApprovalScope.PREFIX,
                candidate,
            )
            for candidate in prefix_candidates
        )
    return ()


def validate_user_question_answers(
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


def user_question_status_conflict_message(
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


def approval_ticket_status_conflict_message(
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


def is_run_already_running_conflict(*, run_id: str, error: RuntimeError) -> bool:
    return str(error) == f"Run {run_id} is already running"


class RunInteractionService:
    def __init__(
        self,
        *,
        run_control_manager: RunControlManager,
        tool_approval_manager: ToolApprovalManager,
        get_approval_ticket_repo: Callable[[], ApprovalTicketRepository | None],
        get_shell_approval_repo: Callable[[], ShellApprovalRepository | None],
        require_user_question_repo: Callable[[], UserQuestionRepository],
        get_user_question_repo: Callable[[], UserQuestionRepository | None],
        get_user_question_manager: Callable[[], UserQuestionManager | None],
        get_runtime: Callable[[str], RunRuntimeRecord | None],
        get_runtime_async: Callable[[str], Awaitable[RunRuntimeRecord | None]],
        is_running_run: Callable[[str], bool],
        has_pending_resolvable_question_for_session: Callable[[str], bool],
        has_pending_resolvable_question_for_session_async: Callable[
            [str], Awaitable[bool]
        ],
        has_running_agents_for_run: Callable[[str], bool],
        has_running_agents_for_run_async: Callable[[str], Awaitable[bool]],
        resume_run: Callable[[str], str],
        resume_run_async: Callable[[str], Awaitable[str]],
        ensure_run_started: Callable[[str], None],
        ensure_run_started_async: Callable[[str], Awaitable[None]],
        event_publisher: RunEventPublisher,
    ) -> None:
        self._run_control_manager = run_control_manager
        self._tool_approval_manager = tool_approval_manager
        self._get_approval_ticket_repo = get_approval_ticket_repo
        self._get_shell_approval_repo = get_shell_approval_repo
        self._require_user_question_repo = require_user_question_repo
        self._get_user_question_repo = get_user_question_repo
        self._get_user_question_manager = get_user_question_manager
        self._get_runtime = get_runtime
        self._get_runtime_async = get_runtime_async
        self._is_running_run = is_running_run
        self._has_pending_resolvable_question_for_session = (
            has_pending_resolvable_question_for_session
        )
        self._has_pending_resolvable_question_for_session_async = (
            has_pending_resolvable_question_for_session_async
        )
        self._has_running_agents_for_run = has_running_agents_for_run
        self._has_running_agents_for_run_async = has_running_agents_for_run_async
        self._resume_run = resume_run
        self._resume_run_async = resume_run_async
        self._ensure_run_started = ensure_run_started
        self._ensure_run_started_async = ensure_run_started_async
        self._event_publisher = event_publisher

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

    async def inject_subagent_message_async(
        self,
        *,
        run_id: str,
        instance_id: str,
        content: str,
    ) -> None:
        await asyncio.to_thread(
            self.inject_subagent_message,
            run_id=run_id,
            instance_id=instance_id,
            content=content,
        )

    def stop_subagent(self, run_id: str, instance_id: str) -> dict[str, str]:
        stopped = self._run_control_manager.stop_subagent(
            run_id=run_id,
            instance_id=instance_id,
        )
        self.complete_pending_user_questions(
            run_id=run_id,
            instance_id=instance_id,
            reason="subagent_stopped",
        )
        return stopped

    async def stop_subagent_async(
        self, run_id: str, instance_id: str
    ) -> dict[str, str]:
        stopped = await self._run_control_manager.stop_subagent_async(
            run_id=run_id,
            instance_id=instance_id,
        )
        await self.complete_pending_user_questions_async(
            run_id=run_id,
            instance_id=instance_id,
            reason="subagent_stopped",
        )
        return stopped

    def resolve_tool_approval(
        self,
        run_id: str,
        tool_call_id: str,
        action: str,
        feedback: str = "",
    ) -> None:
        approval_action = parse_tool_approval_action(action)
        runtime = self._get_runtime(run_id)
        if (
            not self._is_running_run(run_id)
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
        approval_ticket_repo = self._get_approval_ticket_repo()
        ticket = (
            approval_ticket_repo.get(tool_call_id)
            if approval_ticket_repo is not None
            else None
        )
        if ticket is not None and ticket.run_id != run_id:
            raise KeyError(f"Tool approval {tool_call_id} not found for run {run_id}")
        resolved_ticket = ticket
        if approval_ticket_repo is not None:
            try:
                resolved_ticket = approval_ticket_repo.resolve(
                    tool_call_id=tool_call_id,
                    status=(
                        ApprovalTicketStatus.APPROVED
                        if approval_action_is_approved(action)
                        else ApprovalTicketStatus.DENIED
                    ),
                    feedback=feedback,
                    expected_status=ApprovalTicketStatus.REQUESTED,
                )
            except ApprovalTicketStatusConflictError as exc:
                raise RuntimeError(
                    approval_ticket_status_conflict_message(
                        tool_call_id=tool_call_id,
                        status=exc.actual_status,
                    )
                ) from exc
        if approval_action_requires_shell_grant(action):
            self.persist_shell_approval_grants(ticket=ticket, action=action)
        if approval is not None:
            try:
                self._tool_approval_manager.resolve_approval(
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    action=approval_action,
                    feedback=feedback,
                )
            except KeyError:
                LOGGER.debug(  # pragma: no cover
                    "Tool approval was already absent from in-memory tracking",
                    extra={"run_id": run_id, "tool_call_id": tool_call_id},
                )
        if self._is_running_run(run_id) or runtime is None:
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
        self._event_publisher.safe_publish_run_event(
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

    async def resolve_tool_approval_async(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        action: str,
        feedback: str = "",
    ) -> None:
        approval_action = parse_tool_approval_action(action)
        runtime = await self._get_runtime_async(run_id)
        if (
            not self._is_running_run(run_id)
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
        approval_ticket_repo = self._get_approval_ticket_repo()
        ticket = (
            await approval_ticket_repo.get_async(tool_call_id)
            if approval_ticket_repo is not None
            else None
        )
        if ticket is not None and ticket.run_id != run_id:
            raise KeyError(f"Tool approval {tool_call_id} not found for run {run_id}")
        resolved_ticket = ticket
        if approval_ticket_repo is not None:
            try:
                resolved_ticket = await approval_ticket_repo.resolve_async(
                    tool_call_id=tool_call_id,
                    status=(
                        ApprovalTicketStatus.APPROVED
                        if approval_action_is_approved(action)
                        else ApprovalTicketStatus.DENIED
                    ),
                    feedback=feedback,
                    expected_status=ApprovalTicketStatus.REQUESTED,
                )
            except ApprovalTicketStatusConflictError as exc:
                raise RuntimeError(
                    approval_ticket_status_conflict_message(
                        tool_call_id=tool_call_id,
                        status=exc.actual_status,
                    )
                ) from exc
        if approval_action_requires_shell_grant(action):
            await self.persist_shell_approval_grants_async(
                ticket=ticket,
                action=action,
            )
        if approval is not None:
            try:
                self._tool_approval_manager.resolve_approval(
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    action=approval_action,
                    feedback=feedback,
                )
            except KeyError:
                LOGGER.debug(  # pragma: no cover
                    "Tool approval was already absent from in-memory tracking",
                    extra={"run_id": run_id, "tool_call_id": tool_call_id},
                )
        if self._is_running_run(run_id) or runtime is None:
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
        await self._event_publisher.safe_publish_run_event_async(
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

    def persist_shell_approval_grants(
        self,
        *,
        ticket: ApprovalTicketRecord | None,
        action: str,
    ) -> None:
        shell_approval_repo = self._get_shell_approval_repo()
        if shell_approval_repo is None:
            return
        for workspace_key, runtime_family, scope, value in shell_approval_grant_specs(
            ticket=ticket,
            action=action,
        ):
            shell_approval_repo.grant(
                workspace_key=workspace_key,
                runtime_family=runtime_family,
                scope=scope,
                value=value,
            )

    async def persist_shell_approval_grants_async(
        self,
        *,
        ticket: ApprovalTicketRecord | None,
        action: str,
    ) -> None:
        shell_approval_repo = self._get_shell_approval_repo()
        if shell_approval_repo is None:
            return
        for workspace_key, runtime_family, scope, value in shell_approval_grant_specs(
            ticket=ticket,
            action=action,
        ):
            await shell_approval_repo.grant_async(
                workspace_key=workspace_key,
                runtime_family=runtime_family,
                scope=scope,
                value=value,
            )

    def list_open_tool_approvals(self, run_id: str) -> list[dict[str, str]]:
        approval_ticket_repo = self._get_approval_ticket_repo()
        if approval_ticket_repo is None:
            return self._tool_approval_manager.list_open_approvals(run_id=run_id)
        return [
            {
                "tool_call_id": item.tool_call_id,
                "instance_id": item.instance_id,
                "role_id": item.role_id,
                "tool_name": item.tool_name,
                "args_preview": item.args_preview,
            }
            for item in approval_ticket_repo.list_open_by_run(run_id)
        ]

    async def list_open_tool_approvals_async(
        self,
        run_id: str,
    ) -> list[dict[str, str]]:
        approval_ticket_repo = self._get_approval_ticket_repo()
        if approval_ticket_repo is None:
            return self._tool_approval_manager.list_open_approvals(run_id=run_id)
        return [
            {
                "tool_call_id": item.tool_call_id,
                "instance_id": item.instance_id,
                "role_id": item.role_id,
                "tool_name": item.tool_name,
                "args_preview": item.args_preview,
            }
            for item in await approval_ticket_repo.list_open_by_run_async(run_id)
        ]

    def list_user_questions(self, run_id: str) -> list[dict[str, JsonValue]]:
        repo = self._require_user_question_repo()
        runtime = self._get_runtime(run_id)
        if runtime is None:
            raise KeyError(f"Run {run_id} not found")
        return [
            cast(dict[str, JsonValue], item.model_dump(mode="json"))
            for item in repo.list_by_run(run_id)
        ]

    async def list_user_questions_async(
        self,
        run_id: str,
    ) -> list[dict[str, JsonValue]]:
        repo = self._require_user_question_repo()
        runtime = await self._get_runtime_async(run_id)
        if runtime is None:
            raise KeyError(f"Run {run_id} not found")
        return [
            cast(dict[str, JsonValue], item.model_dump(mode="json"))
            for item in await repo.list_by_run_async(run_id)
        ]

    def answer_user_question(
        self,
        *,
        run_id: str,
        question_id: str,
        answers: UserQuestionAnswerSubmission,
    ) -> dict[str, JsonValue]:
        repo = self._require_user_question_repo()
        runtime = self._get_runtime(run_id)
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
                user_question_status_conflict_message(
                    question_id=question_id,
                    status=record.status,
                )
            )
        validated_answers = validate_user_question_answers(
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
                user_question_status_conflict_message(
                    question_id=question_id,
                    status=exc.actual_status,
                )
            ) from exc
        user_question_manager = self._get_user_question_manager()
        manager_question = (
            user_question_manager.get_question(
                run_id=run_id,
                question_id=question_id,
            )
            if user_question_manager is not None
            else None
        )
        if user_question_manager is not None and manager_question is not None:
            try:
                user_question_manager.resolve_question(
                    run_id=run_id,
                    question_id=question_id,
                    answers=validated_answers,
                )
            except (KeyError, UserQuestionClosedError):
                manager_question = None
        self._event_publisher.safe_publish_run_event(
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
        current_runtime = self._get_runtime(run_id)
        if (
            not self._is_running_run(run_id)
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
                _ = self._resume_run(run_id)
            except RuntimeError as exc:
                if not is_run_already_running_conflict(run_id=run_id, error=exc):
                    raise
            else:
                self._ensure_run_started(run_id)
        return cast(dict[str, JsonValue], resolved_record.model_dump(mode="json"))

    async def answer_user_question_async(
        self,
        *,
        run_id: str,
        question_id: str,
        answers: UserQuestionAnswerSubmission,
    ) -> dict[str, JsonValue]:
        repo = self._require_user_question_repo()
        runtime = await self._get_runtime_async(run_id)
        if runtime is None:
            raise KeyError(f"Run {run_id} not found")
        if runtime.status == RunRuntimeStatus.STOPPING:
            raise RuntimeError(
                f"Run {run_id} is stopping. Wait for it to stop before answering."
            )
        record = await repo.get_async(question_id)
        if record is None or record.run_id != run_id:
            raise KeyError(f"User question {question_id} not found for run {run_id}")
        if record.status != UserQuestionRequestStatus.REQUESTED:
            raise RuntimeError(
                user_question_status_conflict_message(
                    question_id=question_id,
                    status=record.status,
                )
            )
        validated_answers = validate_user_question_answers(
            questions=record.questions,
            answers=answers,
        )
        try:
            resolved_record = await repo.resolve_async(
                question_id=question_id,
                status=UserQuestionRequestStatus.ANSWERED,
                answers=validated_answers.answers,
                expected_status=UserQuestionRequestStatus.REQUESTED,
            )
        except UserQuestionStatusConflictError as exc:
            raise RuntimeError(
                user_question_status_conflict_message(
                    question_id=question_id,
                    status=exc.actual_status,
                )
            ) from exc
        user_question_manager = self._get_user_question_manager()
        manager_question = (
            user_question_manager.get_question(
                run_id=run_id,
                question_id=question_id,
            )
            if user_question_manager is not None
            else None
        )
        if user_question_manager is not None and manager_question is not None:
            try:
                user_question_manager.resolve_question(
                    run_id=run_id,
                    question_id=question_id,
                    answers=validated_answers,
                )
            except (KeyError, UserQuestionClosedError):
                manager_question = None
        await self._event_publisher.safe_publish_run_event_async(
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
        current_runtime = await self._get_runtime_async(run_id)
        if (
            not self._is_running_run(run_id)
            and current_runtime is not None
            and current_runtime.is_recoverable
            and current_runtime.status
            in {RunRuntimeStatus.PAUSED, RunRuntimeStatus.STOPPED}
            and not await self._has_pending_resolvable_question_for_session_async(
                resolved_record.session_id
            )
            and not await self._has_running_agents_for_run_async(run_id)
        ):
            try:
                _ = await self._resume_run_async(run_id)
            except RuntimeError as exc:
                if not is_run_already_running_conflict(run_id=run_id, error=exc):
                    raise
            else:
                await self._ensure_run_started_async(run_id)
        return cast(dict[str, JsonValue], resolved_record.model_dump(mode="json"))

    def complete_pending_user_questions(
        self,
        *,
        run_id: str,
        instance_id: str | None = None,
        reason: str,
    ) -> None:
        repo = self._get_user_question_repo()
        if repo is None:
            return
        records = repo.list_by_run(run_id)
        targets = tuple(
            record
            for record in records
            if instance_id is None or record.instance_id == instance_id
        )
        user_question_manager = self._get_user_question_manager()
        if not targets:
            if user_question_manager is None:
                return
            if instance_id is None:
                user_question_manager.mark_questions_closed_for_run(
                    run_id=run_id,
                    reason=reason,
                )
                return
            _ = user_question_manager.mark_questions_closed_for_instance(
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
                LOGGER.debug(  # pragma: no cover
                    "User question was already resolved concurrently",
                    extra={"run_id": run_id, "question_id": record.question_id},
                )
            if resolved_record is not None:
                self._event_publisher.safe_publish_run_event(
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
            if user_question_manager is not None:
                user_question_manager.mark_question_closed(
                    run_id=run_id,
                    question_id=record.question_id,
                    reason=reason,
                )

    async def complete_pending_user_questions_async(
        self,
        *,
        run_id: str,
        instance_id: str | None = None,
        reason: str,
    ) -> None:
        repo = self._get_user_question_repo()
        if repo is None:
            return
        records = await repo.list_by_run_async(run_id)
        targets = tuple(
            record
            for record in records
            if instance_id is None or record.instance_id == instance_id
        )
        user_question_manager = self._get_user_question_manager()
        if not targets:
            if user_question_manager is None:
                return
            if instance_id is None:
                user_question_manager.mark_questions_closed_for_run(
                    run_id=run_id,
                    reason=reason,
                )
                return
            _ = user_question_manager.mark_questions_closed_for_instance(
                run_id=run_id,
                instance_id=instance_id,
                reason=reason,
            )
            return
        for record in targets:
            resolved_record = None
            try:
                resolved_record = await repo.resolve_async(
                    question_id=record.question_id,
                    status=UserQuestionRequestStatus.COMPLETED,
                    answers=record.answers,
                    expected_status=UserQuestionRequestStatus.REQUESTED,
                )
            except UserQuestionStatusConflictError:
                LOGGER.debug(  # pragma: no cover
                    "User question was already resolved concurrently",
                    extra={"run_id": run_id, "question_id": record.question_id},
                )
            if resolved_record is not None:
                await self._event_publisher.safe_publish_run_event_async(
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
            if user_question_manager is not None:
                user_question_manager.mark_question_closed(
                    run_id=run_id,
                    question_id=record.question_id,
                    reason=reason,
                )
