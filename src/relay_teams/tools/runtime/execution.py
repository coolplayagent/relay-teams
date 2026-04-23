# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, JsonValue

import asyncio
import inspect
import json
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable, Mapping
from enum import Enum
from json import dumps
from typing import Protocol, cast, get_args, get_origin, get_type_hints
from uuid import uuid4

from relay_teams.logger import get_logger, log_event, log_tool_error
from relay_teams.metrics.adapters import record_tool_execution
from relay_teams.notifications import NotificationContext, NotificationType
from relay_teams.persistence import is_retryable_sqlite_error
from relay_teams.agents.instances.models import RuntimeToolsSnapshot
from relay_teams.agents.tasks.task_status_sanitizer import (
    sanitize_task_status_payload,
)
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.run_models import RunEvent

from relay_teams.tools.runtime.approval_ticket_repo import (
    ApprovalTicketRecord,
    ApprovalTicketStatus,
    ApprovalTicketStatusConflictError,
)
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimePhase, RunRuntimeStatus
from relay_teams.trace import trace_span
from relay_teams.tools.runtime.context import ToolContext
from relay_teams.tools.runtime.models import (
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolError,
    ToolExecutionError,
    ToolInternalRecord,
    ToolResultEnvelope,
    ToolResultProjection,
)
from relay_teams.tools.runtime.policy import ToolApprovalPolicy
from relay_teams.tools.runtime.persisted_state import (
    ToolApprovalMode,
    ToolApprovalStatus,
    ToolExecutionStatus,
    merge_tool_call_state,
)
from relay_teams.tools.runtime_activation import merge_active_tools
from relay_teams.env.hook_runtime_env import (
    reset_tool_hook_runtime_env,
    set_tool_hook_runtime_env,
)
from relay_teams.hooks import (
    HookDecisionBundle,
    HookDecisionType,
    HookEventName,
    PermissionRequestInput,
    PostToolUseFailureInput,
    PostToolUseInput,
    PreToolUseInput,
)

LOGGER = get_logger(__name__)


