# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime.context import (
    ToolContext,
    ToolDeps,
)
from relay_teams.tools.runtime.execution import execute_tool_call

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def todo_read(ctx: ToolContext) -> dict[str, JsonValue]:
        """Return the current run-scoped todo snapshot."""

        async def _action() -> dict[str, JsonValue]:
            service = ctx.deps.todo_service
            if service is None:
                raise RuntimeError("Todo service is not configured")
            snapshot = await service.get_for_run_async(
                run_id=ctx.deps.run_id,
                session_id=ctx.deps.session_id,
            )
            return {"todo": snapshot.model_dump(mode="json")}

        return await execute_tool_call(
            ctx,
            tool_name="todo_read",
            args_summary={},
            action=_action,
            raw_args=locals(),
        )
