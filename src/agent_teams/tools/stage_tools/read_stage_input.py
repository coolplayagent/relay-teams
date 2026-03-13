# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def read_stage_input(ctx: ToolContext) -> dict[str, JsonValue]:
        def _action() -> str:
            if ctx.deps.role_id == "spec_spec":
                task = ctx.deps.task_repo.get(ctx.deps.task_id)
                return f"Requirement:\n{task.envelope.objective}"

            try:
                path = ctx.deps.workspace.artifacts.previous_stage_doc_path(
                    run_id=ctx.deps.run_id,
                    role_id=ctx.deps.role_id,
                )
                if path.exists() and path.is_file():
                    return path.read_text(encoding="utf-8")
            except ValueError:
                pass

            task = ctx.deps.task_repo.get(ctx.deps.task_id)
            return (
                "No previous stage document available.\n\n"
                f"TaskObjective:\n{task.envelope.objective}"
            )

        return await execute_tool(
            ctx,
            tool_name="read_stage_input",
            args_summary={"role_id": ctx.deps.role_id},
            action=_action,
        )