async def execute_tool(
    ctx: ToolContext,
    *,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    action: Callable[[dict[str, JsonValue]], object | Awaitable[object]]
    | Callable[[], object | Awaitable[object]]
    | object,
    tool_input: dict[str, JsonValue] | None = None,
    approval_request: ToolApprovalRequest | None = None,
    approval_request_factory: Callable[
        [dict[str, JsonValue]], ToolApprovalRequest | None
    ]
    | None = None,
    approval_args_summary: dict[str, JsonValue] | None = None,
    approval_args_summary_factory: Callable[
        [dict[str, JsonValue]], dict[str, JsonValue] | None
    ]
    | None = None,
    keep_approval_ticket_reusable: bool = False,
) -> dict[str, JsonValue]:
    """Run a tool action with approval, logging, and normalized envelopes."""
    tool_call_id = ctx.tool_call_id or f"toolcall_{uuid4().hex[:12]}"
    with trace_span(
        LOGGER,
        component="tools.runtime",
        operation="execute_tool",
        attributes={"tool_name": tool_name},
        trace_id=ctx.deps.trace_id,
        run_id=ctx.deps.run_id,
        task_id=ctx.deps.task_id,
        session_id=ctx.deps.session_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        tool_call_id=tool_call_id,
    ):
        started = time.perf_counter()
        log_event(
            LOGGER,
            logging.DEBUG,
            event="tool.call.started",
            message="Tool call started",
            payload={
                "tool_name": tool_name,
                "args": args_summary,
                "instance_id": ctx.deps.instance_id,
                "role_id": ctx.deps.role_id,
            },
        )

        meta: dict[str, JsonValue] = {}
        effective_tool_input = dict(args_summary if tool_input is None else tool_input)
        _raise_if_stopped(ctx)
        activation_error = _runtime_activation_error(ctx=ctx, tool_name=tool_name)
        if activation_error is not None:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            meta["duration_ms"] = elapsed_ms
            meta["tool_result_event_published"] = True
            envelope = _visible_envelope(
                ok=False,
                error=activation_error,
                meta=meta,
            )
            _persist_tool_record(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args_summary=args_summary,
                visible_envelope=envelope,
                internal_data=None,
                runtime_meta=meta,
                execution_status=ToolExecutionStatus.FAILED,
            )
            _record_tool_metrics(
                ctx=ctx,
                tool_name=tool_name,
                duration_ms=elapsed_ms,
                success=False,
            )
            _publish_tool_result_event(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                visible_envelope=envelope,
            )
            return envelope
        force_approval = False
        (
            effective_tool_input,
            pre_tool_error,
            force_approval,
        ) = await _apply_pre_tool_hooks(
            ctx=ctx,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_input=effective_tool_input,
        )
        args_summary = dict(effective_tool_input)
        resolved_approval_request = (
            approval_request_factory(effective_tool_input)
            if approval_request_factory is not None
            else approval_request
        )
        resolved_approval_args_summary = (
            approval_args_summary_factory(effective_tool_input)
            if approval_args_summary_factory is not None
            else approval_args_summary
        )
        if pre_tool_error is not None:
            approval_ticket_id = None
            approval_error = pre_tool_error
        else:
            approval_ticket_id, approval_error = await _handle_tool_approval(
                ctx=ctx,
                tool_name=tool_name,
                args_summary=args_summary,
                approval_args_summary=resolved_approval_args_summary,
                meta=meta,
                tool_call_id=tool_call_id,
                approval_request=resolved_approval_request,
                force_approval=force_approval,
            )
        if approval_error is not None:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            meta["duration_ms"] = elapsed_ms
            meta["tool_result_event_published"] = True
            envelope = _visible_envelope(
                ok=False,
                error=approval_error,
                meta=meta,
            )
            _persist_tool_record(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args_summary=args_summary,
                visible_envelope=envelope,
                internal_data=None,
                runtime_meta=meta,
                execution_status=ToolExecutionStatus.FAILED,
            )
            _record_tool_metrics(
                ctx=ctx,
                tool_name=tool_name,
                duration_ms=elapsed_ms,
                success=False,
            )
            _publish_tool_result_event(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                visible_envelope=envelope,
            )
            return envelope

        ctx.deps.run_runtime_repo.ensure(
            run_id=ctx.deps.run_id,
            session_id=ctx.deps.session_id,
            root_task_id=ctx.deps.task_id,
        )
        ctx.deps.run_runtime_repo.update(
            ctx.deps.run_id,
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.COORDINATOR_RUNNING
            if ctx.deps.role_registry.is_coordinator_role(ctx.deps.role_id)
            else RunRuntimePhase.SUBAGENT_RUNNING,
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

        try:
            _raise_if_stopped(ctx)
            hook_env_token = set_tool_hook_runtime_env(ctx.deps.hook_runtime_env)
            try:
                result = _invoke_tool_action(
                    action=action,
                    tool_input=effective_tool_input,
                )
                if inspect.isawaitable(result):
                    result = await result
            finally:
                reset_tool_hook_runtime_env(hook_env_token)
            _raise_if_stopped(ctx)
            visible_data, internal_data = _normalize_result_payload(result)

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            meta["duration_ms"] = elapsed_ms

            log_event(
                LOGGER,
                logging.DEBUG,
                event="tool.call.completed",
                message="Tool call completed",
                duration_ms=elapsed_ms,
                payload={"tool_name": tool_name},
            )

            meta["tool_result_event_published"] = True
            envelope = _visible_envelope(
                ok=True,
                data=visible_data,
                meta=meta,
            )
            envelope = await _apply_post_tool_hooks(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                args_summary=args_summary,
                envelope=envelope,
            )
            _persist_tool_record(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args_summary=args_summary,
                visible_envelope=envelope,
                internal_data=internal_data,
                runtime_meta=meta,
                execution_status=ToolExecutionStatus.COMPLETED,
            )
            _record_tool_metrics(
                ctx=ctx,
                tool_name=tool_name,
                duration_ms=elapsed_ms,
                success=True,
            )
            _publish_tool_result_event(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                visible_envelope=envelope,
            )
            if approval_ticket_id and not keep_approval_ticket_reusable:
                ctx.deps.approval_ticket_repo.mark_completed(approval_ticket_id)
            return envelope
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            meta["duration_ms"] = elapsed_ms
            error = _error_payload(exc)
            if error.details:
                meta["error_details"] = dict(error.details)

            compact = json.dumps(
                {
                    "tool": tool_name,
                    "type": error.type,
                    "message": error.message,
                    "details": error.details,
                },
                ensure_ascii=False,
            )
            log_tool_error(ctx.deps.role_id, compact)
            log_event(
                LOGGER,
                logging.ERROR,
                event="tool.call.failed",
                message="Tool call failed",
                duration_ms=elapsed_ms,
                payload={
                    "tool_name": tool_name,
                    "error_type": error.type,
                    "retryable": error.retryable,
                    "details": error.details,
                },
            )
            meta["tool_result_event_published"] = True
            envelope = _visible_envelope(
                ok=False,
                error=error,
                meta=meta,
            )
            envelope = await _apply_post_tool_failure_hooks(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                args_summary=args_summary,
                envelope=envelope,
            )
            _persist_tool_record(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args_summary=args_summary,
                visible_envelope=envelope,
                internal_data=None,
                runtime_meta=meta,
                execution_status=ToolExecutionStatus.FAILED,
            )
            _record_tool_metrics(
                ctx=ctx,
                tool_name=tool_name,
                duration_ms=elapsed_ms,
                success=False,
            )
            _publish_tool_result_event(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                visible_envelope=envelope,
            )
            if approval_ticket_id and not keep_approval_ticket_reusable:
                ctx.deps.approval_ticket_repo.mark_completed(approval_ticket_id)
            return envelope


async def execute_tool_call(
    ctx: ToolContext,
    *,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    action: Callable[..., object | Awaitable[object]] | object,
    raw_args: Mapping[str, object] | None = None,
    args_exclude: tuple[str, ...] = ("ctx",),
    approval_request: ToolApprovalRequest | None = None,
    approval_request_factory: Callable[
        [dict[str, JsonValue]], ToolApprovalRequest | None
    ]
    | None = None,
    approval_args_summary: dict[str, JsonValue] | None = None,
    approval_args_summary_factory: Callable[
        [dict[str, JsonValue]], dict[str, JsonValue] | None
    ]
    | None = None,
    keep_approval_ticket_reusable: bool = False,
) -> dict[str, JsonValue]:
    """Run a tool through the hook-aware runtime using natural Python params.

    Tool authors should prefer this wrapper for new tools:
    - keep ``action`` as a normal callable with named parameters that match the tool
    - pass ``raw_args=locals()`` from the tool body so hooks can rewrite the live input
    - keep ``args_summary`` limited to approval and observability data, not full payloads

    ``execute_tool()`` remains available for compatibility, but ``execute_tool_call()``
    is the default authoring path because it centralizes hook input capture, argument
    binding, and runtime env propagation.
    """
    tool_input = (
        None
        if raw_args is None
        else _capture_tool_input(
            raw_args=raw_args,
            action=action,
            exclude=args_exclude,
        )
    )
    return await execute_tool(
        ctx,
        tool_name=tool_name,
        args_summary=args_summary,
        action=action,
        tool_input=tool_input,
        approval_request=approval_request,
        approval_request_factory=approval_request_factory,
        approval_args_summary=approval_args_summary,
        approval_args_summary_factory=approval_args_summary_factory,
        keep_approval_ticket_reusable=keep_approval_ticket_reusable,
    )


def _runtime_activation_error(
    *,
    ctx: ToolContext,
    tool_name: str,
) -> ToolError | None:
    try:
        runtime_record = ctx.deps.agent_repo.get_instance(ctx.deps.instance_id)
    except AttributeError:
        return ToolError(
            type="internal_error",
            message=(
                "Runtime tool activation policy is unavailable because the "
                "agent instance repository contract is misconfigured."
            ),
            retryable=False,
        )
    except KeyError:
        return None
    runtime_tools = _parse_runtime_tools_snapshot(runtime_record.runtime_tools_json)
    authorized_local_tools = tuple(entry.name for entry in runtime_tools.local_tools)
    if tool_name not in authorized_local_tools:
        return None
    active_local_tools = _resolve_runtime_active_local_tools(
        authorized_local_tools=authorized_local_tools,
        runtime_active_tools_json=runtime_record.runtime_active_tools_json,
    )
    if tool_name in active_local_tools:
        return None
    discovery_authorized = {
        "tool_search",
        "activate_tools",
    }.issubset(set(authorized_local_tools))
    message = (
        f"Tool `{tool_name}` is authorized for this runtime but is currently "
        "deferred. Use `tool_search` to inspect it and `activate_tools` before "
        "retrying."
        if discovery_authorized
        else f"Tool `{tool_name}` is authorized for this runtime but is not active."
    )
    return ToolError(
        type="validation_error",
        message=message,
        retryable=False,
    )


def _parse_runtime_tools_snapshot(raw_snapshot: str) -> RuntimeToolsSnapshot:
    normalized_snapshot = raw_snapshot.strip()
    if not normalized_snapshot:
        return RuntimeToolsSnapshot()
    try:
        return RuntimeToolsSnapshot.model_validate_json(normalized_snapshot)
    except (ValueError, TypeError):
        return RuntimeToolsSnapshot()


def _parse_runtime_active_tools_json(raw_active_tools: str) -> tuple[str, ...]:
    normalized_active_tools = raw_active_tools.strip()
    if not normalized_active_tools:
        return ()
    try:
        parsed = json.loads(normalized_active_tools)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(item for item in parsed if isinstance(item, str))


def _resolve_runtime_active_local_tools(
    *,
    authorized_local_tools: tuple[str, ...],
    runtime_active_tools_json: str,
) -> tuple[str, ...]:
    return merge_active_tools(
        authorized_tools=authorized_local_tools,
        active_tools=_parse_runtime_active_tools_json(runtime_active_tools_json),
    )


def _record_tool_metrics(
    *,
    ctx: ToolContext,
    tool_name: str,
    duration_ms: int,
    success: bool,
) -> None:
    metric_recorder = getattr(ctx.deps, "metric_recorder", None)
    mcp_registry = getattr(ctx.deps, "mcp_registry", None)
    if metric_recorder is None or mcp_registry is None:
        return
    record_tool_execution(
        metric_recorder,
        mcp_registry=mcp_registry,
        workspace_id=ctx.deps.workspace_id,
        session_id=ctx.deps.session_id,
        run_id=ctx.deps.run_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        tool_name=tool_name,
        duration_ms=duration_ms,
        success=success,
    )


def _publish_tool_result_event(
    *,
    ctx: ToolContext,
    tool_call_id: str,
    tool_name: str,
    visible_envelope: dict[str, JsonValue],
) -> None:
    result_payload = cast(
        JsonValue,
        sanitize_task_status_payload(visible_envelope),
    )
    is_error = bool(visible_envelope.get("ok") is False)
    ctx.deps.run_event_hub.publish(
        RunEvent(
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            event_type=RunEventType.TOOL_RESULT,
            payload_json=dumps(
                {
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "result": result_payload,
                    "error": is_error,
                    "role_id": ctx.deps.role_id,
                    "instance_id": ctx.deps.instance_id,
                }
            ),
        )
    )


def _error_payload(exc: Exception) -> ToolError:
    if isinstance(exc, ToolExecutionError):
        return ToolError(
            type=exc.error_type,
            message=str(exc) or exc.__class__.__name__,
            retryable=exc.retryable,
            details=exc.details,
        )

    err_type = "internal_error"
    retryable = False
    message = str(exc) or exc.__class__.__name__

    if isinstance(exc, ValueError):
        err_type = "validation_error"
        retryable = False
    elif isinstance(exc, KeyError):
        err_type = "not_found"
        retryable = True
    elif isinstance(exc, PermissionError):
        err_type = "permission_error"
        retryable = True
    elif isinstance(exc, sqlite3.OperationalError) and is_retryable_sqlite_error(exc):
        retryable = True

    return ToolError(
        type=err_type,
        message=message,
        retryable=retryable,
    )


def _normalize_result_payload(
    result: object,
) -> tuple[JsonValue | None, JsonValue | None]:
    if isinstance(result, ToolResultProjection):
        return (
            _normalize_json_value(result.visible_data),
            _normalize_json_value(result.internal_data),
        )
    normalized = _normalize_json_value(result)
    return normalized, normalized


def _safe_json(value: object) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    if len(text) > 500:
        return text[:500] + "...(truncated)"
    return text


def _normalize_json_value(value: object) -> JsonValue:
    if isinstance(value, Enum):
        return _normalize_json_value(value.value)
    if isinstance(value, BaseModel):
        return cast(JsonValue, value.model_dump(mode="json"))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        items = cast(list[object], value)
        return [_normalize_json_value(item) for item in items]
    if isinstance(value, tuple):
        items = cast(tuple[object, ...], value)
        return [_normalize_json_value(item) for item in items]
    if isinstance(value, dict):
        entries = cast(dict[object, object], value)
        normalized: dict[str, JsonValue] = {}
        for key, item in entries.items():
            normalized[str(key)] = _normalize_json_value(item)
        return normalized
    return str(value)


def _invoke_tool_action(
    *,
    action: Callable[..., object | Awaitable[object]] | object,
    tool_input: dict[str, JsonValue],
) -> object | Awaitable[object]:
    if not callable(action):
        return action
    signature = inspect.signature(action)
    parameters = list(signature.parameters.values())
    if not parameters:
        no_arg_action = cast(Callable[[], object | Awaitable[object]], action)
        return no_arg_action()
    if _uses_tool_input_dict(parameters):
        input_action = cast(
            Callable[[dict[str, JsonValue]], object | Awaitable[object]],
            action,
        )
        return input_action(tool_input)
    kwargs = _bind_tool_action_kwargs(
        parameters=parameters,
        tool_input=tool_input,
        resolved_annotations=_resolve_tool_action_annotations(action),
    )
    named_action = cast(Callable[..., object | Awaitable[object]], action)
    return named_action(**kwargs)


def _capture_tool_input(
    *,
    raw_args: Mapping[str, object],
    action: Callable[..., object | Awaitable[object]] | object,
    exclude: tuple[str, ...],
) -> dict[str, JsonValue]:
    excluded = set(exclude)
    parameter_names = _tool_input_parameter_names(action)
    result: dict[str, JsonValue] = {}
    for name, value in raw_args.items():
        if name in excluded or name.startswith("_"):
            continue
        if parameter_names is not None and name not in parameter_names:
            continue
        result[name] = _normalize_json_value(value)
    return result


def _tool_input_parameter_names(
    action: Callable[..., object | Awaitable[object]] | object,
) -> set[str] | None:
    if not callable(action):
        return None
    parameters = list(inspect.signature(action).parameters.values())
    if not parameters or _uses_tool_input_dict(parameters):
        return None
    names: set[str] = set()
    for parameter in parameters:
        if parameter.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            names.add(parameter.name)
    return names


def _uses_tool_input_dict(parameters: list[inspect.Parameter]) -> bool:
    if len(parameters) != 1:
        return False
    parameter = parameters[0]
    return parameter.kind in {
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    } and parameter.name in {"tool_input", "args", "tool_args"}


def _bind_tool_action_kwargs(
    *,
    parameters: list[inspect.Parameter],
    tool_input: dict[str, JsonValue],
    resolved_annotations: Mapping[str, object] | None = None,
) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    for parameter in parameters:
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            kwargs.update(tool_input)
            continue
        if parameter.kind not in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            raise TypeError(
                f"Unsupported tool action parameter kind: {parameter.kind.value}"
            )
        if parameter.name not in tool_input:
            continue
        kwargs[parameter.name] = _coerce_tool_argument_for_parameter(
            value=tool_input[parameter.name],
            parameter=parameter,
            annotation=_resolved_parameter_annotation(
                parameter=parameter,
                resolved_annotations=resolved_annotations,
            ),
        )
    return kwargs


def _coerce_tool_argument_for_parameter(
    *,
    value: JsonValue,
    parameter: inspect.Parameter,
    annotation: object,
) -> object:
    if value is None:
        return None
    model_list_type = _resolve_pydantic_model_list_type(annotation)
    if model_list_type is not None and isinstance(value, list):
        return [model_list_type.model_validate(item) for item in value]
    model_type = _resolve_pydantic_model_type(annotation)
    if model_type is not None and isinstance(value, dict):
        return model_type.model_validate(value)
    enum_type = _resolve_enum_type(annotation=annotation, parameter=parameter)
    if enum_type is not None and isinstance(value, str):
        return enum_type(value)
    if _parameter_accepts_type(
        annotation=annotation, parameter=parameter, expected_type=bool
    ):
        return _coerce_bool(value)
    if _parameter_accepts_type(
        annotation=annotation, parameter=parameter, expected_type=int
    ):
        return _coerce_int(value)
    if _parameter_accepts_type(
        annotation=annotation, parameter=parameter, expected_type=float
    ):
        return _coerce_float(value)
    if _parameter_accepts_type(
        annotation=annotation, parameter=parameter, expected_type=str
    ):
        return str(value)
    if _parameter_accepts_type(
        annotation=annotation, parameter=parameter, expected_type=tuple
    ) and isinstance(value, list):
        return tuple(value)
    if _parameter_accepts_type(
        annotation=annotation, parameter=parameter, expected_type=list
    ) and isinstance(value, tuple):
        return list(value)
    return value


def _parameter_accepts_type(
    *,
    annotation: object,
    parameter: inspect.Parameter,
    expected_type: type[object],
) -> bool:
    if annotation is not inspect._empty and _annotation_contains_type(
        annotation=annotation,
        expected_type=expected_type,
    ):
        return True
    default = parameter.default
    if default is inspect._empty or default is None:
        return False
    return isinstance(default, expected_type)


def _annotation_contains_type(
    *,
    annotation: object,
    expected_type: type[object],
) -> bool:
    if annotation is expected_type:
        return True
    origin = get_origin(annotation)
    if origin is None:
        return False
    return any(
        item is expected_type for item in get_args(annotation) if item is not type(None)
    )


def _resolve_pydantic_model_list_type(
    annotation: object,
) -> type[BaseModel] | None:
    origin = get_origin(annotation)
    if origin not in {list, tuple}:
        return None
    for item in get_args(annotation):
        if inspect.isclass(item) and issubclass(item, BaseModel):
            return cast(type[BaseModel], item)
    return None


def _resolve_pydantic_model_type(
    annotation: object,
) -> type[BaseModel] | None:
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        return cast(type[BaseModel], annotation)
    origin = get_origin(annotation)
    if origin is None:
        return None
    for item in get_args(annotation):
        if item is type(None):
            continue
        if inspect.isclass(item) and issubclass(item, BaseModel):
            return cast(type[BaseModel], item)
    return None


def _resolve_enum_type(
    *,
    annotation: object,
    parameter: inspect.Parameter,
) -> type[Enum] | None:
    if annotation is not inspect._empty:
        candidate = _enum_type_from_annotation(annotation)
        if candidate is not None:
            return candidate
    default = parameter.default
    if default is inspect._empty or not isinstance(default, Enum):
        return None
    return type(default)


def _enum_type_from_annotation(annotation: object) -> type[Enum] | None:
    if inspect.isclass(annotation) and issubclass(annotation, Enum):
        return cast(type[Enum], annotation)
    origin = get_origin(annotation)
    if origin is None:
        return None
    for item in get_args(annotation):
        if item is type(None):
            continue
        if inspect.isclass(item) and issubclass(item, Enum):
            return cast(type[Enum], item)
    return None


def _resolve_tool_action_annotations(
    action: Callable[..., object | Awaitable[object]] | object,
) -> dict[str, object]:
    if not callable(action):
        return {}
    try:
        return get_type_hints(action)
    except (AttributeError, NameError, TypeError):
        return {}


def _resolved_parameter_annotation(
    *,
    parameter: inspect.Parameter,
    resolved_annotations: Mapping[str, object] | None,
) -> object:
    if resolved_annotations is None:
        return parameter.annotation
    return resolved_annotations.get(parameter.name, parameter.annotation)


def _coerce_bool(value: JsonValue) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _coerce_int(value: JsonValue) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return int(stripped)
    raise ValueError(f"Cannot coerce tool argument to int: {value!r}")


def _coerce_float(value: JsonValue) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return float(stripped)
    raise ValueError(f"Cannot coerce tool argument to float: {value!r}")


def _raise_if_stopped(ctx: ToolContext) -> None:
    ctx.deps.run_control_manager.raise_if_cancelled(
        run_id=ctx.deps.run_id,
        instance_id=ctx.deps.instance_id,
    )


async def _apply_pre_tool_hooks(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    tool_input: dict[str, JsonValue],
) -> tuple[dict[str, JsonValue], ToolError | None, bool]:
    hook_service = getattr(ctx.deps, "hook_service", None)
    if hook_service is None:
        return tool_input, None, False
    bundle = await hook_service.execute(
        event_input=PreToolUseInput(
            event_name=HookEventName.PRE_TOOL_USE,
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_input=tool_input,
        ),
        run_event_hub=ctx.deps.run_event_hub,
    )
    if bundle.decision == HookDecisionType.DENY:
        return (
            tool_input,
            ToolError(
                type="hook_denied",
                message=bundle.reason or "Tool call denied by runtime hooks.",
                retryable=False,
            ),
            False,
        )
    next_args = tool_input
    if isinstance(bundle.updated_input, dict):
        next_args = cast(dict[str, JsonValue], bundle.updated_input)
    return next_args, None, bundle.decision == HookDecisionType.ASK


async def _apply_permission_request_hooks(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    args_summary: dict[str, JsonValue],
) -> tuple[bool, ToolError | None]:
    hook_service = getattr(ctx.deps, "hook_service", None)
    if hook_service is None:
        return False, None
    bundle = await hook_service.execute(
        event_input=PermissionRequestInput(
            event_name=HookEventName.PERMISSION_REQUEST,
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_input=args_summary,
            approval_required=True,
        ),
        run_event_hub=ctx.deps.run_event_hub,
    )
    if bundle.decision == HookDecisionType.DENY:
        return (
            False,
            ToolError(
                type="hook_denied",
                message=bundle.reason or "Tool approval denied by runtime hooks.",
                retryable=False,
            ),
        )
    return bundle.decision == HookDecisionType.ALLOW, None


async def _apply_post_tool_hooks(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    args_summary: dict[str, JsonValue],
    envelope: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    hook_service = getattr(ctx.deps, "hook_service", None)
    if hook_service is None:
        return envelope
    bundle = await hook_service.execute(
        event_input=PostToolUseInput(
            event_name=HookEventName.POST_TOOL_USE,
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_input=args_summary,
            tool_result=envelope,
        ),
        run_event_hub=ctx.deps.run_event_hub,
    )
    return _apply_post_hook_bundle_to_envelope(
        ctx=ctx,
        hook_event=HookEventName.POST_TOOL_USE,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        envelope=envelope,
        bundle=bundle,
    )


async def _apply_post_tool_failure_hooks(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    args_summary: dict[str, JsonValue],
    envelope: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    hook_service = getattr(ctx.deps, "hook_service", None)
    if hook_service is None:
        return envelope
    error_payload = envelope.get("error")
    tool_error = (
        cast(dict[str, JsonValue], error_payload)
        if isinstance(error_payload, dict)
        else {}
    )
    bundle = await hook_service.execute(
        event_input=PostToolUseFailureInput(
            event_name=HookEventName.POST_TOOL_USE_FAILURE,
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_input=args_summary,
            tool_error=tool_error,
        ),
        run_event_hub=ctx.deps.run_event_hub,
    )
    return _apply_post_hook_bundle_to_envelope(
        ctx=ctx,
        hook_event=HookEventName.POST_TOOL_USE_FAILURE,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        envelope=envelope,
        bundle=bundle,
    )


def _apply_post_hook_bundle_to_envelope(
    *,
    ctx: ToolContext,
    hook_event: HookEventName,
    tool_name: str,
    tool_call_id: str,
    envelope: dict[str, JsonValue],
    bundle: HookDecisionBundle,
) -> dict[str, JsonValue]:
    meta = envelope.get("meta")
    runtime_meta = cast(dict[str, JsonValue], meta) if isinstance(meta, dict) else {}
    if bundle.additional_context:
        runtime_meta["hook_additional_context"] = list(bundle.additional_context)
        _enqueue_additional_context_followup(
            ctx=ctx,
            contexts=bundle.additional_context,
        )
    if bundle.deferred_action:
        runtime_meta["hook_deferred_action"] = bundle.deferred_action
        _enqueue_deferred_followup(
            ctx=ctx,
            hook_event=hook_event,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            deferred_action=bundle.deferred_action,
        )
    envelope["meta"] = runtime_meta
    return envelope


def _enqueue_additional_context_followup(
    *,
    ctx: ToolContext,
    contexts: tuple[str, ...],
) -> None:
    injection_manager = getattr(ctx.deps, "injection_manager", None)
    if injection_manager is None:
        return
    if not injection_manager.is_active(ctx.deps.run_id):
        return
    content = "\n\n".join(
        str(context).strip() for context in contexts if str(context).strip()
    )
    if not content:
        return
    _ = injection_manager.enqueue(
        ctx.deps.run_id,
        ctx.deps.instance_id,
        source=InjectionSource.SYSTEM,
        content=content,
    )


def _enqueue_deferred_followup(
    *,
    ctx: ToolContext,
    hook_event: HookEventName,
    tool_name: str,
    tool_call_id: str,
    deferred_action: str,
) -> None:
    injection_manager = getattr(ctx.deps, "injection_manager", None)
    if injection_manager is None:
        return
    if not injection_manager.is_active(ctx.deps.run_id):
        return
    record = injection_manager.enqueue(
        ctx.deps.run_id,
        ctx.deps.instance_id,
        source=InjectionSource.SYSTEM,
        content=deferred_action,
    )
    _ = record
    ctx.deps.run_event_hub.publish(
        RunEvent(
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            event_type=RunEventType.HOOK_DEFERRED,
            payload_json=dumps(
                {
                    "hook_event": hook_event.value,
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "deferred_action": deferred_action,
                },
                ensure_ascii=False,
            ),
        )
    )


async def _handle_tool_approval(
    *,
    ctx: ToolContext,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    approval_args_summary: dict[str, JsonValue] | None,
    meta: dict[str, JsonValue],
    tool_call_id: str,
    approval_request: ToolApprovalRequest | None = None,
    force_approval: bool = False,
) -> tuple[str | None, ToolError | None]:
    decision = _evaluate_tool_approval_policy(
        policy=ctx.deps.tool_approval_policy,
        tool_name=tool_name,
        approval_request=approval_request,
    )
    approval_required = force_approval or decision.required
    run_yolo = _policy_uses_yolo(ctx.deps.tool_approval_policy)
    args_preview = _safe_json(args_summary)
    approval_preview = _safe_json(
        approval_args_summary if approval_args_summary is not None else args_summary
    )
    meta["run_yolo"] = run_yolo
    meta["approval_required"] = approval_required
    meta["approval_mode"] = (
        ToolApprovalMode.YOLO.value
        if run_yolo and not approval_required
        else (
            ToolApprovalMode.POLICY_EXEMPT.value
            if not approval_required
            else ToolApprovalMode.APPROVAL_FLOW.value
        )
    )
    if decision.permission_scope is not None:
        meta["permission_scope"] = decision.permission_scope.value
    if decision.risk_level is not None:
        meta["risk_level"] = decision.risk_level.value
    if decision.target_summary:
        meta["target_summary"] = decision.target_summary
    if decision.source:
        meta["source"] = decision.source
    if decision.execution_surface is not None:
        meta["execution_surface"] = decision.execution_surface.value
    cache_key = approval_request.cache_key if approval_request is not None else ""
    if not approval_required:
        meta["approval_status"] = "not_required"
        return None, None

    hook_allowed, hook_error = await _apply_permission_request_hooks(
        ctx=ctx,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        args_summary=args_summary,
    )
    if hook_error is not None:
        return None, hook_error
    if hook_allowed:
        meta["approval_required"] = False
        meta["approval_mode"] = ToolApprovalMode.POLICY_EXEMPT.value
        meta["approval_status"] = "not_required"
        return None, None

    reusable_ticket = ctx.deps.approval_ticket_repo.find_reusable(
        run_id=ctx.deps.run_id,
        task_id=ctx.deps.task_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        tool_name=tool_name,
        args_preview=args_preview,
        cache_key=cache_key,
        signature_args_preview=approval_preview,
    )
    if reusable_ticket is not None:
        if reusable_ticket.status == ApprovalTicketStatus.APPROVED:
            meta["approval_status"] = "approve"
            if reusable_ticket.feedback:
                meta["approval_feedback"] = reusable_ticket.feedback
            return reusable_ticket.tool_call_id, None
        if reusable_ticket.status == ApprovalTicketStatus.REQUESTED:
            return await _wait_for_ticket_resolution(
                ctx=ctx,
                ticket_id=reusable_ticket.tool_call_id,
                tool_name=tool_name,
                args_preview=args_preview,
                meta=meta,
                decision=decision,
            )
        if reusable_ticket.status == ApprovalTicketStatus.DENIED:
            meta["approval_status"] = "deny"
            if reusable_ticket.feedback:
                meta["approval_feedback"] = reusable_ticket.feedback
            return reusable_ticket.tool_call_id, ToolError(
                type="approval_denied",
                message="Tool call was denied by user.",
                retryable=True,
            )
        if reusable_ticket.status == ApprovalTicketStatus.TIMED_OUT:
            meta["approval_status"] = "timeout"
            return reusable_ticket.tool_call_id, ToolError(
                type="approval_timeout",
                message="Tool approval timed out.",
                retryable=True,
            )
    ticket = ctx.deps.approval_ticket_repo.upsert_requested(
        tool_call_id=tool_call_id,
        run_id=ctx.deps.run_id,
        session_id=ctx.deps.session_id,
        task_id=ctx.deps.task_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        tool_name=tool_name,
        args_preview=args_preview,
        metadata=approval_request.metadata if approval_request is not None else None,
        cache_key=cache_key,
        signature_args_preview=approval_preview,
    )
    return await _wait_for_ticket_resolution(
        ctx=ctx,
        ticket_id=ticket.tool_call_id,
        tool_name=tool_name,
        args_preview=args_preview,
        meta=meta,
        decision=decision,
        publish_request=True,
    )


async def _wait_for_ticket_resolution(
    *,
    ctx: ToolContext,
    ticket_id: str,
    tool_name: str,
    args_preview: str,
    meta: dict[str, JsonValue],
    decision: ToolApprovalDecision,
    publish_request: bool = False,
) -> tuple[str | None, ToolError | None]:
    existing_approval = ctx.deps.tool_approval_manager.get_approval(
        run_id=ctx.deps.run_id,
        tool_call_id=ticket_id,
    )
    if existing_approval is None:
        ctx.deps.tool_approval_manager.open_approval(
            run_id=ctx.deps.run_id,
            tool_call_id=ticket_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            tool_name=tool_name,
            args_preview=args_preview,
            risk_level=(
                decision.risk_level.value if decision.risk_level is not None else "high"
            ),
        )
        publish_request = True

    ctx.deps.run_runtime_repo.ensure(
        run_id=ctx.deps.run_id,
        session_id=ctx.deps.session_id,
        root_task_id=ctx.deps.task_id,
    )
    ctx.deps.run_runtime_repo.update(
        ctx.deps.run_id,
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
        active_instance_id=ctx.deps.instance_id,
        active_task_id=ctx.deps.task_id,
        active_role_id=ctx.deps.role_id,
        active_subagent_instance_id=None,
        last_error=None,
    )
    if publish_request:
        log_event(
            LOGGER,
            logging.WARNING,
            event="tool.approval.requested",
            message="Tool approval requested",
            payload={
                "tool_name": tool_name,
                "tool_call_id": ticket_id,
            },
        )
        _publish_tool_approval_event(
            ctx=ctx,
            event_type=RunEventType.TOOL_APPROVAL_REQUESTED,
            payload={
                "tool_call_id": ticket_id,
                "tool_name": tool_name,
                "args_preview": args_preview,
                "instance_id": ctx.deps.instance_id,
                "role_id": ctx.deps.role_id,
                "risk_level": (
                    decision.risk_level.value
                    if decision.risk_level is not None
                    else "high"
                ),
                "permission_scope": (
                    decision.permission_scope.value
                    if decision.permission_scope is not None
                    else ""
                ),
                "target_summary": decision.target_summary,
                "source": decision.source,
                "execution_surface": (
                    decision.execution_surface.value
                    if decision.execution_surface is not None
                    else ""
                ),
            },
        )
        _publish_tool_approval_notification(
            ctx=ctx,
            tool_call_id=ticket_id,
            tool_name=tool_name,
        )

    try:
        action, feedback = await asyncio.to_thread(
            ctx.deps.tool_approval_manager.wait_for_approval,
            run_id=ctx.deps.run_id,
            tool_call_id=ticket_id,
            timeout=ctx.deps.tool_approval_policy.timeout_seconds,
        )
    except TimeoutError:
        ctx.deps.tool_approval_manager.close_approval(
            run_id=ctx.deps.run_id,
            tool_call_id=ticket_id,
        )
        try:
            resolved_ticket = ctx.deps.approval_ticket_repo.resolve(
                tool_call_id=ticket_id,
                status=ApprovalTicketStatus.TIMED_OUT,
                expected_status=ApprovalTicketStatus.REQUESTED,
            )
        except ApprovalTicketStatusConflictError:
            resolved_ticket = ctx.deps.approval_ticket_repo.get(ticket_id)
            if resolved_ticket is None:
                raise KeyError(f"Unknown approval ticket: {ticket_id}") from None
        resolved_action, resolved_error = _approval_resolution_from_ticket(
            ticket=resolved_ticket,
            meta=meta,
        )
        if resolved_action == "timeout":
            ctx.deps.run_runtime_repo.update(
                ctx.deps.run_id,
                status=RunRuntimeStatus.PAUSED,
                phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
                active_instance_id=ctx.deps.instance_id,
                active_task_id=ctx.deps.task_id,
                active_role_id=ctx.deps.role_id,
                active_subagent_instance_id=None,
                last_error="Tool approval timed out",
            )
        elif resolved_action == "deny":
            ctx.deps.run_runtime_repo.update(
                ctx.deps.run_id,
                status=RunRuntimeStatus.PAUSED,
                phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
                active_instance_id=ctx.deps.instance_id,
                active_task_id=ctx.deps.task_id,
                active_role_id=ctx.deps.role_id,
                active_subagent_instance_id=None,
                last_error="Tool call was denied by user.",
            )
        log_event(
            LOGGER,
            logging.INFO if resolved_action == "approve" else logging.WARNING,
            event="tool.approval.resolved",
            message=(
                "Tool approval resolved"
                if resolved_action != "timeout"
                else "Tool approval timed out"
            ),
            payload={
                "tool_name": tool_name,
                "tool_call_id": ticket_id,
                "action": resolved_action,
            },
        )
        _publish_tool_approval_event(
            ctx=ctx,
            event_type=RunEventType.TOOL_APPROVAL_RESOLVED,
            payload={
                "tool_call_id": ticket_id,
                "tool_name": tool_name,
                "action": resolved_action,
                "feedback": resolved_ticket.feedback,
                "instance_id": ctx.deps.instance_id,
                "role_id": ctx.deps.role_id,
            },
        )
        return ticket_id, resolved_error

    ctx.deps.tool_approval_manager.close_approval(
        run_id=ctx.deps.run_id,
        tool_call_id=ticket_id,
    )
    resolved_status = (
        ApprovalTicketStatus.APPROVED
        if _approval_action_is_approved(action)
        else ApprovalTicketStatus.DENIED
    )
    try:
        resolved_ticket = ctx.deps.approval_ticket_repo.resolve(
            tool_call_id=ticket_id,
            status=resolved_status,
            feedback=feedback,
            expected_status=ApprovalTicketStatus.REQUESTED,
        )
    except ApprovalTicketStatusConflictError:
        resolved_ticket = ctx.deps.approval_ticket_repo.get(ticket_id)
        if resolved_ticket is None:
            raise KeyError(f"Unknown approval ticket: {ticket_id}") from None
    resolved_action, resolved_error = _approval_resolution_from_ticket(
        ticket=resolved_ticket,
        meta=meta,
    )
    log_event(
        LOGGER,
        logging.INFO if resolved_action == "approve" else logging.WARNING,
        event="tool.approval.resolved",
        message="Tool approval resolved",
        payload={
            "tool_name": tool_name,
            "tool_call_id": ticket_id,
            "action": resolved_action,
        },
    )
    _publish_tool_approval_event(
        ctx=ctx,
        event_type=RunEventType.TOOL_APPROVAL_RESOLVED,
        payload={
            "tool_call_id": ticket_id,
            "tool_name": tool_name,
            "action": resolved_action,
            "feedback": resolved_ticket.feedback,
            "instance_id": ctx.deps.instance_id,
            "role_id": ctx.deps.role_id,
        },
    )
    if resolved_action == "deny":
        ctx.deps.run_runtime_repo.update(
            ctx.deps.run_id,
            status=RunRuntimeStatus.PAUSED,
            phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
            active_instance_id=ctx.deps.instance_id,
            active_task_id=ctx.deps.task_id,
            active_role_id=ctx.deps.role_id,
            active_subagent_instance_id=None,
            last_error="Tool call was denied by user.",
        )
        return ticket_id, resolved_error
    if resolved_action == "timeout":
        ctx.deps.run_runtime_repo.update(
            ctx.deps.run_id,
            status=RunRuntimeStatus.PAUSED,
            phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
            active_instance_id=ctx.deps.instance_id,
            active_task_id=ctx.deps.task_id,
            active_role_id=ctx.deps.role_id,
            active_subagent_instance_id=None,
            last_error="Tool approval timed out",
        )
        return ticket_id, resolved_error

    return ticket_id, None


def _approval_action_is_approved(action: str) -> bool:
    return action in {"approve", "approve_once", "approve_exact", "approve_prefix"}


def _approval_resolution_from_ticket(
    *,
    ticket: ApprovalTicketRecord,
    meta: dict[str, JsonValue],
) -> tuple[str, ToolError | None]:
    if ticket.feedback:
        meta["approval_feedback"] = ticket.feedback
    if ticket.status in {
        ApprovalTicketStatus.APPROVED,
        ApprovalTicketStatus.COMPLETED,
    }:
        meta["approval_status"] = "approve"
        return "approve", None
    if ticket.status == ApprovalTicketStatus.DENIED:
        meta["approval_status"] = "deny"
        return "deny", ToolError(
            type="approval_denied",
            message="Tool call was denied by user.",
            retryable=True,
        )
    meta["approval_status"] = "timeout"
    return "timeout", ToolError(
        type="approval_timeout",
        message="Tool approval timed out.",
        retryable=True,
    )


def _publish_tool_approval_notification(
    *,
    ctx: ToolContext,
    tool_call_id: str,
    tool_name: str,
) -> None:
    notification_service = ctx.deps.notification_service
    if notification_service is None:
        return

    role_label = ctx.deps.role_id or "An agent"
    body = f"{role_label} requests approval for {tool_name}."
    _ = notification_service.emit(
        notification_type=NotificationType.TOOL_APPROVAL_REQUESTED,
        title="Approval Required",
        body=body,
        dedupe_key=f"tool_approval_requested:{ctx.deps.run_id}:{tool_call_id}",
        context=NotificationContext(
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        ),
    )


def _publish_tool_approval_event(
    *,
    ctx: ToolContext,
    event_type: RunEventType,
    payload: dict[str, JsonValue],
) -> None:
    ctx.deps.run_event_hub.publish(
        RunEvent(
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            event_type=event_type,
            payload_json=dumps(payload, ensure_ascii=False),
        )
    )


def _visible_envelope(
    *,
    ok: bool,
    data: JsonValue = None,
    error: ToolError | None = None,
    meta: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    envelope = ToolResultEnvelope(
        ok=ok,
        data=data,
        error=error,
        meta={} if meta is None else dict(meta),
    )
    return cast(dict[str, JsonValue], envelope.model_dump(mode="json"))


def _evaluate_tool_approval_policy(
    *,
    policy: ToolApprovalPolicy | _RequiresApprovalPolicy,
    tool_name: str,
    approval_request: ToolApprovalRequest | None,
) -> ToolApprovalDecision:
    if isinstance(policy, ToolApprovalPolicy):
        return policy.evaluate(tool_name, approval_request)
    required = cast(bool, policy.requires_approval(tool_name))
    return ToolApprovalDecision(
        required=required,
        permission_scope=(
            approval_request.permission_scope if approval_request is not None else None
        ),
        risk_level=approval_request.risk_level
        if approval_request is not None
        else None,
        target_summary=(
            approval_request.target_summary if approval_request is not None else ""
        ),
        source=approval_request.source if approval_request is not None else "",
        execution_surface=(
            approval_request.execution_surface if approval_request is not None else None
        ),
    )


class _RequiresApprovalPolicy(Protocol):
    timeout_seconds: float

    def requires_approval(self, tool_name: str) -> bool: ...


def _internal_record(
    *,
    tool_name: str,
    visible_envelope: dict[str, JsonValue],
    internal_data: JsonValue | None,
    runtime_meta: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    record = ToolInternalRecord(
        tool=tool_name,
        visible_result=ToolResultEnvelope.model_validate(visible_envelope),
        internal_data=internal_data,
        runtime_meta=runtime_meta,
    )
    return cast(dict[str, JsonValue], record.model_dump(mode="json"))


def _persist_tool_record(
    *,
    ctx: ToolContext,
    tool_call_id: str,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    visible_envelope: dict[str, JsonValue],
    internal_data: JsonValue | None,
    runtime_meta: dict[str, JsonValue],
    execution_status: ToolExecutionStatus,
) -> None:
    approval_status = _approval_status_from_meta(runtime_meta)
    approval_mode = _approval_mode_from_meta(runtime_meta)
    merge_tool_call_state(
        shared_store=ctx.deps.shared_store,
        task_id=ctx.deps.task_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        run_id=ctx.deps.run_id,
        session_id=ctx.deps.session_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        args_preview=_safe_json(args_summary),
        run_yolo=bool(runtime_meta.get("run_yolo") is True),
        approval_mode=approval_mode,
        approval_status=approval_status,
        approval_feedback=str(runtime_meta.get("approval_feedback") or ""),
        execution_status=execution_status,
        result_envelope=_internal_record(
            tool_name=tool_name,
            visible_envelope=visible_envelope,
            internal_data=internal_data,
            runtime_meta=runtime_meta,
        ),
    )


def _approval_status_from_meta(
    runtime_meta: dict[str, JsonValue],
) -> ToolApprovalStatus | None:
    approval_text = str(runtime_meta.get("approval_status") or "").strip().lower()
    if approval_text in {
        ToolApprovalStatus.APPROVE.value,
        "approve_once",
        "approve_exact",
        "approve_prefix",
    }:
        return ToolApprovalStatus.APPROVE
    if approval_text == ToolApprovalStatus.DENY.value:
        return ToolApprovalStatus.DENY
    if approval_text == ToolApprovalStatus.TIMEOUT.value:
        return ToolApprovalStatus.TIMEOUT
    if approval_text == ToolApprovalStatus.NOT_REQUIRED.value:
        return ToolApprovalStatus.NOT_REQUIRED
    return None


def _approval_mode_from_meta(
    runtime_meta: dict[str, JsonValue],
) -> ToolApprovalMode | None:
    approval_mode = str(runtime_meta.get("approval_mode") or "").strip().lower()
    for candidate in ToolApprovalMode:
        if approval_mode == candidate.value:
            return candidate
    return None


def _policy_uses_yolo(policy: object) -> bool:
    return bool(getattr(policy, "yolo", False))
