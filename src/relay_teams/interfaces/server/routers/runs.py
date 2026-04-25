from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Annotated, ClassVar, Literal, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from relay_teams.interfaces.server.deps import get_run_service, get_skill_registry
from relay_teams.interfaces.server.router_error_mapping import http_exception_for
from relay_teams.logger import get_logger, log_event
from relay_teams.media import (
    ContentPart,
    InlineMediaContentPart,
    MediaRefContentPart,
)
from relay_teams.monitors import MonitorActionType, MonitorRule, MonitorSourceKind
from relay_teams.sessions.runs.run_service import SessionRunService
from relay_teams.sessions.runs.enums import ExecutionMode, InjectionSource
from relay_teams.sessions.runs.user_question_models import (
    UserQuestionAnswerSubmission,
)
from relay_teams.sessions.runs.run_models import (
    IntentInput,
    MediaGenerationConfig,
    RunKind,
    RunThinkingConfig,
)
from relay_teams.skills import SkillRegistry
from relay_teams.trace import bind_trace_context
from relay_teams.validation import (
    OptionalIdentifierStr,
    RequiredIdentifierStr,
    normalize_identifier_tuple,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/runs", tags=["Runs"])


def _reuse_normalized_inline_media_refs(
    *,
    raw_input: tuple[ContentPart, ...],
    normalized_input: tuple[ContentPart, ...],
    display_input: tuple[ContentPart, ...],
) -> tuple[ContentPart, ...]:
    normalized_refs: list[tuple[InlineMediaContentPart, MediaRefContentPart]] = []
    for raw_part, normalized_part in zip(raw_input, normalized_input, strict=False):
        if isinstance(raw_part, InlineMediaContentPart) and isinstance(
            normalized_part, MediaRefContentPart
        ):
            normalized_refs.append((raw_part, normalized_part))
    if not normalized_refs:
        return display_input

    reused_parts: list[ContentPart] = []
    for part in display_input:
        if isinstance(part, InlineMediaContentPart):
            replacement = _find_normalized_media_ref(part, normalized_refs)
            if replacement is not None:
                reused_parts.append(replacement)
                continue
        reused_parts.append(part)
    return tuple(reused_parts)


def _find_normalized_media_ref(
    part: InlineMediaContentPart,
    normalized_refs: list[tuple[InlineMediaContentPart, MediaRefContentPart]],
) -> MediaRefContentPart | None:
    for raw_part, normalized_part in normalized_refs:
        if part == raw_part:
            return normalized_part
    return None


def _contains_inline_media(parts: tuple[ContentPart, ...]) -> bool:
    return any(isinstance(part, InlineMediaContentPart) for part in parts)


class CreateRunRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    session_id: RequiredIdentifierStr
    input: tuple[ContentPart, ...] = Field(default_factory=tuple)
    display_input: tuple[ContentPart, ...] = Field(default_factory=tuple)
    run_kind: RunKind = RunKind.CONVERSATION
    generation_config: MediaGenerationConfig | None = Field(default=None)
    execution_mode: ExecutionMode = ExecutionMode.AI
    yolo: bool = False
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)
    target_role_id: OptionalIdentifierStr = None
    skills: Optional[tuple[str, ...]] = None

    @field_validator("skills", mode="before")
    @classmethod
    def _normalize_skills(cls, value: object) -> Optional[tuple[str, ...]]:
        return normalize_identifier_tuple(value, field_name="skills")


class CreateRunResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    run_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    target_role_id: OptionalIdentifierStr = None


class InjectMessageRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    source: InjectionSource = InjectionSource.USER
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def _reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Injection content must not be empty")
        return value


class ResolveToolApprovalRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    action: Literal[
        "approve",
        "approve_once",
        "approve_exact",
        "approve_prefix",
        "deny",
    ]
    feedback: str = ""


class AnswerUserQuestionRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    answers: tuple[dict[str, object], ...] = Field(min_length=1)


class StopRunRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    scope: Literal["main", "subagent"] = "main"
    instance_id: OptionalIdentifierStr = None


class InjectSubagentRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def _reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Injection content must not be empty")
        return value


class StopBackgroundTaskResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    background_task: dict[str, object]


class CreateMonitorRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    source_kind: MonitorSourceKind = MonitorSourceKind.BACKGROUND_TASK
    source_key: str = Field(min_length=1)
    event_names: tuple[str, ...] = ("background_task.line",)
    patterns: tuple[str, ...] = ()
    action_type: MonitorActionType = MonitorActionType.WAKE_INSTANCE
    cooldown_seconds: int = Field(default=0, ge=0)
    max_triggers: int | None = Field(default=None, ge=1)
    auto_stop_on_first_match: bool = False
    case_sensitive: bool = False


class MonitorResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    monitor: dict[str, object]


