from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.tool_helpers import execute_tool


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def list_tasks(ctx: ToolContext) -> str:
        def _action() -> str:
            items = ctx.deps.task_repo.list_all()
            return "[" + ",".join(item.model_dump_json() for item in items) + "]"

        return execute_tool(
            ctx,
            tool_name="list_tasks",
            args_summary={},
            action=_action,
        )
