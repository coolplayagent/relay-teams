from __future__ import annotations

from json import dumps
from typing import Literal

from pydantic_ai import Agent

from agent_teams.core.enums import InjectionSource
from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.tool_helpers import execute_tool


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def communicate(
        ctx: ToolContext,
        mode: Literal["unicast", "broadcast"],
        message: str,
        recipient_instance_id: str | None = None,
    ) -> str:
        def _action() -> str:
            running = ctx.deps.agent_repo.list_running(ctx.deps.run_id)
            running_ids = {item.instance_id for item in running}

            recipients: list[str] = []
            if mode == "unicast":
                if recipient_instance_id is None:
                    raise ValueError("recipient_instance_id is required for unicast")
                if recipient_instance_id not in running_ids:
                    raise ValueError(
                        f"Recipient is not running: {recipient_instance_id}"
                    )
                recipients = [recipient_instance_id]
            elif mode == "broadcast":
                recipients = [
                    item.instance_id
                    for item in running
                    if item.instance_id != ctx.deps.instance_id
                ]

            for target in recipients:
                ctx.deps.injection_manager.enqueue(
                    run_id=ctx.deps.run_id,
                    recipient_instance_id=target,
                    source=InjectionSource.SUBAGENT,
                    content=message,
                    sender_instance_id=ctx.deps.instance_id,
                    sender_role_id=ctx.deps.role_id,
                )

            return dumps(
                {"mode": mode, "recipients": recipients, "count": len(recipients)}
            )

        return execute_tool(
            ctx,
            tool_name="communicate",
            args_summary={
                "mode": mode,
                "recipient_instance_id": recipient_instance_id,
                "message_len": len(message),
            },
            action=_action,
        )
