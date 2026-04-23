# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.monitors import (
    MonitorAction,
    MonitorActionType,
    MonitorRule,
    MonitorSourceKind,
)
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime.context import (
    ToolContext,
    ToolDeps,
)
from relay_teams.tools.runtime.execution import execute_tool
from relay_teams.tools.workspace_tools.background_task_tool_support import (
    require_background_task_service,
)
from relay_teams.tools.workspace_tools.monitor_tool_support import (
    project_monitor_tool_result,
    require_monitor_service,
)

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def create_monitor(
        ctx: ToolContext,
        background_task_id: str,
        event_names: tuple[str, ...] = ("background_task.line",),
        patterns: tuple[str, ...] = (),
        action_type: MonitorActionType = MonitorActionType.WAKE_INSTANCE,
        cooldown_seconds: int = 0,
        max_triggers: int | None = None,
        auto_stop_on_first_match: bool = False,
        case_sensitive: bool = False,
    ) -> dict[str, JsonValue]:
        def _action():
            background_task_service = require_background_task_service(ctx)
            _ = background_task_service.get_for_run(
                run_id=ctx.deps.run_id,
                background_task_id=background_task_id,
            )
            monitor_service = require_monitor_service(ctx)
            record = monitor_service.create_monitor(
                run_id=ctx.deps.run_id,
                session_id=ctx.deps.session_id,
                source_kind=MonitorSourceKind.BACKGROUND_TASK,
                source_key=background_task_id,
                rule=MonitorRule(
                    event_names=event_names,
                    text_patterns_any=patterns,
                    cooldown_seconds=cooldown_seconds,
                    max_triggers=max_triggers,
                    auto_stop_on_first_match=auto_stop_on_first_match,
                    case_sensitive=case_sensitive,
                ),
                action=MonitorAction(action_type=action_type),
                created_by_instance_id=ctx.deps.instance_id,
                created_by_role_id=ctx.deps.role_id,
                tool_call_id=ctx.tool_call_id,
            )
            return project_monitor_tool_result(record)

        return await execute_tool(
            ctx,
            tool_name="create_monitor",
            args_summary={
                "background_task_id": background_task_id,
                "event_names": list(event_names),
                "patterns": list(patterns),
                "action_type": action_type.value,
                "cooldown_seconds": cooldown_seconds,
                "max_triggers": max_triggers,
                "auto_stop_on_first_match": auto_stop_on_first_match,
                "case_sensitive": case_sensitive,
            },
            action=_action,
        )
