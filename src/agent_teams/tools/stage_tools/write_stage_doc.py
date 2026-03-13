# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool
from agent_teams.tools.stage_tools.docs import write_stage_doc_once


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def write_stage_doc(ctx: ToolContext, content: str) -> dict[str, JsonValue]:
        def _action() -> str:
            if not content.strip():
                raise ValueError("content must not be empty")
            path = write_stage_doc_once(
                workspace=ctx.deps.workspace,
                session_id=ctx.deps.session_id,
                role_id=ctx.deps.role_id,
                content=content,
            )
            return str(path.relative_to(ctx.deps.workspace.root_path))

        return await execute_tool(
            ctx,
            tool_name="write_stage_doc",
            args_summary={"role_id": ctx.deps.role_id, "content_len": len(content)},
            action=_action,
        )
