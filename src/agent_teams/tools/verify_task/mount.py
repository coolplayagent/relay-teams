from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolDeps
from agent_teams.tools.tool_helpers import emit_tool_call, emit_tool_result, with_injections
from agent_teams.tools.verify_task.impl import verify_task as verify_task_impl


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def verify_task(ctx, task_id: str) -> str:
        emit_tool_call(ctx, 'verify_task')
        verification = verify_task_impl(ctx.deps.task_repo, ctx.deps.event_bus, task_id)
        result = with_injections(ctx, verification.model_dump_json())
        emit_tool_result(ctx, 'verify_task')
        return result