@router.post(
    "",
    response_model=CreateRunResponse,
    response_model_exclude_none=True,
)
async def create_run(
    request: Request,
    req: CreateRunRequest,
    service: Annotated[SessionRunService, Depends(get_run_service)],
    skill_registry: Annotated[SkillRegistry, Depends(get_skill_registry)],
) -> CreateRunResponse:
    started = time.perf_counter()
    try:
        normalized_input = req.input
        normalized_display_input = req.display_input
        has_inline_media = any(
            isinstance(part, InlineMediaContentPart)
            for part in (*req.input, *req.display_input)
        )
        if has_inline_media:
            container = getattr(request.app.state, "container", None)
            if container is None:
                raise HTTPException(
                    status_code=503,
                    detail="Media uploads require the server container to be initialized",
                )
            session = container.session_service.get_session(req.session_id)
            normalized_input = container.media_asset_service.normalize_content_parts(
                session_id=req.session_id,
                workspace_id=session.workspace_id,
                parts=req.input,
            )
            if req.display_input:
                display_input = _reuse_normalized_inline_media_refs(
                    raw_input=req.input,
                    normalized_input=normalized_input,
                    display_input=req.display_input,
                )
                normalized_display_input = display_input
                if _contains_inline_media(display_input):
                    normalized_display_input = (
                        container.media_asset_service.normalize_content_parts(
                            session_id=req.session_id,
                            workspace_id=session.workspace_id,
                            parts=display_input,
                        )
                    )
        if not normalized_input:
            raise HTTPException(status_code=400, detail="Run input cannot be empty")
        resolved_skills = None
        if req.skills is not None:
            resolved_skills = skill_registry.resolve_known(
                tuple(req.skills),
                strict=True,
                consumer="interfaces.server.routers.runs.create_run",
            )
        intent_input = IntentInput(
            session_id=req.session_id,
            input=normalized_input,
            display_input=normalized_display_input,
            run_kind=req.run_kind,
            generation_config=req.generation_config,
            execution_mode=req.execution_mode,
            yolo=req.yolo,
            thinking=req.thinking,
            target_role_id=req.target_role_id,
            skills=resolved_skills,
        )

        def create_and_start_run() -> tuple[str, str]:
            created_run_id, created_session_id = service.create_run(intent_input)
            service.ensure_run_started(created_run_id)
            return created_run_id, created_session_id

        run_id, session_id = await asyncio.to_thread(create_and_start_run)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=session_id):
            log_event(
                logger,
                logging.INFO,
                event="run.created",
                message="Run created",
                duration_ms=elapsed_ms,
                payload={
                    "execution_mode": req.execution_mode.value,
                    "yolo": req.yolo,
                },
            )
        return CreateRunResponse(
            run_id=run_id,
            session_id=session_id,
            target_role_id=req.target_role_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        log_event(
            logger,
            logging.WARNING,
            event="run.create.conflict",
            message="Failed to create run due to runtime conflict",
            payload={"session_id": req.session_id},
            exc_info=exc,
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{run_id}/events")
async def stream_run_events(
    run_id: RequiredIdentifierStr,
    service: Annotated[SessionRunService, Depends(get_run_service)],
    after_event_id: int = 0,
) -> StreamingResponse:
    async def event_generator():
        event_count = 0
        started = time.perf_counter()
        with bind_trace_context(trace_id=run_id, run_id=run_id):
            log_event(
                logger,
                logging.INFO,
                event="stream.opened",
                message="Run event stream opened",
                payload={"after_event_id": after_event_id},
            )
            try:
                async for event in service.stream_run_events(
                    run_id, after_event_id=after_event_id
                ):
                    event_count += 1
                    yield f"data: {event.model_dump_json()}\n\n"
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                log_event(
                    logger,
                    logging.INFO,
                    event="stream.closed",
                    message="Run event stream closed",
                    duration_ms=elapsed_ms,
                    payload={"event_count": event_count},
                )
            except KeyError as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    event="stream.not_found",
                    message="Run not found during stream start",
                    exc_info=exc,
                )
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            except Exception as exc:  # pragma: no cover - defensive path
                log_event(
                    logger,
                    logging.ERROR,
                    event="stream.failed",
                    message="Unexpected stream failure",
                    payload={"event_count": event_count},
                    exc_info=exc,
                )
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/{run_id}/monitors")
async def list_monitors(
    run_id: RequiredIdentifierStr,
    service: Annotated[SessionRunService, Depends(get_run_service)],
) -> dict[str, object]:
    try:
        monitors = await asyncio.to_thread(service.list_monitors, run_id)
        return {"items": list(monitors)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        status_code = 503 if "not configured" in str(exc).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post("/{run_id}/monitors", response_model=MonitorResponse)
async def create_monitor(
    run_id: RequiredIdentifierStr,
    req: CreateMonitorRequest,
    service: Annotated[SessionRunService, Depends(get_run_service)],
) -> MonitorResponse:
    try:
        monitor = await asyncio.to_thread(
            service.create_monitor,
            run_id=run_id,
            source_kind=req.source_kind,
            source_key=req.source_key,
            rule=MonitorRule(
                event_names=req.event_names,
                text_patterns_any=req.patterns,
                cooldown_seconds=req.cooldown_seconds,
                max_triggers=req.max_triggers,
                auto_stop_on_first_match=req.auto_stop_on_first_match,
                case_sensitive=req.case_sensitive,
            ),
            action_type=req.action_type,
        )
        return MonitorResponse(monitor=monitor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        status_code = 503 if "not configured" in str(exc).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post("/{run_id}/monitors/{monitor_id}:stop", response_model=MonitorResponse)
async def stop_monitor(
    run_id: RequiredIdentifierStr,
    monitor_id: RequiredIdentifierStr,
    service: Annotated[SessionRunService, Depends(get_run_service)],
) -> MonitorResponse:
    try:
        monitor = await asyncio.to_thread(
            service.stop_monitor,
            run_id=run_id,
            monitor_id=monitor_id,
        )
        return MonitorResponse(monitor=monitor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        status_code = 503 if "not configured" in str(exc).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post("/{run_id}/inject")
async def inject_message(
    run_id: RequiredIdentifierStr,
    req: InjectMessageRequest,
    service: Annotated[SessionRunService, Depends(get_run_service)],
) -> dict[str, object]:
    try:
        result = await asyncio.to_thread(
            service.inject_message,
            run_id=run_id,
            source=req.source,
            content=req.content,
        )
        with bind_trace_context(trace_id=run_id, run_id=run_id):
            log_event(
                logger,
                logging.INFO,
                event="run.message.injected",
                message="Message injected to running agents",
                payload={"source": req.source.value, "length": len(req.content)},
            )
        return result.model_dump()
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 400),),
        ) from exc


@router.get("/{run_id}/tool-approvals")
async def list_tool_approvals(
    run_id: RequiredIdentifierStr,
    service: Annotated[SessionRunService, Depends(get_run_service)],
) -> list[dict[str, str]]:
    with bind_trace_context(trace_id=run_id, run_id=run_id):
        result = await asyncio.to_thread(service.list_open_tool_approvals, run_id)
        log_event(
            logger,
            logging.INFO,
            event="tool.approval.listed",
            message="Listed open tool approvals",
            payload={"count": len(result)},
        )
        return result


@router.get("/{run_id}/questions")
async def list_user_questions(
    run_id: RequiredIdentifierStr,
    service: Annotated[SessionRunService, Depends(get_run_service)],
) -> list[dict[str, object]]:
    try:
        questions = await asyncio.to_thread(service.list_user_questions, run_id)
        return cast(list[dict[str, object]], questions)
    except KeyError as exc:
        raise http_exception_for(exc) from exc


@router.post("/{run_id}/questions/{question_id}:answer")
async def answer_user_question(
    run_id: RequiredIdentifierStr,
    question_id: RequiredIdentifierStr,
    req: AnswerUserQuestionRequest,
    service: Annotated[SessionRunService, Depends(get_run_service)],
) -> dict[str, object]:
    try:
        submission = UserQuestionAnswerSubmission.model_validate(
            {"answers": list(req.answers)}
        )
        result = await asyncio.to_thread(
            service.answer_user_question,
            run_id=run_id,
            question_id=question_id,
            answers=submission,
        )
        with bind_trace_context(
            trace_id=run_id,
            run_id=run_id,
            tool_call_id=question_id,
        ):
            log_event(
                logger,
                logging.INFO,
                event="user.question.answered",
                message="User question answered",
                payload={"answer_count": len(submission.answers)},
            )
        return cast(dict[str, object], result)
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 400),),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{run_id}/background-tasks")
async def list_background_tasks(
    run_id: RequiredIdentifierStr,
    service: Annotated[SessionRunService, Depends(get_run_service)],
) -> dict[str, object]:
    try:
        background_tasks = await asyncio.to_thread(
            service.list_background_tasks, run_id
        )
        return {"items": list(background_tasks)}
    except KeyError as exc:
        raise http_exception_for(exc) from exc


