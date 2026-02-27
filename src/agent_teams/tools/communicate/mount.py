from __future__ import annotations

from json import dumps

from pydantic_ai import Agent

from agent_teams.core.enums import InjectionSource
from agent_teams.tools.runtime import ToolDeps
from agent_teams.tools.tool_helpers import emit_tool_call, emit_tool_result, with_injections


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def communicate(ctx, mode: str, message: str, recipient_instance_id: str | None = None) -> str:
        emit_tool_call(ctx, 'communicate')
        running = ctx.deps.agent_repo.list_running(ctx.deps.run_id)
        running_ids = {item.instance_id for item in running}

        recipients: list[str] = []
        if mode == 'unicast':
            if recipient_instance_id is None:
                raise ValueError('recipient_instance_id is required for unicast')
            if recipient_instance_id not in running_ids:
                raise ValueError(f'Recipient is not running: {recipient_instance_id}')
            recipients = [recipient_instance_id]
        elif mode == 'broadcast':
            recipients = [item.instance_id for item in running if item.instance_id != ctx.deps.instance_id]
        else:
            raise ValueError('mode must be unicast or broadcast')

        for target in recipients:
            ctx.deps.injection_manager.enqueue(
                run_id=ctx.deps.run_id,
                recipient_instance_id=target,
                source=InjectionSource.SUBAGENT,
                content=message,
                sender_instance_id=ctx.deps.instance_id,
                sender_role_id=ctx.deps.role_id,
            )

        result_payload = dumps({'mode': mode, 'recipients': recipients, 'count': len(recipients)})
        result = with_injections(ctx, result_payload)
        emit_tool_result(ctx, 'communicate')
        return result
