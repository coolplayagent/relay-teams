# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import platform
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.agents.tasks.models import TaskEnvelope
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.sessions.runs.run_models import RunTopologySnapshot
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import (
    RoleRegistry,
    is_coordinator_role_definition,
    is_main_agent_role_definition,
)

COMMON_MODE_PROMPT = (
    "## Runtime Rules\n"
    "- Understand the user's goal before you act.\n"
    "- Keep instructions concrete and restate the subject instead of relying on vague references.\n"
    "- Use the available tools deliberately and respect each tool's contract.\n"
    "- Finish with a concrete outcome instead of stopping at partial analysis."
)
ORCHESTRATION_USAGE_PROMPT = (
    "## Orchestration Rules\n"
    "- Orchestrate delegated work and avoid implementing the task directly.\n"
    "- Delegate only when another role is a better fit than continuing yourself.\n"
    "- Choose roles by their Description, Tools, MCP Tools, and Skills.\n"
    "- Create tasks as durable contracts with concrete outcomes and constraints.\n"
    "- Choose the executing role in `dispatch_task`.\n"
    "- Use the dispatch prompt to pass stage-specific instructions and upstream context.\n"
    "- The roles listed below are dispatch targets, not your own capabilities."
)
SKILL_USAGE_PROMPT = (
    "## Skill Usage\n"
    "The list below is a catalog of available skills in the form `skill_name: description`. "
    "Use `load_skill` when a listed skill is relevant and you need its full instructions before acting. "
    "Use `read_skill_resource` to open a file that belongs to a loaded skill when the skill instructions reference extra resources. "
    "Use `run_skill_script` only when the loaded skill explicitly points you to a script-based workflow."
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
    topology: RunTopologySnapshot | None = None
    shared_state_snapshot: tuple[tuple[str, str], ...]
    working_directory: Path | None = None


class PromptSkillInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class SystemPromptBuildInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    system_prompt: str = Field(min_length=1)
    allowed_tools: tuple[str, ...]
    skill_instructions: tuple[PromptSkillInstruction, ...] = ()


def build_environment_info_prompt(*, working_directory: Path | None = None) -> str:
    """Gather current runtime environment information for the system prompt.
    Linked with shell tool implementation to ensure consistency.
    """
    system = platform.system()
    release = platform.release()
    machine = platform.machine()
    cwd = (
        str(working_directory.resolve())
        if working_directory is not None
        else os.getcwd()
    )

    # Link with shell tool implementation (lazy import to avoid circular dependency)
    from agent_teams.tools.workspace_tools.shell_executor import resolve_bash_path

    bash_path = "Unknown"
    try:
        bash_path = resolve_bash_path()
    except Exception:
        pass

    # Detect Shell/Bash type accurately
    shell_info = "Unknown"
    if system == "Windows":
        if "MSYSTEM" in os.environ:
            shell_info = f"Git Bash ({os.environ['MSYSTEM']})"
        elif "bash.exe" in bash_path.lower() and "git" in bash_path.lower():
            shell_info = "Git Bash (Resolved)"
        elif "PSModulePath" in os.environ:
            shell_info = "PowerShell"
        else:
            shell_info = "Command Prompt (cmd.exe)"
    elif system == "Linux":
        if (
            "microsoft" in platform.release().lower()
            or "microsoft" in platform.version().lower()
        ):
            shell_info = "WSL (Linux Bash)"
        else:
            shell_info = "Native Linux Bash"
    elif system == "Darwin":
        shell_info = "macOS Terminal (zsh/bash)"

    lines = [
        "## Runtime Environment Information",
        f"- Operating System: {system} ({release}) {machine}",
        f"- Working Directory: {cwd}",
        f"- Shell Type: {shell_info} (Path: {bash_path})",
    ]
    return "\n".join(lines)


async def build_runtime_system_prompt(
    data: RuntimePromptBuildInput,
    *,
    role_registry: RoleRegistry | None = None,
    mcp_registry: McpRegistry | None = None,
) -> str:
    prompt_sections: list[str] = [data.role.system_prompt, COMMON_MODE_PROMPT]
    topology = data.topology

    # Include environment information for all roles
    env_prompt = build_environment_info_prompt(working_directory=data.working_directory)
    prompt_sections.append(env_prompt)

    if is_main_agent_role_definition(data.role) and topology is not None:
        prompt_sections.append("## Normal Mode\n" + topology.main_agent_prompt)
        return "\n\n".join(prompt_sections)
    if not is_coordinator_role_definition(data.role):
        return "\n\n".join(prompt_sections)
    if role_registry is None or mcp_registry is None:
        raise RuntimeError(
            "Coordinator runtime prompt generation requires role_registry and mcp_registry"
        )
    roles_prompt = await build_available_roles_prompt(
        role_registry=role_registry,
        mcp_registry=mcp_registry,
        allowed_role_ids=topology.allowed_role_ids if topology is not None else (),
    )
    prompt_sections.append(ORCHESTRATION_USAGE_PROMPT)
    if topology is not None and topology.orchestration_prompt.strip():
        prompt_sections.append(
            "## Orchestration Prompt\n" + topology.orchestration_prompt.strip()
        )
    if roles_prompt:
        prompt_sections.append(roles_prompt)
    return "\n\n".join(prompt_sections)


async def build_available_roles_prompt(
    *,
    role_registry: RoleRegistry,
    mcp_registry: McpRegistry,
    allowed_role_ids: tuple[str, ...] = (),
) -> str:
    allowed_role_id_set = {role_id for role_id in allowed_role_ids if role_id}
    roles = sorted(
        (
            role
            for role in role_registry.list_roles()
            if not is_coordinator_role_definition(role)
            and not is_main_agent_role_definition(role)
            and (not allowed_role_id_set or role.role_id in allowed_role_id_set)
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
    return AVAILABLE_ROLES_HEADING + "\n\n" + "\n\n".join(role_blocks)


def build_skill_instructions_prompt(
    skill_instructions: tuple[PromptSkillInstruction, ...],
) -> str:
    if not skill_instructions:
        return ""
    skill_blocks = [
        AVAILABLE_SKILL_ITEM_PREFIX + entry.name + ": " + entry.description
        for entry in skill_instructions
    ]
    return (
        SKILL_USAGE_PROMPT
        + "\n\n"
        + AVAILABLE_SKILLS_HEADING
        + "\n"
        + "\n".join(skill_blocks)
    )


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
            ROLE_BLOCK_HEADING_PREFIX + role.role_id,
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