@router.get("/{run_id}/todo")
async def get_todo(
    run_id: RequiredIdentifierStr,
    service: Annotated[SessionRunService, Depends(get_run_service)],
) -> dict[str, object]:
    try:
        todo = await asyncio.to_thread(service.get_todo, run_id)
        return {"todo": todo}
    except KeyError as exc:
        raise http_exception_for(exc) from exc


@router.get("/{run_id}/background-tasks/{background_task_id}")
async def get_background_task(
    run_id: RequiredIdentifierStr,
    background_task_id: RequiredIdentifierStr,
    service: Annotated[SessionRunService, Depends(get_run_service)],
) -> dict[str, object]:
    try:
        background_task = await asyncio.to_thread(
            service.get_background_task,
            run_id=run_id,
            background_task_id=background_task_id,
        )
        return {"background_task": background_task}
    except KeyError as exc:
        raise http_exception_for(exc) from exc


@router.post(
    "/{run_id}/background-tasks/{background_task_id}:stop",
    response_model=StopBackgroundTaskResponse,
)
async def stop_background_task(
    run_id: RequiredIdentifierStr,
    background_task_id: RequiredIdentifierStr,
    service: Annotated[SessionRunService, Depends(get_run_service)],
) -> StopBackgroundTaskResponse:
    try:
        result = await service.stop_background_task(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        return StopBackgroundTaskResponse(background_task=result)
    except KeyError as exc:
        raise http_exception_for(exc) from exc


@router.post("/{run_id}/tool-approvals/{tool_call_id}/resolve")
async def resolve_tool_approval(
    run_id: RequiredIdentifierStr,
    tool_call_id: RequiredIdentifierStr,
    req: ResolveToolApprovalRequest,
    service: Annotated[SessionRunService, Depends(get_run_service)],
) -> dict[str, str]:
    try:
        await asyncio.to_thread(
            service.resolve_tool_approval,
            run_id=run_id,
            tool_call_id=tool_call_id,
            action=req.action,
            feedback=req.feedback,
        )
        with bind_trace_context(
            trace_id=run_id, run_id=run_id, tool_call_id=tool_call_id
        ):
            log_event(
                logger,
                logging.INFO,
                event="tool.approval.resolved",
                message="Tool approval resolved",
                payload={"action": req.action, "feedback_length": len(req.feedback)},
            )
        return {"status": "ok", "action": req.action}
    except KeyError as exc:
        raise http_exception_for(exc) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{run_id}/stop")
async def stop_run(
    run_id: RequiredIdentifierStr,
    req: StopRunRequest,
    service: Annotated[SessionRunService, Depends(get_run_service)],
) -> dict[str, str]:
    try:
        if req.scope == "main":
            await asyncio.to_thread(service.stop_run, run_id)
            with bind_trace_context(trace_id=run_id, run_id=run_id):
                log_event(
                    logger,
                    logging.WARNING,
                    event="run.stopped",
                    message="Run stop requested",
                )
            return {"status": "ok", "scope": "main"}
        if not req.instance_id:
            raise HTTPException(
                status_code=422,
                detail="instance_id is required when scope is subagent",
            )
        payload = await asyncio.to_thread(
            service.stop_subagent,
            run_id,
            req.instance_id,
        )
        with bind_trace_context(
            trace_id=run_id, run_id=run_id, instance_id=req.instance_id
        ):
            log_event(
                logger,
                logging.WARNING,
                event="subagent.stopped",
                message="Subagent stop requested",
            )
        return {
            "status": "ok",
            "scope": "subagent",
            "instance_id": payload["instance_id"],
        }
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 400),),
        ) from exc


