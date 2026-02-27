from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolDeps
from agent_teams.tools.tool_helpers import emit_tool_call, emit_tool_result, with_injections


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def query_task(ctx, task_id: str) -> str:
        emit_tool_call(ctx, 'query_task')
        record = ctx.deps.task_repo.get(task_id)
        result = with_injections(ctx, record.model_dump_json())
        emit_tool_result(ctx, 'query_task')
        return result
