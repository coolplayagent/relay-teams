# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.agents.tasks.models import TaskEnvelope
from agent_teams.mcp.registry import McpRegistry
from agent_teams.roles.models import RoleDefinition
from agent_teams.roles.registry import RoleRegistry, is_coordinator_role_definition

ROLE_USAGE_PROMPT = (
    "## Role Usage\n"
    "Delegate only when another role is a better fit than answering directly. "
    "Choose the role whose description, tools, MCP tools, and skills best match the task, "
    "then give that role a concrete objective scoped to its responsibility. "
    "Assign work the way a manager briefs an employee: translate the user need into a clear task, "
    "expected outcome, and constraints for that role instead of merely repeating the user's request verbatim."
)
AVAILABLE_ROLES_HEADING = "## Available Roles"
AVAILABLE_ROLES_EMPTY_PROMPT = f"{AVAILABLE_ROLES_HEADING}\nnone"
ROLE_BLOCK_HEADING_PREFIX = "### "
ROLE_BLOCK_DESCRIPTION_PREFIX = "- Description: "
ROLE_BLOCK_TOOLS_PREFIX = "- Tools: "
ROLE_BLOCK_MCP_TOOLS_PREFIX = "- MCP Tools: "
ROLE_BLOCK_SKILLS_PREFIX = "- Skills: "
AVAILABLE_SKILLS_HEADING = "## Available Skills"
AVAILABLE_SKILL_ITEM_PREFIX = "- "
NONE_LABEL = "none"


class RuntimePromptBuildInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    role: RoleDefinition
    task: TaskEnvelope | None = None
    shared_state_snapshot: tuple[tuple[str, str], ...]


class PromptSkillInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class SystemPromptBuildInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    system_prompt: str = Field(min_length=1)
    allowed_tools: tuple[str, ...]
    skill_instructions: tuple[PromptSkillInstruction, ...] = ()


async def build_runtime_system_prompt(
    data: RuntimePromptBuildInput,
    *,
    role_registry: RoleRegistry | None = None,
    mcp_registry: McpRegistry | None = None,
) -> str:
    prompt = data.role.system_prompt
    if not is_coordinator_role_definition(data.role):
        return prompt
    if role_registry is None or mcp_registry is None:
        raise RuntimeError(
            "Coordinator runtime prompt generation requires role_registry and mcp_registry"
        )
    roles_prompt = await build_available_roles_prompt(
        role_registry=role_registry,
        mcp_registry=mcp_registry,
    )
    if not roles_prompt:
        return prompt
    return f"{prompt}\n\n{roles_prompt}"


async def build_available_roles_prompt(
    *,
    role_registry: RoleRegistry,
    mcp_registry: McpRegistry,
) -> str:
    roles = sorted(
        (
            role
            for role in role_registry.list_roles()
            if not is_coordinator_role_definition(role)
        ),
        key=lambda role: (role.name, role.role_id),
    )
    if not roles:
        return AVAILABLE_ROLES_EMPTY_PROMPT

    role_blocks = await asyncio.gather(
        *[
            _build_available_role_block(role=role, mcp_registry=mcp_registry)
            for role in roles
        ]
    )
    return (
        ROLE_USAGE_PROMPT
        + "\n\n"
        + AVAILABLE_ROLES_HEADING
        + "\n\n"
        + "\n\n".join(role_blocks)
    )


def build_skill_instructions_prompt(
    skill_instructions: tuple[PromptSkillInstruction, ...],
) -> str:
    if not skill_instructions:
        return ""
    skill_blocks = [
        AVAILABLE_SKILL_ITEM_PREFIX + entry.name + ": " + entry.description
        for entry in skill_instructions
    ]
    return AVAILABLE_SKILLS_HEADING + "\n" + "\n".join(skill_blocks)


def build_system_prompt(data: SystemPromptBuildInput) -> str:
    _ = data.allowed_tools
    sections: list[str] = [data.system_prompt]
    skill_prompt = build_skill_instructions_prompt(data.skill_instructions)
    if skill_prompt:
        sections.append(skill_prompt)
    return "\n\n".join(sections)


async def _build_available_role_block(
    *,
    role: RoleDefinition,
    mcp_registry: McpRegistry,
) -> str:
    mcp_tools = await _list_role_mcp_tools(role=role, mcp_registry=mcp_registry)
    return "\n".join(
        (
            ROLE_BLOCK_HEADING_PREFIX + role.name,
            ROLE_BLOCK_DESCRIPTION_PREFIX + role.description,
            ROLE_BLOCK_TOOLS_PREFIX + _format_names(role.tools),
            ROLE_BLOCK_MCP_TOOLS_PREFIX + _format_names(mcp_tools),
            ROLE_BLOCK_SKILLS_PREFIX + _format_names(role.skills),
        )
    )


async def _list_role_mcp_tools(
    *,
    role: RoleDefinition,
    mcp_registry: McpRegistry,
) -> tuple[str, ...]:
    if not role.mcp_servers:
        return ()
    summaries = await asyncio.gather(
        *[mcp_registry.list_tools(server_name) for server_name in role.mcp_servers]
    )
    return tuple(
        f"{server_name}/{tool.name}"
        for server_name, tools in zip(role.mcp_servers, summaries, strict=True)
        for tool in tools
    )


def _format_names(names: tuple[str, ...]) -> str:
    if not names:
        return NONE_LABEL
    return ", ".join(names)


PromptBuildInput = RuntimePromptBuildInput


class RuntimePromptBuilder(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    role_registry: RoleRegistry | None = None
    mcp_registry: McpRegistry | None = None

    async def build(self, data: PromptBuildInput) -> str:
        return await build_runtime_system_prompt(
            data,
            role_registry=self.role_registry,
            mcp_registry=self.mcp_registry,
        )
