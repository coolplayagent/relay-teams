# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from pydantic import JsonValue

from pydantic_ai import Agent

from relay_teams.paths import path_exists
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolResultProjection,
    execute_tool,
)
from relay_teams.tools.workspace_tools import ripgrep
from relay_teams.tools.workspace_tools.path_utils import resolve_workspace_glob_scope

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
    ) -> dict[str, JsonValue]:
        """Find workspace files whose paths match a glob pattern."""

        async def _action() -> ToolResultProjection:
            root, resolved_pattern, logical_prefix = resolve_workspace_glob_scope(
                ctx.deps.workspace,
                pattern,
            )

            if not path_exists(root):
                return _project_glob_result(
                    output="No files found",
                    truncated=False,
                    count=0,
                )

            files, truncated = await ripgrep.enumerate_files(
                cwd=root,
                pattern=resolved_pattern,
            )

            if not files:
                return _project_glob_result(
                    output="No files found",
                    truncated=False,
                    count=0,
                )

            rel_files = [str(f.relative_to(root)) for f in files]
            if logical_prefix is not None:
                rel_files = [
                    str((Path(logical_prefix) / relative_path).as_posix())
                    for relative_path in rel_files
                ]
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
            args_summary={"pattern": pattern},
            action=_action,
        )
