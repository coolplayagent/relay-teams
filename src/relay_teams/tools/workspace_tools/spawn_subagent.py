# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolResultProjection,
    execute_tool_call,
)
from relay_teams.tools.workspace_tools.background_task_tool_support import (
    project_background_task_tool_result,
    require_background_task_service,
)

DESCRIPTION = load_tool_description(__file__)
_ROLE_REGISTRY_ATTR = "_agent_teams_role_registry"
_AVAILABLE_SUBAGENTS_HEADING = "Available Subagent Capabilities"
_NONE_LABEL = "none"


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=_build_spawn_subagent_description(agent))
    async def spawn_subagent(
        ctx: ToolContext,
        role_id: str,
        description: str,
        prompt: str,
        background: bool = False,
    ) -> dict[str, JsonValue]:
        async def _action(
            role_id: str,
            description: str,
            prompt: str,
            background: bool = False,
        ) -> ToolResultProjection:
            service = require_background_task_service(ctx)
            resolved_role_id = ctx.deps.role_registry.resolve_subagent_role_id(role_id)
            if background:
                record = await service.start_subagent(
                    run_id=ctx.deps.run_id,
                    session_id=ctx.deps.session_id,
                    instance_id=ctx.deps.instance_id,
                    role_id=ctx.deps.role_id,
                    tool_call_id=ctx.tool_call_id,
                    workspace_id=ctx.deps.workspace.ref.workspace_id,
                    cwd=ctx.deps.workspace.resolve_workdir(),
                    subagent_role_id=resolved_role_id,
                    title=description,
                    prompt=prompt,
                )
                return project_background_task_tool_result(
                    record,
                    completed=False,
                    include_task_id=True,
                )
            result = await service.run_subagent(
                run_id=ctx.deps.run_id,
                session_id=ctx.deps.session_id,
                workspace_id=ctx.deps.workspace.ref.workspace_id,
                subagent_role_id=resolved_role_id,
                title=description,
                prompt=prompt,
            )
            visible_payload: dict[str, JsonValue] = {
                "completed": True,
                "output": result.output,
            }
            return ToolResultProjection(
                visible_data=visible_payload,
                internal_data={
                    **visible_payload,
                    "run_id": result.run_id,
                    "instance_id": result.instance_id,
                    "role_id": result.role_id,
                    "task_id": result.task_id,
                    "title": result.title,
                },
            )

        return await execute_tool_call(
            ctx,
            tool_name="spawn_subagent",
            args_summary={
                "role_id": role_id,
                "background": background,
                "description_len": len(description),
                "prompt_len": len(prompt),
            },
            action=_action,
            raw_args=locals(),
        )


def _build_spawn_subagent_description(agent: Agent[ToolDeps, str]) -> str:
    role_registry = _role_registry_from_agent(agent)
    capability_block = _build_subagent_capability_block(role_registry)
    if not capability_block:
        return DESCRIPTION
    return f"{DESCRIPTION}\n\n{capability_block}"


def _role_registry_from_agent(
    agent: Agent[ToolDeps, str],
) -> RoleRegistry | None:
    registry = getattr(agent, _ROLE_REGISTRY_ATTR, None)
    if isinstance(registry, RoleRegistry):
        return registry
    return None


def _build_subagent_capability_block(role_registry: RoleRegistry | None) -> str:
    if role_registry is None:
        return f"## {_AVAILABLE_SUBAGENTS_HEADING}\n{_NONE_LABEL}"
    roles = role_registry.list_subagent_roles()
    if not roles:
        return f"## {_AVAILABLE_SUBAGENTS_HEADING}\n{_NONE_LABEL}"
    blocks = [f"## {_AVAILABLE_SUBAGENTS_HEADING}"]
    for role in roles:
        blocks.append(f"### {role.role_id}")
        blocks.append(f"- Name: {role.name}")
        blocks.append(f"- Description: {role.description}")
        blocks.append(f"- Tools: {_format_names(role.tools)}")
        blocks.append(f"- MCP Servers: {_format_names(role.mcp_servers)}")
        blocks.append(f"- Skills: {_format_names(role.skills)}")
        blocks.append("")
    return "\n".join(blocks).rstrip()


def _format_names(names: tuple[str, ...]) -> str:
    if not names:
        return _NONE_LABEL
    return ", ".join(names)