@router.post("/{run_id}:resume")
async def resume_run(
    run_id: RequiredIdentifierStr,
    service: Annotated[SessionRunService, Depends(get_run_service)],
) -> dict[str, str]:
    try:

        def resume_and_start_run() -> str:
            resumed_session_id = service.resume_run(run_id)
            service.ensure_run_started(run_id)
            return resumed_session_id

        session_id = await asyncio.to_thread(resume_and_start_run)
        with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=session_id):
            log_event(
                logger,
                logging.INFO,
                event="run.resume.requested",
                message="Run resume requested",
            )
        return {"status": "ok", "run_id": run_id, "session_id": session_id}
    except (KeyError, RuntimeError) as exc:
        raise http_exception_for(
            exc,
            mappings=((RuntimeError, 409),),
        ) from exc


@router.post("/{run_id}/subagents/{instance_id}/inject")
async def inject_subagent(
    run_id: RequiredIdentifierStr,
    instance_id: RequiredIdentifierStr,
    req: InjectSubagentRequest,
    service: Annotated[SessionRunService, Depends(get_run_service)],
) -> dict[str, str]:
    try:
        await asyncio.to_thread(
            service.inject_subagent_message,
            run_id=run_id,
            instance_id=instance_id,
            content=req.content,
        )
        with bind_trace_context(
            trace_id=run_id, run_id=run_id, instance_id=instance_id
        ):
            log_event(
                logger,
                logging.INFO,
                event="subagent.message.injected",
                message="Subagent follow-up message injected",
                payload={"length": len(req.content)},
            )
        return {"status": "ok", "instance_id": instance_id}
    except (KeyError, RuntimeError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((RuntimeError, 409), (ValueError, 400)),
        ) from exc
