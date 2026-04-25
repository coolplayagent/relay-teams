# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime.context import (
    ToolContext,
    ToolDeps,
)
from relay_teams.tools.runtime.execution import execute_tool
from relay_teams.tools.runtime.models import ToolResultProjection
from relay_teams.tools.workspace_tools.notebook import (
    CellType,
    EditMode,
    notebook_edit_file_with_guard,
)

DESCRIPTION = load_tool_description(__file__)


def _project_notebook_edit_result(
    result: dict[str, JsonValue],
) -> ToolResultProjection:
    return ToolResultProjection(
        visible_data={"output": result["output"]},
        internal_data=result,
    )


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def notebook_edit(
        ctx: ToolContext,
        path: str,
        new_source: str,
        cell_id: str | None = None,
        cell_type: CellType | None = None,
        edit_mode: EditMode = "replace",
    ) -> dict[str, JsonValue]:
        """Edit a Jupyter notebook cell without editing raw JSON."""

        async def _action() -> ToolResultProjection:
            file_path = ctx.deps.workspace.resolve_path(path, write=True)
            result = notebook_edit_file_with_guard(
                shared_store=ctx.deps.shared_store,
                session_id=ctx.deps.session_id,
                conversation_id=ctx.deps.conversation_id,
                file_path=file_path,
                cell_id=cell_id,
                new_source=new_source,
                cell_type=cell_type,
                edit_mode=edit_mode,
            )
            return _project_notebook_edit_result(result)

        return await execute_tool(
            ctx,
            tool_name="notebook_edit",
            args_summary={
                "path": path,
                "cell_id": cell_id,
                "new_source_len": len(new_source),
                "cell_type": cell_type,
                "edit_mode": edit_mode,
            },
            action=_action,
        )
