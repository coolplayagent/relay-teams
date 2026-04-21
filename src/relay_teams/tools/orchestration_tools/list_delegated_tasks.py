from __future__ import annotations

from pydantic import JsonValue

from pydantic_ai import Agent

from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime import ToolContext, ToolDeps, execute_tool

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def orch_list_delegated_tasks(
        ctx: ToolContext,
        include_root: bool = False,
    ) -> dict[str, JsonValue]:
        """List delegated tasks associated with the current run."""

        def _action() -> dict[str, JsonValue]:
            return ctx.deps.task_service.list_delegated_tasks(
                run_id=ctx.deps.run_id,
                include_root=include_root,
            )

        return await execute_tool(
            ctx,
            tool_name="orch_list_delegated_tasks",
            args_summary={"include_root": include_root},
            action=_action,
        )
