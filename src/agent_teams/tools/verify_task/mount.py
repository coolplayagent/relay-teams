from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.tool_helpers import execute_tool
from agent_teams.tools.verify_task.impl import verify_task as verify_task_impl


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def verify_task(ctx: ToolContext, task_id: str) -> str:
        def _action() -> str:
            verification = verify_task_impl(
                ctx.deps.task_repo, ctx.deps.event_bus, task_id
            )
            return verification.model_dump_json()

        return execute_tool(
            ctx,
            tool_name="verify_task",
            args_summary={"task_id": task_id},
            action=_action,
        )
