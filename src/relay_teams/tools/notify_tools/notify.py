# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.computer import ComputerActionRisk, ExecutionSurface
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.notify_tools.models import NotifyProvider, NotifyTarget
from relay_teams.tools.notify_tools.models import NotifyResolution
from relay_teams.tools.notify_tools.xiaoluban import (
    resolve_xiaoluban_notify_targets,
    send_xiaoluban_notify,
)
from relay_teams.tools.runtime.context import ToolContext, ToolDeps
from relay_teams.tools.runtime.execution import execute_tool
from relay_teams.tools.runtime.models import ToolApprovalRequest, ToolExecutionError

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def notify(
        ctx: ToolContext,
        provider: NotifyProvider,
        message: str,
        target: NotifyTarget = NotifyTarget.OWNER,
        account: str | None = None,
        recipients: tuple[str, ...] = (),
    ) -> dict[str, JsonValue]:
        """Send a proactive notification through a configured provider."""

        async def _action(tool_input: dict[str, JsonValue]) -> dict[str, JsonValue]:
            return await _execute_notify_action(ctx, tool_input)

        return await execute_tool(
            ctx,
            tool_name="notify",
            args_summary={
                "provider": provider.value,
                "message": message[:80],
                "target": target.value,
                "account": account,
                "recipients": list(recipients),
            },
            tool_input={
                "provider": provider.value,
                "message": message,
                "target": target.value,
                "account": account,
                "recipients": list(recipients),
            },
            approval_request_factory=lambda tool_input: build_notify_approval_request(
                ctx,
                tool_input,
            ),
            approval_args_summary_factory=_approval_args_summary,
            action=_action,
        )


async def _execute_notify_action(
    ctx: ToolContext,
    tool_input: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    _ = NotifyProvider(str(tool_input["provider"]))
    resolved_target = NotifyTarget(str(tool_input.get("target") or "owner"))
    resolved_message = str(tool_input["message"])
    resolution = resolve_xiaoluban_notify_targets(
        ctx,
        account=_optional_text(tool_input.get("account")),
        target=resolved_target,
        recipients=_recipient_tuple(tool_input.get("recipients")),
    )
    result = await send_xiaoluban_notify(
        ctx,
        message=resolved_message,
        target=resolved_target,
        resolution=resolution,
    )
    return result.to_payload()


def build_notify_approval_request(
    ctx: ToolContext,
    tool_input: dict[str, JsonValue],
) -> ToolApprovalRequest | None:
    try:
        resolved = _resolve_input(ctx, tool_input)
    except ToolExecutionError:
        return None
    if not resolved.includes_group:
        return None
    return ToolApprovalRequest(
        risk_level=ComputerActionRisk.GUARDED,
        target_summary=resolved.target_summary(),
        source="notify:xiaoluban",
        execution_surface=ExecutionSurface.API,
        metadata={
            "provider": NotifyProvider.XIAOLUBAN.value,
            "account_id": resolved.account.account_id,
            "group_target_count": sum(
                1
                for recipient in resolved.recipients
                if recipient.kind.value == "group"
            ),
            "filtered_recipients": list(resolved.filtered_recipients),
        },
    )


def _resolve_input(
    ctx: ToolContext,
    tool_input: dict[str, JsonValue],
) -> NotifyResolution:
    _ = NotifyProvider(str(tool_input["provider"]))
    return resolve_xiaoluban_notify_targets(
        ctx,
        account=_optional_text(tool_input.get("account")),
        target=NotifyTarget(str(tool_input.get("target") or "owner")),
        recipients=_recipient_tuple(tool_input.get("recipients")),
    )


def _approval_args_summary(tool_input: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {
        "provider": str(tool_input.get("provider") or ""),
        "target": str(tool_input.get("target") or "owner"),
        "account": _optional_text(tool_input.get("account")),
        "recipient_count": len(_recipient_tuple(tool_input.get("recipients"))),
    }


def _optional_text(value: JsonValue | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _recipient_tuple(value: JsonValue | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    if isinstance(value, tuple):
        return tuple(str(item) for item in value)
    if isinstance(value, str):
        return (value,)
    return (str(value),)


__all__ = ["build_notify_approval_request", "register"]
