# -*- coding: utf-8 -*-
from __future__ import annotations

from functools import partial
from typing import Protocol, cast

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.roles.role_models import RoleDefinition
from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.generated_tools import (
    AUTO_HARNESS_SYNTHESIZE_TOOL,
    GeneratedToolSynthesisResult,
    GeneratedToolTestCase,
)
from relay_teams.tools.runtime.context import ToolContext, ToolDeps
from relay_teams.tools.runtime.execution import execute_tool_call

DESCRIPTION = load_tool_description(__file__)


class AutoHarnessSynthesisServiceLike(Protocol):
    async def synthesize_tool(
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
    ) -> GeneratedToolSynthesisResult:
        raise NotImplementedError


async def _resolve_current_role(ctx: ToolContext) -> RoleDefinition:
    resolver = ctx.deps.runtime_role_resolver
    if resolver is not None:
        return await resolver.get_effective_role_async(
            run_id=ctx.deps.run_id,
            role_id=ctx.deps.role_id,
        )
    return ctx.deps.role_registry.get(ctx.deps.role_id)


async def _run_synthesize_action(
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
    service = cast(AutoHarnessSynthesisServiceLike, raw_service)
    result = await service.synthesize_tool(
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
        test_cases=tuple(test_cases),
        target_role_id=target_role_id,
        thinking=RunThinkingConfig(),
    )
    return result.model_dump(mode="json")


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def auto_harness_synthesize_tool(
        ctx: ToolContext,
        tool_name: str,
        description: str,
        input_schema: dict[str, JsonValue],
        behavior: str,
        test_cases: list[GeneratedToolTestCase],
        target_role_id: str | None = None,
    ) -> dict[str, JsonValue]:
        """Generate and test a pending role-owned utility tool."""

        action = partial(_run_synthesize_action, ctx)
        return await execute_tool_call(
            ctx,
            tool_name=AUTO_HARNESS_SYNTHESIZE_TOOL,
            args_summary={
                "tool_name": tool_name,
                "description_len": len(description),
                "behavior_len": len(behavior),
                "test_count": len(test_cases),
                "target_role_id": target_role_id,
            },
            action=action,
            raw_args=locals(),
        )
