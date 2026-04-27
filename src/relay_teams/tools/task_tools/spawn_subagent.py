# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.roles.role_models import RoleDefinition, RoleMode
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.role_registry import is_reserved_system_role_definition
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime.context import (
    ToolContext,
    ToolDeps,
)
from relay_teams.tools.runtime.execution import execute_tool_call
from relay_teams.tools.runtime.models import ToolResultProjection
from relay_teams.tools.runtime.persisted_state import update_tool_call_call_state_async
from relay_teams.tools.workspace_tools.background_task_tool_support import (
    project_background_task_tool_result,
    require_background_task_service,
)
from relay_teams.sessions.runs.background_tasks.models import BackgroundTaskRecord

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
            resolved_role_id, role_snapshot = _resolve_subagent_role(ctx, role_id)
            if background:

                async def _on_background_launch_prepared(
                    prepared_record: BackgroundTaskRecord,
                ) -> None:
                    await _persist_spawn_subagent_call_state(
                        ctx=ctx,
                        record=prepared_record,
                        requested_role_id=role_id,
                        resolved_role_id=resolved_role_id,
                        description=description,
                        prompt=prompt,
                        background=True,
                    )

                record = await service.start_subagent(
                    run_id=ctx.deps.run_id,
                    session_id=ctx.deps.session_id,
                    instance_id=ctx.deps.instance_id,
                    role_id=ctx.deps.role_id,
                    tool_call_id=ctx.tool_call_id,
                    workspace_id=ctx.deps.workspace.ref.workspace_id,
                    cwd=ctx.deps.workspace.resolve_workdir(),
                    subagent_role_id=resolved_role_id,
                    subagent_role=role_snapshot,
                    title=description,
                    prompt=prompt,
                    on_launch_prepared=_on_background_launch_prepared,
                )
                return project_background_task_tool_result(
                    record,
                    completed=False,
                    include_task_id=True,
                )

            async def _on_launch_prepared(
                prepared_record: BackgroundTaskRecord,
            ) -> None:
                await _persist_spawn_subagent_call_state(
                    ctx=ctx,
                    record=prepared_record,
                    requested_role_id=role_id,
                    resolved_role_id=resolved_role_id,
                    description=description,
                    prompt=prompt,
                    background=False,
                )

            result = await service.run_subagent(
                run_id=ctx.deps.run_id,
                session_id=ctx.deps.session_id,
                workspace_id=ctx.deps.workspace.ref.workspace_id,
                tool_call_id=ctx.tool_call_id,
                parent_instance_id=ctx.deps.instance_id,
                parent_role_id=ctx.deps.role_id,
                subagent_role_id=resolved_role_id,
                subagent_role=role_snapshot,
                title=description,
                prompt=prompt,
                on_launch_prepared=_on_launch_prepared,
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


async def _persist_spawn_subagent_call_state(
    *,
    ctx: ToolContext,
    record: BackgroundTaskRecord,
    requested_role_id: str,
    resolved_role_id: str,
    description: str,
    prompt: str,
    background: bool,
) -> None:
    tool_call_id = str(ctx.tool_call_id or "").strip()
    if not tool_call_id:
        return
    try:
        shared_store = ctx.deps.shared_store
        task_id = ctx.deps.task_id
        instance_id = ctx.deps.instance_id
        parent_role_id = ctx.deps.role_id
    except AttributeError:
        return

    def mutate(current: dict[str, JsonValue]) -> dict[str, JsonValue]:
        next_state = dict(current)
        next_state.update(
            {
                "kind": (
                    "spawn_subagent_background" if background else "spawn_subagent_sync"
                ),
                "background": background,
                "background_task_id": record.background_task_id,
                "subagent_run_id": record.subagent_run_id or "",
                "subagent_instance_id": record.subagent_instance_id or "",
                "subagent_task_id": record.subagent_task_id or "",
                "subagent_role_id": record.subagent_role_id or resolved_role_id,
                "requested_role_id": requested_role_id,
                "resolved_role_id": resolved_role_id,
                "description": description,
                "title": record.title,
                "prompt": prompt,
            }
        )
        return next_state

    await update_tool_call_call_state_async(
        shared_store=shared_store,
        task_id=task_id,
        tool_call_id=tool_call_id,
        tool_name="spawn_subagent",
        instance_id=instance_id,
        role_id=parent_role_id,
        mutate=mutate,
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


def _resolve_subagent_role(
    ctx: ToolContext,
    role_id: str,
) -> tuple[str, RoleDefinition | None]:
    normalized_role_id = role_id.strip()
    if not normalized_role_id:
        raise ValueError("role_id must not be empty")
    runtime_role_resolver = ctx.deps.runtime_role_resolver
    if runtime_role_resolver is not None:
        try:
            role = runtime_role_resolver.get_temporary_role(
                run_id=ctx.deps.run_id,
                role_id=normalized_role_id,
            )
        except KeyError:
            pass
        else:
            _raise_if_role_unspawnable(ctx.deps.role_registry, role)
            return role.role_id, role
    return ctx.deps.role_registry.resolve_subagent_role_id(normalized_role_id), None


def _raise_if_role_unspawnable(
    role_registry: RoleRegistry,
    role: RoleDefinition,
) -> None:
    if role_registry.is_coordinator_role(role.role_id):
        raise ValueError(
            f"Coordinator role cannot be used as a subagent: {role.role_id}"
        )
    if role_registry.is_main_agent_role(role.role_id):
        raise ValueError(
            f"Main agent role cannot be used as a subagent: {role.role_id}"
        )
    if is_reserved_system_role_definition(role):
        raise ValueError(
            f"Reserved system role cannot be used as a subagent: {role.role_id}"
        )
    if role.mode not in {RoleMode.SUBAGENT, RoleMode.ALL}:
        raise ValueError(
            f"Role cannot be used as a subagent: {role.role_id} (mode={role.mode.value})"
        )


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
