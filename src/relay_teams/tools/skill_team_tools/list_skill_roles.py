# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.skills.skill_team_roles import list_skill_team_roles
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
    async def list_skill_roles(
        ctx: ToolContext,
        skill_name: str,
    ) -> dict[str, JsonValue]:
        def _action(skill_name: str) -> dict[str, JsonValue]:
            skill = resolve_authorized_skill_for_tool(
                ctx=ctx,
                skill_name=skill_name,
                tool_name="list_skill_roles",
            )
            roles = list_skill_team_roles(skill)
            role_payloads: list[JsonValue] = [
                skill_team_role_summary_to_json(entry.summary) for entry in roles
            ]
            return {
                "skill": {
                    "name": skill.metadata.name,
                    "ref": skill.ref,
                    "source": skill.source.value,
                },
                "roles": role_payloads,
            }

        return await execute_tool_call(
            ctx,
            tool_name="list_skill_roles",
            args_summary={"skill_name": skill_name},
            action=_action,
            raw_args=locals(),
        )
