from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.core.enums import TaskStatus
from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.tool_helpers import execute_tool


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def assign_task(ctx: ToolContext, task_id: str, instance_id: str) -> str:
        def _action() -> str:
            # Guard against made-up instance ids from model output.
            ctx.deps.instance_pool.get(instance_id)
            ctx.deps.task_repo.update_status(
                task_id=task_id,
                status=TaskStatus.ASSIGNED,
                assigned_instance_id=instance_id,
            )
            return task_id

        return execute_tool(
            ctx,
            tool_name="assign_task",
            args_summary={"task_id": task_id, "instance_id": instance_id},
            action=_action,
        )
