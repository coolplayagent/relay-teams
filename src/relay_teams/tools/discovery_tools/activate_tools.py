# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.agents.instances.models import RuntimeToolsSnapshot
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.registry.runtime_activation import apply_tool_activation
from relay_teams.tools.runtime import ToolContext, ToolDeps, execute_tool_call

DESCRIPTION = load_tool_description(__file__)
_MAX_ACTIVE_TOOLS = 20


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def activate_tools(
        ctx: ToolContext,
        tool_names: list[str] | str,
    ) -> dict[str, JsonValue]:
        """Activate runtime-authorized local tools for future model turns."""

        def _action(tool_names: list[str] | str) -> dict[str, JsonValue]:
            return _activate_runtime_tools(
                ctx=ctx,
                tool_names=tool_names,
            )

        summary_tool_names: JsonValue = (
            tool_names
            if isinstance(tool_names, str)
            else [str(name) for name in tool_names]
        )

        return await execute_tool_call(
            ctx,
            tool_name="activate_tools",
            args_summary={"tool_names": summary_tool_names},
            action=_action,
            raw_args=locals(),
        )


def _activate_runtime_tools(
    *,
    ctx: ToolContext,
    tool_names: list[str] | str,
) -> dict[str, JsonValue]:
    requested_tool_names = _normalize_requested_tool_names(tool_names)
    if not requested_tool_names:
        return {
            "activated": [],
            "already_active": [],
            "unknown_or_unauthorized": [],
            "rejected_due_to_limit": [],
            "active_tools": [],
            "active_tools_count": 0,
            "deferred_tools_count": 0,
            "warning": "tool_names must contain at least one non-empty tool name.",
        }

    try:
        runtime_record = ctx.deps.agent_repo.get_instance(ctx.deps.instance_id)
    except KeyError:
        return {
            "activated": [],
            "already_active": [],
            "unknown_or_unauthorized": list(requested_tool_names),
            "rejected_due_to_limit": [],
            "active_tools": [],
            "active_tools_count": 0,
            "deferred_tools_count": 0,
            "warning": "Runtime tool snapshot is unavailable for the current instance.",
        }

    snapshot = _parse_runtime_tools_snapshot(runtime_record.runtime_tools_json)
    authorized_local_tools = tuple(entry.name for entry in snapshot.local_tools)
    activation_result = apply_tool_activation(
        authorized_tools=authorized_local_tools,
        active_tools=_parse_runtime_active_tools_json(
            runtime_record.runtime_active_tools_json
        ),
        requested_tool_names=requested_tool_names,
        max_active_tools=_MAX_ACTIVE_TOOLS,
    )

    next_runtime_active_tools_json = json.dumps(
        list(activation_result.active_tools),
        ensure_ascii=False,
        indent=2,
    )
    ctx.deps.agent_repo.update_runtime_snapshot(
        ctx.deps.instance_id,
        runtime_system_prompt=runtime_record.runtime_system_prompt,
        runtime_tools_json=runtime_record.runtime_tools_json,
        runtime_active_tools_json=next_runtime_active_tools_json,
    )
    warning = _build_warning_message(activation_result)
    return {
        "activated": list(activation_result.activated),
        "already_active": list(activation_result.already_active),
        "unknown_or_unauthorized": list(activation_result.unknown_or_unauthorized),
        "rejected_due_to_limit": list(activation_result.rejected_due_to_limit),
        "active_tools": list(activation_result.active_tools),
        "active_tools_count": len(activation_result.active_tools),
        "deferred_tools_count": len(activation_result.deferred_tools),
        "warning": warning,
    }


def _normalize_requested_tool_names(
    tool_names: list[str] | str,
) -> tuple[str, ...]:
    if isinstance(tool_names, str):
        normalized_name = tool_names.strip()
        return (normalized_name,) if normalized_name else ()
    return tuple(str(name).strip() for name in tool_names if str(name).strip())


def _parse_runtime_tools_snapshot(raw_snapshot: str) -> RuntimeToolsSnapshot:
    normalized_snapshot = raw_snapshot.strip()
    if not normalized_snapshot:
        return RuntimeToolsSnapshot()
    try:
        return RuntimeToolsSnapshot.model_validate_json(normalized_snapshot)
    except (ValueError, TypeError):
        return RuntimeToolsSnapshot()


def _parse_runtime_active_tools_json(raw_active_tools: str) -> tuple[str, ...]:
    normalized_active_tools = raw_active_tools.strip()
    if not normalized_active_tools:
        return ()
    try:
        parsed = json.loads(normalized_active_tools)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(item for item in parsed if isinstance(item, str))


def _build_warning_message(activation_result: object) -> str | None:
    result = activation_result
    warning_parts: list[str] = []
    unknown_or_unauthorized = getattr(result, "unknown_or_unauthorized", ())
    rejected_due_to_limit = getattr(result, "rejected_due_to_limit", ())
    if unknown_or_unauthorized:
        warning_parts.append(
            "Some requested tools were not authorized local tools for this runtime: "
            + ", ".join(str(name) for name in unknown_or_unauthorized)
        )
    if rejected_due_to_limit:
        warning_parts.append(
            "Some requested tools could not be activated because the active-tool "
            f"limit ({_MAX_ACTIVE_TOOLS}) was reached: "
            + ", ".join(str(name) for name in rejected_due_to_limit)
        )
    return " ".join(warning_parts) if warning_parts else None
