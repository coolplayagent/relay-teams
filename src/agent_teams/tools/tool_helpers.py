from __future__ import annotations

from agent_teams.core.enums import RunEventType
from agent_teams.core.models import RunEvent
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


def with_injections(ctx: ToolContext, base_result: str) -> str:
    pending = ctx.deps.injection_manager.drain_at_boundary(ctx.deps.run_id, ctx.deps.instance_id)
    if not pending:
        return base_result

    running = ctx.deps.agent_repo.list_running(ctx.deps.run_id)
    running_line = ', '.join(f'{item.instance_id}:{item.role_id}' for item in running) or 'none'

    lines: list[str] = []
    for item in pending:
        sender = item.sender_instance_id or 'unknown'
        sender_role = item.sender_role_id or 'unknown'
        lines.append(f'[{item.source.value}] from={sender} role={sender_role} msg={item.content}')
        ctx.deps.run_event_hub.publish(
            RunEvent(
                run_id=ctx.deps.run_id,
                trace_id=ctx.deps.trace_id,
                task_id=ctx.deps.task_id,
                event_type=RunEventType.INJECTION_APPLIED,
                payload_json=item.model_dump_json(),
            )
        )

    injected_text = '\n'.join(lines)
    return f'{base_result}\n\n[InjectedMessages]\n{injected_text}\n\n[RunningAgents]\n{running_line}'
