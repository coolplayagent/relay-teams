# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from typing import Protocol, cast

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.computer import ComputerActionRisk, ExecutionSurface
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.generated_tools import (
    AUTO_HARNESS_UPGRADE_TOOL,
    GeneratedToolTestCase,
    GeneratedToolUpgradeResult,
)
from relay_teams.tools.runtime.context import ToolContext, ToolDeps
from relay_teams.tools.runtime.execution import execute_tool_call
from relay_teams.tools.runtime.models import ToolApprovalRequest

DESCRIPTION = load_tool_description(__file__)


class AutoHarnessUpgradeServiceLike(Protocol):
    async def upgrade_tool(
        self,
        *,
        role: RoleDefinition,
        session_id: str,
        run_id: str,
        task_id: str,
        workspace_id: str,
        conversation_id: str,
        instance_id: str,
        tool_name: str,
        description: str,
        input_schema: dict[str, JsonValue],
        behavior: str,
        test_cases: tuple[GeneratedToolTestCase, ...],
        target_role_id: str | None,
        thinking: RunThinkingConfig,
    ) -> GeneratedToolUpgradeResult:
        raise NotImplementedError


async def _resolve_current_role(ctx: ToolContext) -> RoleDefinition:
    resolver = ctx.deps.runtime_role_resolver
    if resolver is not None:
        return await resolver.get_effective_role_async(
            run_id=ctx.deps.run_id,
            role_id=ctx.deps.role_id,
        )
    return ctx.deps.role_registry.get(ctx.deps.role_id)


def _coerce_test_cases(
    test_cases: Sequence[object],
) -> tuple[GeneratedToolTestCase, ...]:
    return tuple(
        item
        if isinstance(item, GeneratedToolTestCase)
        else GeneratedToolTestCase.model_validate(item)
        for item in test_cases
    )


def _build_upgrade_approval_request(
    *,
    tool_name: str,
    target_role_id: str | None,
) -> ToolApprovalRequest:
    target_cache_key = (target_role_id or "").strip()
    return ToolApprovalRequest(
        risk_level=ComputerActionRisk.GUARDED,
        target_summary=f"Upgrade generated role tool {tool_name}",
        source="auto_harness",
        execution_surface=ExecutionSurface.API,
        cache_key=f"auto_harness:upgrade:{tool_name}:{target_cache_key}",
        metadata={
            "tool_name": tool_name,
            "target_role_id": target_role_id or "",
        },
    )


async def _run_upgrade_action(
    ctx: ToolContext,
    *,
    tool_name: str,
    description: str,
    input_schema: dict[str, JsonValue],
    behavior: str,
    test_cases: list[GeneratedToolTestCase],
    target_role_id: str | None = None,
) -> dict[str, JsonValue]:
    raw_service = ctx.deps.auto_harness_service
    if raw_service is None:
        raise RuntimeError("AutoHarness service is not configured")
    service = cast(AutoHarnessUpgradeServiceLike, raw_service)
    coerced_test_cases = _coerce_test_cases(test_cases)
    result = await service.upgrade_tool(
        role=await _resolve_current_role(ctx),
        session_id=ctx.deps.session_id,
        run_id=ctx.deps.run_id,
        task_id=ctx.deps.task_id,
        workspace_id=ctx.deps.workspace_id,
        conversation_id=ctx.deps.conversation_id,
        instance_id=ctx.deps.instance_id,
        tool_name=tool_name,
        description=description,
        input_schema=input_schema,
        behavior=behavior,
        test_cases=coerced_test_cases,
        target_role_id=target_role_id,
        thinking=RunThinkingConfig(),
    )
    return result.model_dump(mode="json")


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def auto_harness_upgrade_tool(
        ctx: ToolContext,
        tool_name: str,
        description: str,
        input_schema: dict[str, JsonValue],
        behavior: str,
        test_cases: list[GeneratedToolTestCase],
        target_role_id: str | None = None,
    ) -> dict[str, JsonValue]:
        """Upgrade an enabled generated role tool with a new version."""

        action = partial(_run_upgrade_action, ctx)
        return await execute_tool_call(
            ctx,
            tool_name=AUTO_HARNESS_UPGRADE_TOOL,
            args_summary={
                "tool_name": tool_name,
                "description_len": len(description),
                "behavior_len": len(behavior),
                "test_count": len(test_cases),
                "target_role_id": target_role_id,
            },
            action=action,
            raw_args=locals(),
            approval_request_factory=lambda tool_input: _build_upgrade_approval_request(
                tool_name=str(tool_input.get("tool_name") or tool_name),
                target_role_id=(
                    str(tool_input["target_role_id"])
                    if tool_input.get("target_role_id") is not None
                    else target_role_id
                ),
            ),
            approval_args_summary_factory=lambda tool_input: {
                "tool_name": str(tool_input.get("tool_name") or tool_name),
                "target_role_id": (
                    str(tool_input["target_role_id"])
                    if tool_input.get("target_role_id") is not None
                    else target_role_id
                ),
            },
            force_approval=True,
        )
