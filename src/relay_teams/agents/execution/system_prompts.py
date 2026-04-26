# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from functools import lru_cache
from datetime import datetime, timedelta
import logging
import os
import platform
from collections.abc import Sequence
from pathlib import Path
import shutil

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.agents.instances.models import RuntimeToolsSnapshot
from relay_teams.agents.execution.prompt_instructions import (
    LoadedPromptInstructions,
    PromptInstructionResolver,
)
from relay_teams.agents.tasks.models import TaskEnvelope
from relay_teams.logger import get_logger, log_event
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import (
    RoleRegistry,
    is_coordinator_role_definition,
    is_main_agent_role_definition,
)
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.sessions.runs.run_models import (
    RuntimePromptConversationContext,
    RunTopologySnapshot,
)
from relay_teams.sessions.session_models import SessionMode
from relay_teams.workspace import (
    WorkspaceHandle,
    WorkspaceMountCapabilities,
    WorkspaceMountProvider,
    WorkspaceRemoteMountRoot,
    WorkspaceSshMountConfig,
)
from relay_teams.workspace.ssh_profile_service import SshProfileService

LOGGER = get_logger(__name__)

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
    "- Inspect the current worker pool with `orch_list_available_roles` when selecting or reusing a dispatch target.\n"
    "- If no existing role is a good fit, create a run-scoped role with `orch_create_temporary_role` before dispatch.\n"
    "- Prefer `template_role_id` when creating a temporary role so it inherits the closest existing capabilities.\n"
    "- Reuse an existing temporary role when it already matches the delegated work.\n"
    "- Create tasks as durable contracts with concrete outcomes and constraints.\n"
    "- Fill task specs with acceptance criteria, verification evidence, and "
    "lifecycle settings when risk or duration warrants them.\n"
    "- Choose the executing role in `orch_dispatch_task`.\n"
    "- Use the dispatch prompt to pass stage-specific instructions and upstream context.\n"
    "- The roles listed below are dispatch targets, not your own capabilities."
)
SUBAGENT_USAGE_PROMPT = (
    "## Subagent Rules\n"
    "- Use `spawn_subagent` when another role is a better fit for a bounded task.\n"
    "- Inspect the `spawn_subagent` tool description for the current list of subagent capabilities.\n"
    "- Pass the full task context in the spawn prompt because each subagent starts fresh.\n"
    "- `spawn_subagent` waits for the final result by default; pass `background=true` only for work that should continue independently.\n"
    "- Use `list_background_tasks`, `wait_background_task`, and `stop_background_task` only for background subagent runs.\n"
    "- Summarize completed subagent work yourself when it becomes relevant to the user."
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
AVAILABLE_SUBAGENTS_HEADING = "## Available Subagents"
AVAILABLE_SUBAGENTS_EMPTY_PROMPT = f"{AVAILABLE_SUBAGENTS_HEADING}\nnone"
ROLE_BLOCK_HEADING_PREFIX = "### "
ROLE_BLOCK_SOURCE_PREFIX = "- Source: "
ROLE_BLOCK_DESCRIPTION_PREFIX = "- Description: "
ROLE_BLOCK_TOOLS_PREFIX = "- Tools: "
ROLE_BLOCK_MCP_TOOLS_PREFIX = "- MCP Tools: "
ROLE_BLOCK_SKILLS_PREFIX = "- Skills: "
AVAILABLE_SKILLS_HEADING = "## Available Skills"
AVAILABLE_SKILL_ITEM_PREFIX = "- "
NONE_LABEL = "none"
AUTHORIZED_RUNTIME_TOOLS_HEADING = "## Authorized Runtime Tools"
WORKSPACE_ENVIRONMENTS_HEADING = "## Workspace Environments"
TIME_TRUST_RULE = (
    "- Time Trust Rule: Do not trust your internal knowledge for the current date or "
    "time. When the user asks about today, current, recent, yesterday, tomorrow, or "
    "this week, use the runtime date in this section as the default source of truth "
    "and verify with tools when higher precision is needed."
)


class WorkspaceSshProfilePromptMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ssh_profile_id: str = Field(min_length=1)
    host: str = Field(min_length=1)
    username: str = Field(min_length=1)
    port: int | None = Field(default=None, ge=1, le=65535)
    remote_shell: str | None = None


class RuntimePromptBuildInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    role: RoleDefinition
    task: TaskEnvelope | None = None
    topology: RunTopologySnapshot | None = None
    shared_state_snapshot: tuple[tuple[str, str], ...]
    working_directory: Path | None = None
    worktree_root: Path | None = None
    workspace: WorkspaceHandle | None = None
    ssh_profile_metadata: tuple[WorkspaceSshProfilePromptMetadata, ...] = ()
    conversation_context: RuntimePromptConversationContext | None = None
    runtime_tools: RuntimeToolsSnapshot | None = None


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
    Linked with the workspace command runtime to ensure consistency.
    """
    system = platform.system()
    release = platform.release()
    machine = platform.machine()
    cwd = (
        str(working_directory.resolve())
        if working_directory is not None
        else os.getcwd()
    )

    from relay_teams.tools.workspace_tools.shell_executor import describe_runtime_shell

    runtime_shell = describe_runtime_shell()
    shell_info = runtime_shell.shell_info
    shell_path = runtime_shell.shell_path
    current_date, runtime_timezone = _get_runtime_date_context()

    lines = [
        "## Runtime Environment Information",
        f"- Operating System: {system} ({release}) {machine}",
        f"- Working Directory: {cwd}",
        f"- Shell Type: {shell_info} (Path: {shell_path})",
        f"- Current Date: {current_date}",
        f"- Runtime Timezone: {runtime_timezone}",
        TIME_TRUST_RULE,
    ]
    github_line = _build_github_cli_environment_line()
    if github_line:
        lines.append(github_line)
    clawhub_line = _build_clawhub_environment_line()
    if clawhub_line:
        lines.append(clawhub_line)
    lines.extend(_build_python_package_environment_lines())
    return "\n".join(lines)


def build_workspace_environments_prompt(
    *,
    workspace: WorkspaceHandle | None,
    ssh_profile_metadata: tuple[WorkspaceSshProfilePromptMetadata, ...] = (),
) -> str:
    if workspace is None:
        return ""

    profile_by_id = {
        profile.ssh_profile_id: profile for profile in ssh_profile_metadata
    }
    lines = [
        WORKSPACE_ENVIRONMENTS_HEADING,
        f"- Workspace ID: {workspace.ref.workspace_id}",
        f"- Default Mount: {workspace.default_mount_name}",
        f"- Active Execution Mount: {workspace.locations.mount_name}",
        f"- Workspace Temp Root: {workspace.tmp_root.resolve()}",
        (
            "- Path Syntax: unprefixed paths use the default mount; use "
            "`<mount_name>:/path` for non-default mounts; use `tmp/...` for "
            "workspace temp files."
        ),
        "- Authentication: handled by backend workspace tools.",
    ]
    for mount in workspace.mounts:
        lines.append("")
        lines.extend(
            _build_workspace_mount_environment_lines(
                workspace=workspace,
                profile_by_id=profile_by_id,
                mount_name=mount.mount_name,
            )
        )
    return "\n".join(lines)


def build_workspace_ssh_profile_prompt_metadata(
    *,
    workspace: WorkspaceHandle,
    ssh_profile_service: SshProfileService | None,
    consumer: str,
) -> tuple[WorkspaceSshProfilePromptMetadata, ...]:
    if ssh_profile_service is None:
        return ()
    profile_ids = _workspace_ssh_profile_ids(workspace)
    metadata: list[WorkspaceSshProfilePromptMetadata] = []
    for ssh_profile_id in profile_ids:
        try:
            record = ssh_profile_service.get_profile(ssh_profile_id)
        except KeyError:
            log_event(
                LOGGER,
                logging.WARNING,
                event="workspace.ssh_profile_prompt_metadata_missing",
                message="Workspace SSH profile metadata was unavailable for prompt context",
                payload={
                    "consumer": consumer,
                    "workspace_id": workspace.ref.workspace_id,
                    "ssh_profile_id": ssh_profile_id,
                },
            )
            continue
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="workspace.ssh_profile_prompt_metadata_failed",
                message="Workspace SSH profile metadata lookup failed for prompt context",
                payload={
                    "consumer": consumer,
                    "workspace_id": workspace.ref.workspace_id,
                    "ssh_profile_id": ssh_profile_id,
                    "error_type": type(exc).__name__,
                },
            )
            continue
        username = str(record.username or "").strip()
        if not username:
            log_event(
                LOGGER,
                logging.WARNING,
                event="workspace.ssh_profile_prompt_metadata_missing_username",
                message="Workspace SSH profile metadata was missing a login username",
                payload={
                    "consumer": consumer,
                    "workspace_id": workspace.ref.workspace_id,
                    "ssh_profile_id": ssh_profile_id,
                },
            )
            continue
        metadata.append(
            WorkspaceSshProfilePromptMetadata(
                ssh_profile_id=record.ssh_profile_id,
                host=record.host,
                username=username,
                port=record.port,
                remote_shell=record.remote_shell,
            )
        )
    return tuple(metadata)


def _workspace_ssh_profile_ids(workspace: WorkspaceHandle) -> tuple[str, ...]:
    profile_ids: list[str] = []
    seen: set[str] = set()
    for mount in workspace.mounts:
        if mount.provider != WorkspaceMountProvider.SSH:
            continue
        provider_config = mount.provider_config
        if not isinstance(provider_config, WorkspaceSshMountConfig):
            continue
        ssh_profile_id = provider_config.ssh_profile_id
        if ssh_profile_id in seen:
            continue
        profile_ids.append(ssh_profile_id)
        seen.add(ssh_profile_id)
    return tuple(profile_ids)


def _build_workspace_mount_environment_lines(
    *,
    workspace: WorkspaceHandle,
    profile_by_id: dict[str, WorkspaceSshProfilePromptMetadata],
    mount_name: str,
) -> list[str]:
    mount = workspace.mount_by_name(mount_name)
    default_suffix = (
        " (default)" if mount.mount_name == workspace.default_mount_name else ""
    )
    lines = [
        f"### Mount: {mount.mount_name}{default_suffix}",
        f"- Provider: {mount.provider.value}",
        f"- Root: {mount.root_reference}",
        f"- Working Directory: {mount.working_directory}",
        "- Readable Paths: " + _format_scope_paths(mount.readable_paths),
        "- Writable Paths: " + _format_scope_paths(mount.writable_paths),
        "- Capabilities: " + _format_mount_capabilities(mount.capabilities),
    ]
    if mount.provider == WorkspaceMountProvider.SSH:
        lines.extend(
            _build_ssh_mount_environment_lines(
                workspace=workspace,
                profile_by_id=profile_by_id,
                mount_name=mount.mount_name,
            )
        )
    return lines


def _build_ssh_mount_environment_lines(
    *,
    workspace: WorkspaceHandle,
    profile_by_id: dict[str, WorkspaceSshProfilePromptMetadata],
    mount_name: str,
) -> list[str]:
    mount = workspace.mount_by_name(mount_name)
    provider_config = mount.provider_config
    if not isinstance(provider_config, WorkspaceSshMountConfig):
        return ["- SSH Metadata: unavailable"]
    metadata = profile_by_id.get(provider_config.ssh_profile_id)
    lines = [
        f"- SSH Profile ID: {provider_config.ssh_profile_id}",
        f"- Remote Root: {provider_config.remote_root}",
    ]
    if metadata is None:
        lines.append("- SSH Metadata: unavailable")
    else:
        lines.extend(
            [
                f"- SSH Host: {metadata.host}",
                f"- SSH Username: {metadata.username}",
                "- SSH Port: " + _format_optional_number(metadata.port),
                "- SSH Remote Shell: " + _format_optional_text(metadata.remote_shell),
            ]
        )
    lines.extend(
        [
            (
                "- SSH Login User Rule: use the SSH Username shown above as the "
                "remote login user. Do not substitute the local operating-system "
                "user or write explicit ssh login scripts."
            ),
            (
                "- SSH Path Rule: the materialized local root below is a local "
                "backend mount path, not the remote user's home directory."
            ),
        ]
    )
    remote_mount_root = _remote_mount_root_for(
        workspace=workspace,
        mount_name=mount_name,
    )
    if remote_mount_root is None:
        lines.append("- Materialized Local Root: not materialized in this run")
    else:
        lines.append(
            f"- Materialized Local Root: {remote_mount_root.local_root.resolve()}"
        )
    return lines


def _remote_mount_root_for(
    *,
    workspace: WorkspaceHandle,
    mount_name: str,
) -> WorkspaceRemoteMountRoot | None:
    for remote_mount_root in workspace.locations.remote_mount_roots:
        if remote_mount_root.mount_name == mount_name:
            return remote_mount_root
    return None


def _format_scope_paths(paths: tuple[str, ...]) -> str:
    if not paths:
        return NONE_LABEL
    return ", ".join(paths)


def _format_mount_capabilities(capabilities: WorkspaceMountCapabilities | None) -> str:
    if capabilities is None:
        return "not declared"
    fields = (
        ("read", capabilities.can_read),
        ("write", capabilities.can_write),
        ("search", capabilities.can_search),
        ("shell", capabilities.can_shell),
        ("diff", capabilities.can_diff),
        ("preview", capabilities.can_preview),
    )
    return ", ".join(f"{name}={'yes' if enabled else 'no'}" for name, enabled in fields)


def _format_optional_text(value: str | None) -> str:
    if value is None:
        return NONE_LABEL
    normalized = value.strip()
    return normalized or NONE_LABEL


def _format_optional_number(value: int | None) -> str:
    if value is None:
        return NONE_LABEL
    return str(value)


def _get_runtime_date_context() -> tuple[str, str]:
    now = datetime.now().astimezone()
    current_date = now.date().isoformat()
    timezone_name = now.tzname() or "local"
    utc_offset = now.utcoffset()
    if utc_offset is None:
        return current_date, timezone_name
    offset_text = _format_utc_offset(utc_offset)
    if timezone_name == offset_text:
        return current_date, f"UTC{offset_text}"
    return current_date, f"{timezone_name} (UTC{offset_text})"


def _format_utc_offset(offset: timedelta) -> str:
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def _build_github_cli_environment_line() -> str | None:
    token_configured, system_gh_path = _get_github_cli_environment_status()
    if not token_configured:
        return None
    if system_gh_path is not None:
        return f"- GitHub CLI: token configured; using system gh at {system_gh_path}"
    return "- GitHub CLI: token configured; gh will be resolved on demand"


def _build_clawhub_environment_line() -> str | None:
    token_configured, clawhub_path = _get_clawhub_environment_status()
    if not token_configured:
        return None
    if clawhub_path is not None:
        return (
            f"- ClawHub CLI: token configured; using system clawhub at {clawhub_path}"
        )
    return "- ClawHub CLI: token configured"


def _build_python_package_environment_lines() -> list[str]:
    pip_path = _resolve_package_tool_path(("pip", "pip3"))
    uv_path = _resolve_package_tool_path(("uv",))
    lines = [
        "- Python Package Tool (pip): " + _format_package_tool_status(pip_path),
        "- Python Package Tool (uv): " + _format_package_tool_status(uv_path),
    ]
    if uv_path is not None:
        lines.append(
            "- Python Package Install Hint: If pip install fails with "
            "externally-managed-environment (PEP 668), try uv pip install "
            "<packages>."
        )
    return lines


@lru_cache(maxsize=None)
def _resolve_package_tool_path(command_names: tuple[str, ...]) -> Path | None:
    for command_name in command_names:
        resolved = shutil.which(command_name)
        if not resolved:
            continue
        resolved_path = Path(resolved)
        if resolved_path.is_file():
            return resolved_path
    return None


def _format_package_tool_status(tool_path: Path | None) -> str:
    if tool_path is None:
        return "not found on PATH"
    return str(tool_path)


def _get_github_cli_environment_status() -> tuple[bool, Path | None]:
    try:
        from relay_teams.env.github_config_service import GitHubConfigService
        from relay_teams.paths import get_app_config_dir
        from relay_teams.net.github_cli import resolve_system_gh_path
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


def _get_clawhub_environment_status() -> tuple[bool, Path | None]:
    try:
        from relay_teams.env.clawhub_config_service import ClawHubConfigService
        from relay_teams.env.clawhub_cli import resolve_existing_clawhub_path
        from relay_teams.paths import get_app_config_dir
    except Exception:
        return False, None

    try:
        config = ClawHubConfigService(
            config_dir=get_app_config_dir()
        ).get_clawhub_config()
    except Exception:
        return False, None
    if config.token is None:
        return False, None
    return True, resolve_existing_clawhub_path()


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
    workspace_environments_prompt = build_workspace_environments_prompt(
        workspace=data.workspace,
        ssh_profile_metadata=data.ssh_profile_metadata,
    )
    if workspace_environments_prompt:
        workspace_context_sections.append(workspace_environments_prompt)
    workspace_context_sections.append(
        "## Execution Surface\n"
        f"- Declared surface: {data.role.execution_surface.value}\n"
        "- Use desktop or browser computer-use tools only when the role and runtime actually expose them."
    )
    loaded_instructions = await _load_runtime_prompt_instructions(
        instruction_resolver=instruction_resolver,
        working_directory=data.working_directory,
        worktree_root=data.worktree_root,
    )
    workspace_context_sections.extend(loaded_instructions.sections)
    if _is_feishu_group_conversation(data.conversation_context):
        workspace_context_sections.append(FEISHU_GROUP_CONTEXT_PROMPT)
    runtime_tools_prompt = build_runtime_tools_prompt(data.runtime_tools)
    if runtime_tools_prompt:
        workspace_context_sections.append(runtime_tools_prompt)

    if is_main_agent_role_definition(data.role):
        _append_available_subagents_prompt(
            data=data,
            role_registry=role_registry,
            base_instruction_sections=base_instruction_sections,
            capability_summary_sections=capability_summary_sections,
        )
        return _build_runtime_prompt_sections(
            role_instructions=data.role.system_prompt,
            base_instruction_sections=base_instruction_sections,
            capability_summary_sections=capability_summary_sections,
            workspace_context_sections=workspace_context_sections,
            local_instruction_paths=loaded_instructions.local_paths,
        )
    if not is_coordinator_role_definition(data.role):
        _append_available_subagents_prompt(
            data=data,
            role_registry=role_registry,
            base_instruction_sections=base_instruction_sections,
            capability_summary_sections=capability_summary_sections,
        )
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


def build_available_subagents_prompt(*, role_registry: RoleRegistry) -> str:
    roles = role_registry.list_subagent_roles()
    if not roles:
        return AVAILABLE_SUBAGENTS_EMPTY_PROMPT
    lines = [AVAILABLE_SUBAGENTS_HEADING]
    for role in roles:
        lines.append(f"- {role.role_id}: {role.name} - {role.description}")
    return "\n".join(lines)


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


def build_runtime_tools_prompt(runtime_tools: RuntimeToolsSnapshot | None) -> str:
    if runtime_tools is None:
        return ""
    lines = [AUTHORIZED_RUNTIME_TOOLS_HEADING]
    lines.append("- Only call tools that appear in this runtime-authorized list.")
    lines.append(
        "- Local Tools: " + _format_names(_tool_names(runtime_tools.local_tools))
    )
    lines.append(
        "- Skill Tools: " + _format_names(_tool_names(runtime_tools.skill_tools))
    )
    lines.extend(_build_todo_guidance(runtime_tools))
    lines.extend(_format_mcp_tool_lines(runtime_tools))
    return "\n".join(lines)


def _build_todo_guidance(runtime_tools: RuntimeToolsSnapshot) -> list[str]:
    local_tool_names = set(_tool_names(runtime_tools.local_tools))
    if "todo_write" not in local_tool_names:
        return []
    lines = [
        "- Use `todo_write` to maintain a concise run-scoped plan when the task has multiple concrete steps.",
        "- `todo_write` always replaces the full todo table. Keep at most one item `in_progress`.",
    ]
    if "todo_read" in local_tool_names:
        lines.append(
            "- Use `todo_read` before updating if you need to inspect the latest persisted todo snapshot."
        )
    return lines


def compose_system_prompt(data: SystemPromptSectionsInput) -> str:
    sections: list[str] = [data.base_instructions]
    skill_prompt = build_skill_instructions_prompt(data.skill_instructions)
    if skill_prompt:
        sections.append(skill_prompt)
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


def _tool_names(entries: Sequence[object]) -> tuple[str, ...]:
    names: list[str] = []
    for entry in entries:
        name = getattr(entry, "name", None)
        if isinstance(name, str) and name.strip():
            names.append(name)
    return tuple(names)


def _append_available_subagents_prompt(
    *,
    data: RuntimePromptBuildInput,
    role_registry: RoleRegistry | None,
    base_instruction_sections: list[str],
    capability_summary_sections: list[str],
) -> None:
    if role_registry is None:
        return
    if data.topology is not None and data.topology.session_mode != SessionMode.NORMAL:
        return
    if "spawn_subagent" not in data.role.tools:
        return
    base_instruction_sections.append(SUBAGENT_USAGE_PROMPT)


def _format_mcp_tool_lines(runtime_tools: RuntimeToolsSnapshot) -> list[str]:
    if not runtime_tools.mcp_tools:
        return ["- MCP Tools: none"]
    grouped: dict[str, list[str]] = {}
    for entry in runtime_tools.mcp_tools:
        server_name = entry.server_name or "unknown"
        grouped.setdefault(server_name, []).append(entry.name)
    lines: list[str] = []
    for index, server_name in enumerate(sorted(grouped.keys())):
        prefix = "- MCP Tools: " if index == 0 else "- MCP Tools "
        lines.append(
            f"{prefix}{server_name}: {', '.join(sorted(grouped[server_name]))}"
        )
    return lines


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
