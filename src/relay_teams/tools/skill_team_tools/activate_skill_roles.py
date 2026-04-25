# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.roles.temporary_role_models import TemporaryRoleSource
from relay_teams.skills.skill_team_roles import (
    build_skill_team_role_spec,
    list_skill_team_roles,
)
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime.context import ToolContext, ToolDeps
from relay_teams.tools.runtime.execution import execute_tool_call
from relay_teams.tools.skill_team_tools.support import (
    resolve_authorized_skill_for_tool,
    skill_team_role_summary_to_json,
)

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def activate_skill_roles(
        ctx: ToolContext,
        skill_name: str,
        role_ids: list[str],
    ) -> dict[str, JsonValue]:
        def _action(skill_name: str, role_ids: list[str]) -> dict[str, JsonValue]:
            runtime_role_resolver = ctx.deps.runtime_role_resolver
            if runtime_role_resolver is None:
                raise RuntimeError("Temporary role activation is unavailable")
            requested_role_ids = _normalize_requested_role_ids(role_ids)
            skill = resolve_authorized_skill_for_tool(
                ctx=ctx,
                skill_name=skill_name,
                tool_name="activate_skill_roles",
            )
            role_map = {
                entry.summary.role_id: entry for entry in list_skill_team_roles(skill)
            }
            missing_role_ids = [
                role_id for role_id in requested_role_ids if role_id not in role_map
            ]
            if missing_role_ids:
                raise ValueError(
                    f"Skill roles not found for {skill.metadata.name}: {missing_role_ids}"
                )
            activated_roles: list[JsonValue] = []
            for requested_role_id in requested_role_ids:
                entry = role_map[requested_role_id]
                activated_role = runtime_role_resolver.create_temporary_role(
                    run_id=ctx.deps.run_id,
                    session_id=ctx.deps.session_id,
                    source=TemporaryRoleSource.SKILL_TEAM,
                    role=build_skill_team_role_spec(
                        skill=skill,
                        role=entry.role,
                    ),
                )
                summary = entry.summary.model_copy(
                    update={
                        "effective_role_id": activated_role.role_id,
                        "name": activated_role.name,
                        "description": activated_role.description,
                        "tools": activated_role.tools,
                        "mcp_servers": activated_role.mcp_servers,
                        "skills": activated_role.skills,
                        "model_profile": activated_role.model_profile,
                    }
                )
                activated_roles.append(skill_team_role_summary_to_json(summary))
            return {
                "skill": {
                    "name": skill.metadata.name,
                    "ref": skill.ref,
                    "source": skill.source.value,
                },
                "activated_roles": activated_roles,
            }

        return await execute_tool_call(
            ctx,
            tool_name="activate_skill_roles",
            args_summary={"skill_name": skill_name, "role_count": len(role_ids)},
            action=_action,
            raw_args=locals(),
        )


def _normalize_requested_role_ids(role_ids: list[str]) -> tuple[str, ...]:
    normalized_role_ids: list[str] = []
    for role_id in role_ids:
        normalized = role_id.strip()
        if not normalized:
            continue
        if normalized not in normalized_role_ids:
            normalized_role_ids.append(normalized)
    if not normalized_role_ids:
        raise ValueError("role_ids must contain at least one role id")
    return tuple(normalized_role_ids)
