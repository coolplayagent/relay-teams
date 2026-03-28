# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from pydantic_ai import Agent

from agent_teams.tools._description_loader import load_tool_description
from agent_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolResultProjection,
    execute_tool,
)
from agent_teams.tools.workspace_tools import ripgrep

DESCRIPTION = load_tool_description(__file__)


def _project_glob_result(
    *,
    output: str,
    truncated: bool,
    count: int,
) -> ToolResultProjection:
    visible_data: dict[str, JsonValue] = {
        "output": output,
        "truncated": truncated,
        "count": count,
    }
    return ToolResultProjection(
        visible_data=visible_data,
        internal_data=dict(visible_data),
    )


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def glob(
        ctx: ToolContext,
        pattern: str,
        path: str = ".",
    ) -> dict[str, JsonValue]:
        """Find files whose paths match a glob pattern under a readable root."""

        async def _action() -> ToolResultProjection:
            root = ctx.deps.workspace.resolve_read_path(path)
            if not root.exists():
                raise ValueError(f"Path not found: {path}")
            if not root.is_dir():
                raise ValueError(f"Path is not a directory: {path}")

            files, truncated = await ripgrep.enumerate_files(
                cwd=root,
                pattern=pattern,
            )

            if not files:
                return _project_glob_result(
                    output="No files found",
                    truncated=False,
                    count=0,
                )

            rel_files = [str(f.relative_to(root)) for f in files]
            output = "\n".join(rel_files)

            if truncated:
                output += f"\n\n(Results truncated: showing first {len(files)} files)"

            return _project_glob_result(
                output=output,
                truncated=truncated,
                count=len(files),
            )

        return await execute_tool(
            ctx,
            tool_name="glob",
            args_summary={"pattern": pattern, "path": path},
            action=_action,
        )
