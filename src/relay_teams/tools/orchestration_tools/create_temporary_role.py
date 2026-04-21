# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai import Agent

from relay_teams.roles.temporary_role_models import (
    TemporaryRoleSource,
    TemporaryRoleSpec,
)
from relay_teams.roles.role_models import RoleMode
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime import ToolContext, ToolDeps, execute_tool

DESCRIPTION = load_tool_description(__file__)


class CreateTemporaryRoleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    tools: tuple[str, ...] | None = None
    mcp_servers: tuple[str, ...] | None = None
    skills: tuple[str, ...] | None = None
    model_profile: str | None = Field(default=None, min_length=1)
    template_role_id: str | None = None


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def orch_create_temporary_role(
        ctx: ToolContext,
        role_id: str,
        name: str,
        description: str,
        system_prompt: str,
        tools: list[str] | None = None,
        mcp_servers: list[str] | None = None,
        skills: list[str] | None = None,
        model_profile: str | None = None,
        template_role_id: str | None = None,
    ) -> dict[str, JsonValue]:
        """Create a run-scoped temporary role for orchestration dispatch."""

        runtime_role_resolver = ctx.deps.runtime_role_resolver
        if runtime_role_resolver is None:
            raise RuntimeError("Temporary role creation is unavailable")

        payload = CreateTemporaryRoleInput(
            role_id=role_id,
            name=name,
            description=description,
            system_prompt=system_prompt,
            tools=tuple(tools) if tools is not None else None,
            mcp_servers=tuple(mcp_servers) if mcp_servers is not None else None,
            skills=tuple(skills) if skills is not None else None,
            model_profile=model_profile,
            template_role_id=template_role_id,
        )

        def _action() -> dict[str, JsonValue]:
            role = runtime_role_resolver.create_temporary_role(
                run_id=ctx.deps.run_id,
                session_id=ctx.deps.session_id,
                source=TemporaryRoleSource.META_AGENT_GENERATED,
                role=TemporaryRoleSpec(
                    role_id=payload.role_id,
                    name=payload.name,
                    description=payload.description,
                    version="temporary",
                    tools=payload.tools or (),
                    mcp_servers=payload.mcp_servers or (),
                    skills=payload.skills or (),
                    model_profile=payload.model_profile or "default",
                    system_prompt=payload.system_prompt,
                    mode=RoleMode.SUBAGENT,
                    template_role_id=payload.template_role_id,
                ),
            )
            return {
                "role": {
                    "role_id": role.role_id,
                    "name": role.name,
                    "description": role.description,
                    "tools": list(role.tools),
                    "mcp_servers": list(role.mcp_servers),
                    "skills": list(role.skills),
                    "model_profile": role.model_profile,
                    "source": "temporary",
                    "run_id": ctx.deps.run_id,
                }
            }

        return await execute_tool(
            ctx,
            tool_name="orch_create_temporary_role",
            args_summary=payload.model_dump(mode="json"),
            action=_action,
        )
