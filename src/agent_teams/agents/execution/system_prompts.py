# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import platform
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.agents.execution.prompt_instructions import (
    LoadedPromptInstructions,
    PromptInstructionResolver,
)
from agent_teams.agents.tasks.models import TaskEnvelope
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import (
    RoleRegistry,
    is_coordinator_role_definition,
    is_main_agent_role_definition,
)
from agent_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from agent_teams.sessions.runs.run_models import (
    RuntimePromptConversationContext,
    RunTopologySnapshot,
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
    "- Inspect the current worker pool with `list_available_roles` when selecting or reusing a dispatch target.\n"
    "- If no existing role is a good fit, create a run-scoped role with `create_temporary_role` before dispatch.\n"
    "- Prefer `template_role_id` when creating a temporary role so it inherits the closest existing capabilities.\n"
    "- Reuse an existing temporary role when it already matches the delegated work.\n"
    "- Create tasks as durable contracts with concrete outcomes and constraints.\n"
    "- Choose the executing role in `dispatch_task`.\n"
    "- Use the dispatch prompt to pass stage-specific instructions and upstream context.\n"
    "- The roles listed below are dispatch targets, not your own capabilities."
)
SKILL_USAGE_PROMPT = (
    "## Skill Usage\n"
    "The list below is a catalog of available skills in the form `skill_name: description`. "
    "Use `load_skill` when a listed skill is relevant and you need its full instructions before acting. "
    "It returns the skill manifest, instructions, and selected absolute file paths for the skill directory. "
    "After loading a skill, use the normal `read` tool for skill files when you need more detail."
)
FEISHU_GROUP_CONTEXT_PROMPT = (
    "## Feishu Group Chat Rules\n"
    "当前对话来自飞书群聊；用户输入会包含发送者标识，你必须明确区分不同发送者，不要把群成员当作同一用户。\n"
    "你调用 im_send 的发送的消息和本轮对话的最终答案（Final Answer）均会被发送给用户，不要重复发送消息，不要回复“已通过飞书回复用户”之类的消息。\n"
    "如果最终答案（Final Answer）能够完成用户任务，就不要调用im_send，避免消息过多。"
)
AVAILABLE_ROLES_HEADING = "## Available Roles"
AVAILABLE_ROLES_EMPTY_PROMPT = f"{AVAILABLE_ROLES_HEADING}\nnone"
ROLE_BLOCK_HEADING_PREFIX = "### "
ROLE_BLOCK_SOURCE_PREFIX = "- Source: "
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
    worktree_root: Path | None = None
    conversation_context: RuntimePromptConversationContext | None = None


class PromptSkillInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class RuntimePromptSections(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    prompt: str = Field(min_length=1)
    base_instructions: str = Field(min_length=1)
    capability_summary: str = ""
    workspace_context: str = ""
    local_instruction_paths: tuple[Path, ...] = ()


class SystemPromptSectionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_instructions: str = Field(min_length=1)
    capability_summary: str = ""
    workspace_context: str = ""
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

    from agent_teams.tools.workspace_tools.shell_executor import resolve_bash_path

    bash_path = "Unknown"
    try:
        bash_path = resolve_bash_path()
    except Exception:
        pass

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
    github_line = _build_github_cli_environment_line()
    if github_line:
        lines.append(github_line)
    return "\n".join(lines)


def _build_github_cli_environment_line() -> str | None:
    token_configured, system_gh_path = _get_github_cli_environment_status()
    if not token_configured:
        return None
    if system_gh_path is not None:
        return f"- GitHub CLI: token configured; using system gh at {system_gh_path}"
    return "- GitHub CLI: token configured; gh will be resolved on demand"


def _get_github_cli_environment_status() -> tuple[bool, Path | None]:
    try:
        from agent_teams.env.github_config_service import GitHubConfigService
        from agent_teams.paths import get_app_config_dir
        from agent_teams.tools.workspace_tools.github_cli import resolve_system_gh_path
    except Exception:
        return False, None

    try:
        config = GitHubConfigService(
            config_dir=get_app_config_dir()
        ).get_github_config()
    except Exception:
        return False, None
    if config.token is None:
        return False, None
    try:
        system_gh_path = resolve_system_gh_path()
    except Exception:
        system_gh_path = None
    return True, system_gh_path


async def build_runtime_system_prompt(
    data: RuntimePromptBuildInput,
    *,
    role_registry: RoleRegistry | None = None,
    runtime_role_resolver: RuntimeRoleResolver | None = None,
    mcp_registry: McpRegistry | None = None,
    instruction_resolver: PromptInstructionResolver | None = None,
) -> str:
    sections = await build_runtime_system_prompt_result(
        data,
        role_registry=role_registry,
        runtime_role_resolver=runtime_role_resolver,
        mcp_registry=mcp_registry,
        instruction_resolver=instruction_resolver,
    )
    return sections.prompt


async def build_runtime_system_prompt_result(
    data: RuntimePromptBuildInput,
    *,
    role_registry: RoleRegistry | None = None,
    runtime_role_resolver: RuntimeRoleResolver | None = None,
    mcp_registry: McpRegistry | None = None,
    instruction_resolver: PromptInstructionResolver | None = None,
) -> RuntimePromptSections:
    base_instruction_sections: list[str] = [data.role.system_prompt, COMMON_MODE_PROMPT]
    capability_summary_sections: list[str] = []
    workspace_context_sections: list[str] = []
    topology = data.topology

    env_prompt = build_environment_info_prompt(working_directory=data.working_directory)
    workspace_context_sections.append(env_prompt)
    loaded_instructions = await _load_runtime_prompt_instructions(
        instruction_resolver=instruction_resolver,
        working_directory=data.working_directory,
        worktree_root=data.worktree_root,
    )
    workspace_context_sections.extend(loaded_instructions.sections)
    if _is_feishu_group_conversation(data.conversation_context):
        workspace_context_sections.append(FEISHU_GROUP_CONTEXT_PROMPT)

    if is_main_agent_role_definition(data.role):
        return _build_runtime_prompt_sections(
            role_instructions=data.role.system_prompt,
            base_instruction_sections=base_instruction_sections,
            capability_summary_sections=capability_summary_sections,
            workspace_context_sections=workspace_context_sections,
            local_instruction_paths=loaded_instructions.local_paths,
        )
    if not is_coordinator_role_definition(data.role):
        return _build_runtime_prompt_sections(
            role_instructions=data.role.system_prompt,
            base_instruction_sections=base_instruction_sections,
            capability_summary_sections=capability_summary_sections,
            workspace_context_sections=workspace_context_sections,
            local_instruction_paths=loaded_instructions.local_paths,
        )
    if role_registry is None or mcp_registry is None:
        raise RuntimeError(
            "Coordinator runtime prompt generation requires role_registry and mcp_registry"
        )
    roles_prompt = await build_available_roles_prompt(
        role_registry=role_registry,
        runtime_role_resolver=runtime_role_resolver,
        mcp_registry=mcp_registry,
        run_id=data.task.trace_id if data.task is not None else None,
        allowed_role_ids=topology.allowed_role_ids if topology is not None else (),
    )
    base_instruction_sections.append(ORCHESTRATION_USAGE_PROMPT)
    if topology is not None and topology.orchestration_prompt.strip():
        workspace_context_sections.append(
            "## Orchestration Prompt\n" + topology.orchestration_prompt.strip()
        )
    if roles_prompt:
        capability_summary_sections.append(roles_prompt)
    return _build_runtime_prompt_sections(
        role_instructions=data.role.system_prompt,
        base_instruction_sections=base_instruction_sections,
        capability_summary_sections=capability_summary_sections,
        workspace_context_sections=workspace_context_sections,
        local_instruction_paths=loaded_instructions.local_paths,
    )


async def build_available_roles_prompt(
    *,
    role_registry: RoleRegistry,
    runtime_role_resolver: RuntimeRoleResolver | None = None,
    mcp_registry: McpRegistry,
    run_id: str | None = None,
    allowed_role_ids: tuple[str, ...] = (),
) -> str:
    allowed_role_id_set = {role_id for role_id in allowed_role_ids if role_id}
    static_role_ids = {role.role_id for role in role_registry.list_roles()}
    source_roles = (
        runtime_role_resolver.list_effective_roles(run_id=run_id)
        if runtime_role_resolver is not None and run_id is not None
        else role_registry.list_roles()
    )
    roles = sorted(
        (
            role
            for role in source_roles
            if not is_coordinator_role_definition(role)
            and not is_main_agent_role_definition(role)
            and (
                role.role_id not in static_role_ids
                or not allowed_role_id_set
                or role.role_id in allowed_role_id_set
            )
        ),
        key=lambda role: (role.name, role.role_id),
    )
    if not roles:
        return AVAILABLE_ROLES_EMPTY_PROMPT

    role_blocks = await asyncio.gather(
        *[
            _build_available_role_block(
                role=role,
                role_source=(
                    "static" if role.role_id in static_role_ids else "temporary"
                ),
                mcp_registry=mcp_registry,
            )
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


def compose_system_prompt(data: SystemPromptSectionsInput) -> str:
    sections: list[str] = [data.base_instructions]
    if data.capability_summary:
        sections.append(data.capability_summary)
    if data.workspace_context:
        sections.append(data.workspace_context)
    return _join_prompt_sections(sections)


def compose_runtime_system_prompt(
    runtime_prompt_sections: RuntimePromptSections,
    *,
    skill_instructions: tuple[PromptSkillInstruction, ...] = (),
) -> str:
    return compose_system_prompt(
        SystemPromptSectionsInput(
            base_instructions=runtime_prompt_sections.base_instructions,
            capability_summary=runtime_prompt_sections.capability_summary,
            workspace_context=runtime_prompt_sections.workspace_context,
            skill_instructions=skill_instructions,
        )
    )


def compose_provider_system_prompt(
    runtime_prompt_sections: RuntimePromptSections,
    *,
    skill_instructions: tuple[PromptSkillInstruction, ...] = (),
) -> str:
    return compose_runtime_system_prompt(
        runtime_prompt_sections,
        skill_instructions=skill_instructions,
    )


async def _load_runtime_prompt_instructions(
    *,
    instruction_resolver: PromptInstructionResolver | None,
    working_directory: Path | None,
    worktree_root: Path | None,
) -> LoadedPromptInstructions:
    resolver = (
        instruction_resolver
        if instruction_resolver is not None
        else PromptInstructionResolver()
    )
    return await resolver.load_initial_instructions(
        working_directory=working_directory,
        worktree_root=worktree_root,
    )


async def _build_available_role_block(
    *,
    role: RoleDefinition,
    role_source: str,
    mcp_registry: McpRegistry,
) -> str:
    mcp_tools = await _list_role_mcp_tools(role=role, mcp_registry=mcp_registry)
    return "\n".join(
        (
            ROLE_BLOCK_HEADING_PREFIX + role.role_id,
            ROLE_BLOCK_SOURCE_PREFIX + role_source,
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
    resolved_server_names = mcp_registry.resolve_server_names(
        role.mcp_servers,
        strict=False,
        consumer=f"agents.execution.system_prompts.role:{role.role_id}",
    )
    if not resolved_server_names:
        return ()
    summaries = await asyncio.gather(
        *[mcp_registry.list_tools(server_name) for server_name in resolved_server_names]
    )
    return tuple(
        tool.name
        for _server_name, tools in zip(resolved_server_names, summaries, strict=True)
        for tool in tools
    )


def _format_names(names: tuple[str, ...]) -> str:
    if not names:
        return NONE_LABEL
    return ", ".join(names)


def _validate_base_instruction_prefix(
    base_instructions: str,
    role_instructions: str,
) -> None:
    if not base_instructions.startswith(role_instructions):
        raise ValueError(
            "base_instructions must start with role_instructions for layer stability"
        )


def _build_runtime_prompt_sections(
    *,
    role_instructions: str,
    base_instruction_sections: Sequence[str],
    capability_summary_sections: Sequence[str],
    workspace_context_sections: Sequence[str],
    local_instruction_paths: tuple[Path, ...],
) -> RuntimePromptSections:
    base_instructions = _join_prompt_sections(base_instruction_sections)
    _validate_base_instruction_prefix(base_instructions, role_instructions)
    capability_summary = _join_prompt_sections(capability_summary_sections)
    workspace_context = _join_prompt_sections(workspace_context_sections)
    return RuntimePromptSections(
        prompt=_join_prompt_sections(
            (base_instructions, capability_summary, workspace_context)
        ),
        base_instructions=base_instructions,
        capability_summary=capability_summary,
        workspace_context=workspace_context,
        local_instruction_paths=local_instruction_paths,
    )


def _join_prompt_sections(sections: Sequence[str]) -> str:
    return "\n\n".join(section for section in sections if section.strip())


def _is_feishu_group_conversation(
    context: RuntimePromptConversationContext | None,
) -> bool:
    if context is None:
        return False
    provider = str(context.source_provider or "").strip().lower()
    chat_type = str(context.feishu_chat_type or "").strip().lower()
    return provider == "feishu" and chat_type == "group"


PromptBuildInput = RuntimePromptBuildInput


class RuntimePromptBuilder(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    role_registry: RoleRegistry | None = None
    runtime_role_resolver: RuntimeRoleResolver | None = None
    mcp_registry: McpRegistry | None = None
    instruction_resolver: PromptInstructionResolver | None = None

    async def build(self, data: PromptBuildInput) -> str:
        sections = await self.build_sections(data)
        return sections.prompt

    async def build_sections(
        self,
        data: PromptBuildInput,
    ) -> RuntimePromptSections:
        return await build_runtime_system_prompt_result(
            data,
            role_registry=self.role_registry,
            runtime_role_resolver=self.runtime_role_resolver,
            mcp_registry=self.mcp_registry,
            instruction_resolver=self.instruction_resolver,
        )

    async def build_details(
        self,
        data: PromptBuildInput,
    ) -> RuntimePromptSections:
        return await self.build_sections(data)
