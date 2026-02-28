from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.stage_docs import previous_stage_doc_path
from agent_teams.tools.tool_helpers import execute_tool


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def read_stage_input(ctx: ToolContext) -> str:
        def _action() -> str:
            if ctx.deps.role_id == "spec_spec":
                task = ctx.deps.task_repo.get(ctx.deps.task_id)
                parent = task.envelope.parent_instruction or ""
                return f"Requirement:\n{task.envelope.objective}\n\nParentInstruction:\n{parent}"

            try:
                path = previous_stage_doc_path(
                    workspace_root=ctx.deps.workspace_root,
                    run_id=ctx.deps.run_id,
                    role_id=ctx.deps.role_id,
                )
                if path.exists() and path.is_file():
                    return path.read_text(encoding="utf-8")
            except ValueError:
                pass

            task = ctx.deps.task_repo.get(ctx.deps.task_id)
            return f"No previous stage document available.\n\nTaskObjective:\n{task.envelope.objective}\n\nParentInstruction:\n{task.envelope.parent_instruction or 'None'}"

        return execute_tool(
            ctx,
            tool_name="read_stage_input",
            args_summary={"role_id": ctx.deps.role_id},
            action=_action,
        )
