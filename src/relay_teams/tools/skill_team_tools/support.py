# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from relay_teams.roles.role_models import RoleDefinition
from relay_teams.skills.skill_models import Skill
from relay_teams.skills.skill_team_roles import SkillTeamRoleSummary
from relay_teams.tools.runtime.context import (
    SkillRegistryLike,
    ToolContext,
)


def resolve_authorized_skill_for_tool(
    *,
    ctx: ToolContext,
    skill_name: str,
    tool_name: str,
) -> Skill:
    requested_name = skill_name.strip()
    if not requested_name:
        raise ValueError("skill_name must not be empty")
    skill_registry = require_skill_registry(ctx)
    role = get_effective_role_for_skill_tool(ctx)
    resolved_name = skill_registry.resolve_authorized_name_for_role(
        role=role,
        requested_name=requested_name,
        consumer=f"tools.{tool_name}.role:{role.role_id}",
    )
    if resolved_name is None:
        raise PermissionError(
            f"Role {role.role_id} is not authorized to load skill: {requested_name}"
        )
    skill = skill_registry.get_skill_definition(resolved_name)
    if skill is None:
        raise KeyError(f"Skill not found: {requested_name}")
    return cast(Skill, skill)


def require_skill_registry(ctx: ToolContext) -> SkillRegistryLike:
    skill_registry = ctx.deps.skill_registry
    if skill_registry is None:
        raise RuntimeError("Skill registry is unavailable")
    return skill_registry


def get_effective_role_for_skill_tool(ctx: ToolContext) -> RoleDefinition:
    runtime_role_resolver = ctx.deps.runtime_role_resolver
    if runtime_role_resolver is not None:
        try:
            return runtime_role_resolver.get_effective_role(
                run_id=ctx.deps.run_id,
                role_id=ctx.deps.role_id,
            )
        except KeyError:
            pass
    return ctx.deps.role_registry.get(ctx.deps.role_id)


def skill_team_role_summary_to_json(
    summary: SkillTeamRoleSummary,
) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], summary.model_dump(mode="json"))
