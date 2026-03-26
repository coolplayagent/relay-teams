# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue
from pydantic_ai import Agent

from agent_teams.tools._description_loader import load_tool_description
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def im_send(
        ctx: ToolContext,
        text: str | None = None,
        file_path: str | None = None,
    ) -> dict[str, JsonValue]:
        """Send a text message or file to the IM chat for this session."""

        async def _action() -> dict[str, JsonValue]:
            service = ctx.deps.im_tool_service
            if service is None:
                raise RuntimeError("IM send is not available in this session.")
            if text is None and file_path is None:
                raise ValueError("Provide at least one of text or file_path.")
            results: list[JsonValue] = []
            if text is not None:
                result = service.send_text(
                    session_id=ctx.deps.session_id,
                    text=text,
                )
                results.append(result)
            if file_path is not None:
                resolved_path = ctx.deps.workspace.resolve_path(file_path, write=False)
                result = service.send_file(
                    session_id=ctx.deps.session_id,
                    file_path=resolved_path,
                )
                results.append(result)
            return {"status": "ok", "details": results}

        return await execute_tool(
            ctx,
            tool_name="im_send",
            args_summary={
                "text": text[:80] if text else None,
                "file_path": file_path,
            },
            action=_action,
        )
