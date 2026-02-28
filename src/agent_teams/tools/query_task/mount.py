from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.tool_helpers import execute_tool


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def query_task(ctx: ToolContext, task_id: str) -> str:
        def _action() -> str:
            record = ctx.deps.task_repo.get(task_id)
            if (
                ctx.deps.role_id == "coordinator_agent"
                and record.envelope.trace_id != ctx.deps.trace_id
            ):
                raise ValueError(
                    "Coordinator can only query tasks from current run trace. "
                    f"task_trace={record.envelope.trace_id}, current_trace={ctx.deps.trace_id}"
                )
            return record.model_dump_json()

        return execute_tool(
            ctx,
            tool_name="query_task",
            args_summary={"task_id": task_id},
            action=_action,
        )
