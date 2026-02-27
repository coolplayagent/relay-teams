from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.core.enums import ScopeType
from agent_teams.core.models import ScopeRef, StateMutation
from agent_teams.tools.runtime import ToolDeps
from agent_teams.tools.tool_helpers import emit_tool_call, emit_tool_result, with_injections


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def manage_state(ctx, scope_type: str, scope_id: str, key: str, value_json: str) -> str:
        emit_tool_call(ctx, 'manage_state')
        mutation = StateMutation(
            scope=ScopeRef(scope_type=ScopeType(scope_type), scope_id=scope_id),
            key=key,
            value_json=value_json,
        )
        ctx.deps.shared_store.manage_state(mutation)
        result = with_injections(ctx, key)
        emit_tool_result(ctx, 'manage_state')
        return result
