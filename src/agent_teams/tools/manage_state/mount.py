from __future__ import annotations

from typing import Literal

from pydantic_ai import Agent

from agent_teams.core.enums import ScopeType
from agent_teams.core.models import ScopeRef, StateMutation
from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.tool_helpers import execute_tool

SCOPE_TYPE_LITERAL = Literal["global", "session", "task", "instance"]


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def manage_state(
        ctx: ToolContext,
        scope_type: SCOPE_TYPE_LITERAL,
        scope_id: str,
        key: str,
        value_json: str,
    ) -> str:
        def _action() -> str:
            mutation = StateMutation(
                scope=ScopeRef(scope_type=ScopeType(scope_type), scope_id=scope_id),
                key=key,
                value_json=value_json,
            )
            ctx.deps.shared_store.manage_state(mutation)
            return key

        return execute_tool(
            ctx,
            tool_name="manage_state",
            args_summary={
                "scope_type": scope_type,
                "scope_id": scope_id,
                "key": key,
                "value_len": len(value_json),
            },
            action=_action,
        )
