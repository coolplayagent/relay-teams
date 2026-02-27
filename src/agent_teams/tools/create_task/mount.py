from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.core.models import TaskEnvelope
from agent_teams.tools.runtime import ToolDeps
from agent_teams.tools.tool_helpers import emit_tool_call, emit_tool_result, with_injections


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def create_task(ctx, envelope_json: str) -> str:
        emit_tool_call(ctx, 'create_task')
        envelope = TaskEnvelope.model_validate_json(envelope_json)
        ctx.deps.task_repo.create(envelope)
        result = with_injections(ctx, envelope.task_id)
        emit_tool_result(ctx, 'create_task')
        return result
