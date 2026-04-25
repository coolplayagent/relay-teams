# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from json import dumps
from uuid import uuid4

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.logger import get_logger, log_event
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import publish_run_event_async
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.user_question_manager import (
    DEFAULT_USER_QUESTION_TIMEOUT_SECONDS,
    UserQuestionClosedError,
)
from relay_teams.sessions.runs.user_question_models import (
    UserQuestionRequestRecord,
    UserQuestionPrompt,
    UserQuestionRequestStatus,
)
from relay_teams.sessions.runs.user_question_repository import (
    UserQuestionRepository,
    UserQuestionStatusConflictError,
)
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime.context import (
    ToolContext,
    ToolDeps,
)
from relay_teams.tools.runtime.execution import execute_tool_call
from relay_teams.tools.runtime.models import (
    ToolExecutionError,
    ToolResultProjection,
)

DESCRIPTION = load_tool_description(__file__)
LOGGER = get_logger(__name__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def ask_question(
        ctx: ToolContext,
        questions: list[UserQuestionPrompt],
    ) -> dict[str, JsonValue]:
        if not ctx.tool_call_id:
            ctx.tool_call_id = f"question_{uuid4().hex[:12]}"

        async def _action(
            questions: list[UserQuestionPrompt],
        ) -> ToolResultProjection:
            repo = ctx.deps.user_question_repo
            manager = ctx.deps.user_question_manager
            if repo is None or manager is None:
                raise ToolExecutionError(
                    error_type="tool_unavailable",
                    message="ask_question runtime is not configured",
                    retryable=False,
                )

            question_id = str(ctx.tool_call_id or "").strip()
            if not question_id:
                raise ToolExecutionError(
                    error_type="internal_error",
                    message="ask_question is missing tool_call_id",
                    retryable=False,
                )
            prompts = tuple(questions)
            if not prompts:
                raise ToolExecutionError(
                    error_type="validation_error",
                    message="ask_question requires at least one question",
                    retryable=False,
                )
            manager.open_question(
                run_id=ctx.deps.run_id,
                question_id=question_id,
                instance_id=ctx.deps.instance_id,
                role_id=ctx.deps.role_id,
            )
            try:
                _ = await repo.upsert_requested_async(
                    question_id=question_id,
                    run_id=ctx.deps.run_id,
                    session_id=ctx.deps.session_id,
                    task_id=ctx.deps.task_id,
                    instance_id=ctx.deps.instance_id,
                    role_id=ctx.deps.role_id,
                    tool_name="ask_question",
                    questions=prompts,
                )
            except Exception:
                manager.close_question(
                    run_id=ctx.deps.run_id,
                    question_id=question_id,
                    reason="persist_failed",
                )
                raise
            if (
                manager.get_question(
                    run_id=ctx.deps.run_id,
                    question_id=question_id,
                )
                is None
            ):
                return await _build_question_result_async(
                    ctx=ctx,
                    record=await _resolve_closed_question(
                        repo=repo,
                        question_id=question_id,
                    ),
                )
            await _set_runtime_phase_async(
                ctx, phase=RunRuntimePhase.AWAITING_MANUAL_ACTION
            )
            await _publish_user_question_event(
                ctx=ctx,
                event_type=RunEventType.USER_QUESTION_REQUESTED,
                payload={
                    "question_id": question_id,
                    "instance_id": ctx.deps.instance_id,
                    "role_id": ctx.deps.role_id,
                    "questions": [
                        question.model_dump(mode="json") for question in prompts
                    ],
                },
            )
            log_event(
                LOGGER,
                logging.INFO,
                event="user.question.requested",
                message="User question requested",
                payload={
                    "run_id": ctx.deps.run_id,
                    "question_id": question_id,
                    "question_count": len(prompts),
                },
            )

            try:
                answers = await asyncio.to_thread(
                    manager.wait_for_answer,
                    run_id=ctx.deps.run_id,
                    question_id=question_id,
                    timeout=DEFAULT_USER_QUESTION_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                manager.close_question(
                    run_id=ctx.deps.run_id,
                    question_id=question_id,
                    reason="timed_out",
                )
                try:
                    resolved_record = await repo.resolve_async(
                        question_id=question_id,
                        status=UserQuestionRequestStatus.TIMED_OUT,
                        expected_status=UserQuestionRequestStatus.REQUESTED,
                    )
                except UserQuestionStatusConflictError:
                    resolved_record = await repo.get_async(question_id)
                    if resolved_record is None:
                        raise KeyError(
                            f"Unknown user question: {question_id}"
                        ) from None
                if resolved_record.status == UserQuestionRequestStatus.TIMED_OUT:
                    await _publish_user_question_event(
                        ctx=ctx,
                        event_type=RunEventType.USER_QUESTION_ANSWERED,
                        payload={
                            "question_id": question_id,
                            "status": UserQuestionRequestStatus.TIMED_OUT.value,
                            "instance_id": ctx.deps.instance_id,
                            "role_id": ctx.deps.role_id,
                        },
                    )
                return await _build_question_result_async(
                    ctx=ctx, record=resolved_record
                )
            except UserQuestionClosedError:
                manager.close_question(
                    run_id=ctx.deps.run_id,
                    question_id=question_id,
                    reason="stopped",
                )
                payload: dict[str, JsonValue] = {
                    "status": "completed",
                    "question_id": question_id,
                }
                return ToolResultProjection(
                    visible_data=payload,
                    internal_data=payload,
                )

            manager.close_question(
                run_id=ctx.deps.run_id,
                question_id=question_id,
                reason="answered",
            )
            completed_record = await repo.mark_completed_async(question_id)
            if completed_record is None:
                raise KeyError(f"Unknown user question: {question_id}")
            return await _build_question_result_async(
                ctx=ctx,
                record=completed_record.model_copy(
                    update={
                        "status": UserQuestionRequestStatus.ANSWERED,
                        "answers": answers.answers,
                    }
                ),
            )

        return await execute_tool_call(
            ctx,
            tool_name="ask_question",
            args_summary={"question_count": len(questions)},
            action=_action,
            raw_args=locals(),
        )


async def _publish_user_question_event(
    *,
    ctx: ToolContext,
    event_type: RunEventType,
    payload: dict[str, JsonValue],
) -> None:
    await publish_run_event_async(
        ctx.deps.run_event_hub,
        RunEvent(
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            event_type=event_type,
            payload_json=dumps(payload, ensure_ascii=False),
        ),
    )


async def _set_runtime_phase_async(ctx: ToolContext, *, phase: RunRuntimePhase) -> None:
    runtime = await ctx.deps.run_runtime_repo.ensure_async(
        run_id=ctx.deps.run_id,
        session_id=ctx.deps.session_id,
        root_task_id=ctx.deps.task_id,
    )
    if runtime.status in {
        RunRuntimeStatus.STOPPING,
        RunRuntimeStatus.STOPPED,
    }:
        return
    await ctx.deps.run_runtime_repo.update_async(
        ctx.deps.run_id,
        status=(
            RunRuntimeStatus.PAUSED
            if phase == RunRuntimePhase.AWAITING_MANUAL_ACTION
            else RunRuntimeStatus.RUNNING
        ),
        phase=phase,
        active_instance_id=ctx.deps.instance_id,
        active_task_id=ctx.deps.task_id,
        active_role_id=ctx.deps.role_id,
        active_subagent_instance_id=(
            None
            if ctx.deps.role_registry.is_coordinator_role(ctx.deps.role_id)
            else ctx.deps.instance_id
        ),
        last_error=None,
    )


def _set_runtime_phase(ctx: ToolContext, *, phase: RunRuntimePhase) -> None:
    runtime = ctx.deps.run_runtime_repo.ensure(
        run_id=ctx.deps.run_id,
        session_id=ctx.deps.session_id,
        root_task_id=ctx.deps.task_id,
    )
    if runtime.status in {
        RunRuntimeStatus.STOPPING,
        RunRuntimeStatus.STOPPED,
    }:
        return
    ctx.deps.run_runtime_repo.update(
        ctx.deps.run_id,
        status=(
            RunRuntimeStatus.PAUSED
            if phase == RunRuntimePhase.AWAITING_MANUAL_ACTION
            else RunRuntimeStatus.RUNNING
        ),
        phase=phase,
        active_instance_id=ctx.deps.instance_id,
        active_task_id=ctx.deps.task_id,
        active_role_id=ctx.deps.role_id,
        active_subagent_instance_id=(
            None
            if ctx.deps.role_registry.is_coordinator_role(ctx.deps.role_id)
            else ctx.deps.instance_id
        ),
        last_error=None,
    )


def _running_phase(ctx: ToolContext) -> RunRuntimePhase:
    if ctx.deps.role_registry.is_coordinator_role(ctx.deps.role_id):
        return RunRuntimePhase.COORDINATOR_RUNNING
    return RunRuntimePhase.SUBAGENT_RUNNING


async def _build_question_result_async(
    *,
    ctx: ToolContext,
    record: UserQuestionRequestRecord,
) -> ToolResultProjection:
    if record.status == UserQuestionRequestStatus.ANSWERED:
        await _set_runtime_phase_async(ctx, phase=_running_phase(ctx))
        answers_payload: list[JsonValue] = [
            answer.model_dump(mode="json") for answer in record.answers
        ]
        payload: dict[str, JsonValue] = {
            "status": "answered",
            "question_id": record.question_id,
            "answers": answers_payload,
        }
        return ToolResultProjection(
            visible_data=payload,
            internal_data=payload,
        )
    if record.status == UserQuestionRequestStatus.TIMED_OUT:
        await _set_runtime_phase_async(ctx, phase=_running_phase(ctx))
        payload = {
            "status": "timed_out",
            "question_id": record.question_id,
        }
        return ToolResultProjection(
            visible_data=payload,
            internal_data=payload,
        )
    payload = {
        "status": "completed",
        "question_id": record.question_id,
    }
    return ToolResultProjection(
        visible_data=payload,
        internal_data=payload,
    )


async def _resolve_closed_question(
    *,
    repo: UserQuestionRepository,
    question_id: str,
) -> UserQuestionRequestRecord:
    resolved_record = None
    try:
        resolved_record = await repo.resolve_async(
            question_id=question_id,
            status=UserQuestionRequestStatus.COMPLETED,
            expected_status=UserQuestionRequestStatus.REQUESTED,
        )
    except UserQuestionStatusConflictError:
        resolved_record = await repo.get_async(question_id)
    if resolved_record is None:
        raise KeyError(f"Unknown user question: {question_id}")
    return resolved_record
