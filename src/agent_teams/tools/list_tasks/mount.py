from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolDeps
from agent_teams.tools.tool_helpers import emit_tool_call, emit_tool_result, with_injections


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def list_tasks(ctx) -> str:
        emit_tool_call(ctx, 'list_tasks')
        items = ctx.deps.task_repo.list_all()
        payload = '[' + ','.join(item.model_dump_json() for item in items) + ']'
        result = with_injections(ctx, payload)
        emit_tool_result(ctx, 'list_tasks')
        return result
