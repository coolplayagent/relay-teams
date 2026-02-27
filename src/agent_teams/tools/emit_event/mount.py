from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.core.enums import EventType
from agent_teams.core.models import EventEnvelope
from agent_teams.tools.runtime import ToolDeps
from agent_teams.tools.tool_helpers import emit_tool_call, emit_tool_result, with_injections


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def emit_event(
        ctx,
        event_type: str,
        trace_id: str,
        session_id: str,
        task_id: str | None = None,
        instance_id: str | None = None,
        payload_json: str = '{}',
    ) -> str:
        emit_tool_call(ctx, 'emit_event')
        event = EventEnvelope(
            event_type=EventType(event_type),
            trace_id=trace_id,
            session_id=session_id,
            task_id=task_id,
            instance_id=instance_id,
            payload_json=payload_json,
        )
        ctx.deps.event_bus.emit(event)
        result = with_injections(ctx, event.event_type.value)
        emit_tool_result(ctx, 'emit_event')
        return result
