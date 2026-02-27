from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.core.enums import InstanceStatus
from agent_teams.tools.runtime import ToolDeps
from agent_teams.tools.tool_helpers import emit_tool_call, emit_tool_result, with_injections


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def create_subagent(ctx, role_id: str) -> str:
        emit_tool_call(ctx, 'create_subagent')
        instance = ctx.deps.instance_pool.create_subagent(role_id)
        ctx.deps.agent_repo.upsert_instance(
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            session_id=ctx.deps.session_id,
            instance_id=instance.instance_id,
            role_id=instance.role_id,
            status=InstanceStatus.IDLE,
        )
        result = with_injections(ctx, instance.model_dump_json())
        emit_tool_result(ctx, 'create_subagent')
        return result
