from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.core.enums import InstanceStatus
from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.tool_helpers import execute_tool


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def create_subagent(ctx: ToolContext, role_id: str) -> str:
        def _action() -> str:
            instance = ctx.deps.instance_pool.create_subagent(role_id)
            ctx.deps.agent_repo.upsert_instance(
                run_id=ctx.deps.run_id,
                trace_id=ctx.deps.trace_id,
                session_id=ctx.deps.session_id,
                instance_id=instance.instance_id,
                role_id=instance.role_id,
                status=InstanceStatus.IDLE,
            )
            return instance.model_dump_json()

        return execute_tool(
            ctx,
            tool_name="create_subagent",
            args_summary={"role_id": role_id},
            action=_action,
        )
