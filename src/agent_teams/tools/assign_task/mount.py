from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.core.enums import TaskStatus
from agent_teams.tools.runtime import ToolDeps
from agent_teams.tools.tool_helpers import emit_tool_call, emit_tool_result, with_injections


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def assign_task(ctx, task_id: str, instance_id: str) -> str:
        emit_tool_call(ctx, 'assign_task')
        ctx.deps.task_repo.update_status(
            task_id=task_id,
            status=TaskStatus.ASSIGNED,
            assigned_instance_id=instance_id,
        )
        result = with_injections(ctx, task_id)
        emit_tool_result(ctx, 'assign_task')
        return result
