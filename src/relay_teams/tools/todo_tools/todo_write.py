# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.sessions.runs.todo_models import TodoItem
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime.context import (
    ToolContext,
    ToolDeps,
)
from relay_teams.tools.runtime.execution import execute_tool_call

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def todo_write(
        ctx: ToolContext,
        items: list[TodoItem],
    ) -> dict[str, JsonValue]:
        """Replace the entire run-scoped todo list for the current run."""

        def _action(items: list[TodoItem]) -> dict[str, JsonValue]:
            service = ctx.deps.todo_service
            if service is None:
                raise RuntimeError("Todo service is not configured")
            snapshot = service.replace_for_run(
                run_id=ctx.deps.run_id,
                session_id=ctx.deps.session_id,
                items=items,
                updated_by_role_id=ctx.deps.role_id,
                updated_by_instance_id=ctx.deps.instance_id,
            )
            return {"todo": snapshot.model_dump(mode="json")}

        return await execute_tool_call(
            ctx,
            tool_name="todo_write",
            args_summary={
                "item_count": len(items),
                "in_progress_count": sum(
                    1 for item in items if item.status.value == "in_progress"
                ),
            },
            action=_action,
            raw_args=locals(),
        )
