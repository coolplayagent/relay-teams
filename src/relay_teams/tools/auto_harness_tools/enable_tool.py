# -*- coding: utf-8 -*-
from __future__ import annotations

from functools import partial
from typing import Protocol, cast

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.computer import ComputerActionRisk, ExecutionSurface
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.generated_tools import (
    AUTO_HARNESS_ENABLE_TOOL,
    GeneratedToolEnableResult,
)
from relay_teams.tools.runtime.context import ToolContext, ToolDeps
from relay_teams.tools.runtime.execution import execute_tool_call
from relay_teams.tools.runtime.models import ToolApprovalRequest

DESCRIPTION = load_tool_description(__file__)


class AutoHarnessEnableServiceLike(Protocol):
    async def enable_tool(
        self,
        *,
        current_role_id: str,
        tool_name: str,
        code_hash: str,
        target_role_id: str | None,
        run_id: str | None = None,
        instance_id: str | None = None,
        session_id: str | None = None,
    ) -> GeneratedToolEnableResult:
        raise NotImplementedError


def _build_enable_approval_request(
    *,
    tool_name: str,
    code_hash: str,
    target_role_id: str | None,
) -> ToolApprovalRequest:
    normalized_hash = code_hash.strip()
    target_cache_key = (target_role_id or "").strip()
    return ToolApprovalRequest(
        risk_level=ComputerActionRisk.GUARDED,
        target_summary=f"Enable generated role tool {tool_name}",
        source="auto_harness",
        execution_surface=ExecutionSurface.API,
        cache_key=f"auto_harness:{tool_name}:{normalized_hash}:{target_cache_key}",
        metadata={
            "tool_name": tool_name,
            "code_hash": normalized_hash,
            "target_role_id": target_role_id or "",
        },
    )


async def _run_enable_action(
    ctx: ToolContext,
    *,
    tool_name: str,
    code_hash: str,
    target_role_id: str | None = None,
) -> dict[str, JsonValue]:
    raw_service = ctx.deps.auto_harness_service
    if raw_service is None:
        raise RuntimeError("AutoHarness service is not configured")
    service = cast(AutoHarnessEnableServiceLike, raw_service)
    result = await service.enable_tool(
        current_role_id=ctx.deps.role_id,
        tool_name=tool_name,
        code_hash=code_hash,
        target_role_id=target_role_id,
        run_id=ctx.deps.run_id,
        instance_id=ctx.deps.instance_id,
        session_id=ctx.deps.session_id,
    )
    return result.model_dump(mode="json")


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def auto_harness_enable_tool(
        ctx: ToolContext,
        tool_name: str,
        code_hash: str,
        target_role_id: str | None = None,
    ) -> dict[str, JsonValue]:
        """Enable a previously synthesized generated role tool after approval."""

        action = partial(_run_enable_action, ctx)
        return await execute_tool_call(
            ctx,
            tool_name=AUTO_HARNESS_ENABLE_TOOL,
            args_summary={
                "tool_name": tool_name,
                "code_hash": code_hash,
                "target_role_id": target_role_id,
            },
            action=action,
            raw_args=locals(),
            approval_request_factory=lambda tool_input: _build_enable_approval_request(
                tool_name=str(tool_input.get("tool_name") or tool_name),
                code_hash=str(tool_input.get("code_hash") or code_hash),
                target_role_id=(
                    str(tool_input["target_role_id"])
                    if tool_input.get("target_role_id") is not None
                    else target_role_id
                ),
            ),
            approval_args_summary_factory=lambda tool_input: {
                "tool_name": str(tool_input.get("tool_name") or tool_name),
                "code_hash": str(tool_input.get("code_hash") or code_hash),
                "target_role_id": (
                    str(tool_input["target_role_id"])
                    if tool_input.get("target_role_id") is not None
                    else target_role_id
                ),
            },
            force_approval=True,
        )
