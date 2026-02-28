from __future__ import annotations

import json
import time
from collections.abc import Callable

from agent_teams.runtime.console import is_debug, log_debug, log_tool_call, log_tool_error
from agent_teams.tools.runtime import ToolContext


def emit_tool_call(ctx: ToolContext, tool_name: str) -> None:
    ctx.deps.run_event_hub.publish(
        RunEvent(
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            event_type=RunEventType.TOOL_CALL,
            payload_json=f'{{"tool":"{tool_name}"}}',
        )
    )


def emit_tool_result(ctx: ToolContext, tool_name: str) -> None:
    ctx.deps.run_event_hub.publish(
        RunEvent(
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            event_type=RunEventType.TOOL_RESULT,
            payload_json=f'{{"tool":"{tool_name}"}}',
        )
    )


def execute_tool(
    ctx: ToolContext,
    *,
    tool_name: str,
    args_summary: dict[str, object],
    action: Callable[[], str],
) -> str:
    emit_tool_call(ctx, tool_name)
    started = time.perf_counter()
    if is_debug():
        log_debug(
            f'[tool:start] tool={tool_name} run={ctx.deps.run_id} '
            f'task={ctx.deps.task_id} instance={ctx.deps.instance_id} args={_safe_json(args_summary)}'
        )
    else:
        log_tool_call(ctx.deps.role_id, tool_name, args_summary)
    try:
        result = action()
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if is_debug():
            log_debug(f'[tool:ok] tool={tool_name} elapsed_ms={elapsed_ms}')
        emit_tool_result(ctx, tool_name)
        return result
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        payload = _error_payload(tool_name, exc)
        if is_debug():
            log_debug(
                f'[tool:error] tool={tool_name} elapsed_ms={elapsed_ms} '
                f'err_type={payload["error"]["type"]} msg={payload["error"]["message"]}'
            )
        else:
            compact = json.dumps(
                {
                    'tool': tool_name,
                    'type': payload['error']['type'],
                    'message': str(payload['error']['message']),
                },
                ensure_ascii=False,
            )
            log_tool_error(ctx.deps.role_id, compact)
        emit_tool_result(ctx, tool_name)
        return json.dumps(payload, ensure_ascii=False)


def _error_payload(tool_name: str, exc: Exception) -> dict[str, object]:
    err_type = 'internal_error'
    retryable = False
    suggested_fix = 'Retry with corrected tool parameters.'
    allowed_values: list[str] | None = None
    message = str(exc) or exc.__class__.__name__

    if isinstance(exc, ValueError):
        err_type = 'validation_error'
        retryable = True
        if 'scope_type' in message:
            allowed_values = ['global', 'session', 'task', 'instance']
            suggested_fix = 'Use one of allowed_values for scope_type.'
    elif isinstance(exc, KeyError):
        err_type = 'not_found'
        retryable = True
        suggested_fix = 'Check IDs and ensure target exists before retrying.'
    elif isinstance(exc, PermissionError):
        err_type = 'permission_error'
        retryable = True
        suggested_fix = 'Use a path within workspace or permitted scope.'

    error: dict[str, object] = {
        'type': err_type,
        'message': message,
        'suggested_fix': suggested_fix,
    }
    if allowed_values is not None:
        error['allowed_values'] = allowed_values

    return {
        'ok': False,
        'tool': tool_name,
        'error': error,
        'retryable': retryable,
    }


def _safe_json(value: object) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    if len(text) > 500:
        return text[:500] + '...(truncated)'
    return text
