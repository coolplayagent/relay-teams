from __future__ import annotations

from typing import Literal

from pydantic_ai import Agent

from agent_teams.core.enums import EventType
from agent_teams.core.models import EventEnvelope
from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.tool_helpers import execute_tool

EVENT_TYPE_LITERAL = Literal[
    "task_created",
    "task_assigned",
    "task_started",
    "task_completed",
    "task_failed",
    "task_timeout",
    "instance_created",
    "instance_recycled",
    "verification_passed",
    "verification_failed",
]


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def emit_event(
        ctx: ToolContext,
        event_type: EVENT_TYPE_LITERAL,
        task_id: str | None = None,
        instance_id: str | None = None,
        payload_json: str = "{}",
    ) -> str:
        def _action() -> str:
            event = EventEnvelope(
                event_type=EventType(event_type),
                trace_id=ctx.deps.trace_id,
                session_id=ctx.deps.session_id,
                task_id=task_id,
                instance_id=instance_id,
                payload_json=payload_json,
            )
            ctx.deps.event_bus.emit(event)
            return event.event_type.value

        return execute_tool(
            ctx,
            tool_name="emit_event",
            args_summary={
                "event_type": event_type,
                "task_id": task_id,
                "instance_id": instance_id,
                "payload_len": len(payload_json),
            },
            action=_action,
        )
