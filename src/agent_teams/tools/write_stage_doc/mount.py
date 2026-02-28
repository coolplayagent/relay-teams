from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.stage_docs import current_stage_doc_path, write_stage_doc_once
from agent_teams.tools.tool_helpers import execute_tool


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def write_stage_doc(ctx: ToolContext, content: str) -> str:
        def _action() -> str:
            if not content.strip():
                raise ValueError("content must not be empty")
            path = current_stage_doc_path(
                workspace_root=ctx.deps.workspace_root,
                run_id=ctx.deps.run_id,
                role_id=ctx.deps.role_id,
            )
            write_stage_doc_once(path=path, content=content)
            return str(path.relative_to(ctx.deps.workspace_root))

        return execute_tool(
            ctx,
            tool_name="write_stage_doc",
            args_summary={"role_id": ctx.deps.role_id, "content_len": len(content)},
            action=_action,
        )
