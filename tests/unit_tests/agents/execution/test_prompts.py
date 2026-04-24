# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from relay_teams.agents.execution.prompt_instructions import PromptInstructionResolver
import relay_teams.agents.execution.system_prompts as system_prompts
from relay_teams.agents.execution.system_prompts import (
    PromptSkillInstruction,
    RuntimePromptBuildInput,
    SystemPromptSectionsInput,
    WorkspaceSshProfilePromptMetadata,
    build_workspace_ssh_profile_prompt_metadata,
    build_runtime_system_prompt,
    build_runtime_system_prompt_result,
    compose_system_prompt,
)
from relay_teams.agents.execution.user_prompts import (
    UserPromptBuildInput,
    UserPromptSkillCandidate,
    build_user_prompt,
)
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from relay_teams.mcp.mcp_models import McpConfigScope, McpServerSpec, McpToolInfo
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.secrets import AppSecretStore
from relay_teams.roles.role_models import RoleDefinition, RoleMode
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.roles.temporary_role_models import TemporaryRoleSpec
from relay_teams.roles.temporary_role_repository import TemporaryRoleRepository
from relay_teams.sessions.runs.run_models import RunTopologySnapshot
from relay_teams.sessions.session_models import SessionMode
from relay_teams.workspace import (
    SshProfileConfig,
    SshProfileRepository,
    SshProfileSecretStore,
    SshProfileService,
    SshProfileStoredConfig,
    WorkspaceHandle,
    WorkspaceLocations,
    WorkspaceMountProvider,
    WorkspaceMountRecord,
    WorkspaceRef,
    WorkspaceRemoteMountRoot,
    WorkspaceSshMountConfig,
    build_local_workspace_mount,
)


@pytest.fixture(autouse=True)
def _suppress_host_github_prompt_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        system_prompts,
        "_get_github_cli_environment_status",
        lambda: (False, None),
    )


@pytest.fixture(autouse=True)
def _suppress_host_clawhub_prompt_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        system_prompts,
        "_get_clawhub_environment_status",
        lambda: (False, None),
    )


@pytest.fixture(autouse=True)
def _freeze_runtime_date_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        system_prompts,
        "_get_runtime_date_context",
        lambda: ("2026-04-02", "HKT (UTC+08:00)"),
    )


@pytest.fixture(autouse=True)
def _suppress_host_package_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        system_prompts,
        "_resolve_package_tool_path",
        lambda command_names: None,
    )


def _role(role_id: str) -> RoleDefinition:
    tools = ()
    if role_id.casefold() == "coordinator":
        tools = (
            "orch_create_tasks",
            "orch_create_temporary_role",
            "orch_update_task",
            "orch_list_available_roles",
            "orch_list_delegated_tasks",
            "orch_dispatch_task",
        )
    return RoleDefinition(
        role_id=role_id,
        name="role",
        description="Role description.",
        version="1",
        tools=tools,
        mcp_servers=(),
        skills=(),
        model_profile="default",
        system_prompt="You are a focused agent.",
    )


def _task() -> TaskEnvelope:
    return TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="trace-1",
        objective="Deliver weekly summary",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )


class _FileOnlySecretStore(AppSecretStore):
    def has_usable_keyring_backend(self) -> bool:
        return False


def _mixed_workspace_handle(tmp_path: Path) -> WorkspaceHandle:
    workspace_dir = tmp_path / ".relay-teams" / "workspaces" / "mixed"
    local_root = tmp_path / "project"
    ssh_local_root = workspace_dir / "ssh_mounts" / "prod"
    tmp_root = workspace_dir / "tmp"
    (local_root / "src").mkdir(parents=True)
    ssh_local_root.mkdir(parents=True)
    tmp_root.mkdir(parents=True)
    return WorkspaceHandle(
        ref=WorkspaceRef(
            workspace_id="mixed",
            session_id="session-1",
            role_id="writer_agent",
            conversation_id="conversation-1",
            default_mount_name="app",
            mount_names=("app", "prod"),
        ),
        mounts=(
            build_local_workspace_mount(
                mount_name="app",
                root_path=local_root,
                working_directory="src",
                readable_paths=(".", "shared"),
                writable_paths=("src",),
            ),
            WorkspaceMountRecord(
                mount_name="prod",
                provider=WorkspaceMountProvider.SSH,
                provider_config=WorkspaceSshMountConfig(
                    ssh_profile_id="prod-profile",
                    remote_root="/srv/app",
                ),
                working_directory=".",
                readable_paths=(".",),
                writable_paths=("config",),
            ),
        ),
        locations=WorkspaceLocations(
            workspace_dir=workspace_dir,
            mount_name="app",
            provider=WorkspaceMountProvider.LOCAL,
            scope_root=local_root,
            execution_root=local_root / "src",
            tmp_root=tmp_root,
            readable_roots=(local_root, tmp_root),
            writable_roots=(local_root / "src", tmp_root),
            remote_mount_roots=(
                WorkspaceRemoteMountRoot(
                    mount_name="prod",
                    local_root=ssh_local_root,
                    remote_root="/srv/app",
                ),
            ),
        ),
    )


class _FakeMcpRegistry(McpRegistry):
    def __init__(self) -> None:
        super().__init__(
            (
                McpServerSpec(
                    name="docs",
                    config={"mcpServers": {"docs": {"command": "npx"}}},
                    server_config={"command": "npx"},
                    source=McpConfigScope.APP,
                ),
            )
        )

    async def list_tools(self, name: str) -> tuple[McpToolInfo, ...]:
        assert name == "docs"
        return (McpToolInfo(name="docs_search", description="Search docs"),)


def _coordinator_registry() -> RoleRegistry:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates delegated work.",
            version="1",
            tools=(
                "orch_create_tasks",
                "orch_create_temporary_role",
                "orch_update_task",
                "orch_list_available_roles",
                "orch_list_delegated_tasks",
                "orch_dispatch_task",
            ),
            mcp_servers=(),
            skills=(),
            model_profile="default",
            system_prompt="You are a focused agent.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="writer_agent",
            name="Writer",
            description="Drafts release notes.",
            version="1",
            tools=("read", "write"),
            mcp_servers=("docs",),
            skills=("time",),
            model_profile="default",
            system_prompt="You are a writer.",
        )
    )
    return registry


def test_runtime_system_prompt_for_coordinator_has_contract_and_context() -> None:
    prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("Coordinator"),
                task=_task(),
                topology=RunTopologySnapshot(
                    session_mode=SessionMode.ORCHESTRATION,
                    main_agent_role_id="MainAgent",
                    normal_root_role_id="MainAgent",
                    coordinator_role_id="Coordinator",
                    orchestration_preset_id="default",
                    orchestration_prompt="Delegate by capability and finalize yourself.",
                    allowed_role_ids=("writer_agent",),
                ),
                shared_state_snapshot=(("status", "ready"),),
            ),
            role_registry=_coordinator_registry(),
            mcp_registry=_FakeMcpRegistry(),
        )
    )

    assert prompt.startswith("You are a focused agent.")
    assert "## Runtime Rules" in prompt
    assert "## Orchestration Rules" in prompt
    assert "## Orchestration Prompt" in prompt
    assert "## Available Roles" in prompt
    assert "### writer_agent" in prompt
    assert (
        "Delegate only when another role is a better fit than continuing yourself."
        in prompt
    )
    assert (
        "Create tasks as durable contracts with concrete outcomes and constraints."
        in prompt
    )
    assert (
        "Inspect the current worker pool with `orch_list_available_roles` when selecting or reusing a dispatch target."
        in prompt
    )
    assert (
        "If no existing role is a good fit, create a run-scoped role with `orch_create_temporary_role` before dispatch."
        in prompt
    )
    assert (
        "Prefer `template_role_id` when creating a temporary role so it inherits the closest existing capabilities."
        in prompt
    )
    assert "Choose the executing role in `orch_dispatch_task`." in prompt
    assert (
        "Use the dispatch prompt to pass stage-specific instructions and upstream context."
        in prompt
    )
    assert "dispatch targets, not your own capabilities." in prompt
    assert "- Source: static" in prompt
    assert "- Description: Drafts release notes." in prompt
    assert "- Tools: read, write" in prompt
    assert "- MCP Tools: docs_search" in prompt
    assert "- Skills: time" in prompt
    assert "Deliver weekly summary" not in prompt


def test_runtime_system_prompt_ignores_unknown_mcp_servers_in_available_roles() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates delegated work.",
            version="1",
            tools=("orch_create_tasks", "orch_update_task", "orch_dispatch_task"),
            mcp_servers=(),
            skills=(),
            model_profile="default",
            system_prompt="You are a focused agent.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="writer_agent",
            name="Writer",
            description="Drafts release notes.",
            version="1",
            tools=("read", "write"),
            mcp_servers=("docs", "missing_server"),
            skills=("time",),
            model_profile="default",
            system_prompt="You are a writer.",
        )
    )

    prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("Coordinator"),
                task=_task(),
                topology=RunTopologySnapshot(
                    session_mode=SessionMode.ORCHESTRATION,
                    main_agent_role_id="MainAgent",
                    normal_root_role_id="MainAgent",
                    coordinator_role_id="Coordinator",
                    orchestration_preset_id="default",
                    orchestration_prompt="Delegate by capability and finalize yourself.",
                    allowed_role_ids=("writer_agent",),
                ),
                shared_state_snapshot=(),
            ),
            role_registry=registry,
            mcp_registry=_FakeMcpRegistry(),
        )
    )

    assert "- MCP Tools: docs_search" in prompt


def test_runtime_system_prompt_includes_run_temporary_roles_in_available_roles(
    tmp_path: Path,
) -> None:
    registry = _coordinator_registry()
    resolver = RuntimeRoleResolver(
        role_registry=registry,
        temporary_role_repository=TemporaryRoleRepository(tmp_path / "roles.db"),
    )
    resolver.create_temporary_role(
        run_id="trace-1",
        session_id="session-1",
        role=TemporaryRoleSpec(
            role_id="tmp_writer",
            name="Tmp Writer",
            description="Handles a run-specific writing format.",
            system_prompt="You are a temporary writer.",
            tools=("read", "write"),
        ),
    )

    prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("Coordinator"),
                task=_task(),
                topology=RunTopologySnapshot(
                    session_mode=SessionMode.ORCHESTRATION,
                    main_agent_role_id="MainAgent",
                    normal_root_role_id="MainAgent",
                    coordinator_role_id="Coordinator",
                    orchestration_preset_id="default",
                    orchestration_prompt="Delegate by capability and finalize yourself.",
                    allowed_role_ids=("writer_agent",),
                ),
                shared_state_snapshot=(),
            ),
            role_registry=registry,
            runtime_role_resolver=resolver,
            mcp_registry=_FakeMcpRegistry(),
        )
    )

    assert "### writer_agent" in prompt
    assert "### tmp_writer" in prompt
    assert "- Source: temporary" in prompt
    assert "- Description: Handles a run-specific writing format." in prompt
    assert "- Tools: read, write" in prompt


def test_runtime_system_prompt_for_worker_skips_runtime_contract() -> None:
    working_directory = Path("/tmp/workspace-root")
    prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("writer_agent"),
                task=_task(),
                shared_state_snapshot=(),
                working_directory=working_directory,
            )
        )
    )

    assert prompt.startswith("You are a focused agent.")
    assert "## Runtime Environment Information" in prompt
    assert "- Operating System:" in prompt
    assert f"- Working Directory: {working_directory.resolve()}" in prompt
    assert "- Current Date: 2026-04-02" in prompt
    assert "- Runtime Timezone: HKT (UTC+08:00)" in prompt
    assert "- Python Package Tool (pip): not found on PATH" in prompt
    assert "- Python Package Tool (uv): not found on PATH" in prompt
    assert (
        "Do not trust your internal knowledge for the current date or time." in prompt
    )


def test_runtime_system_prompt_includes_workspace_environments(
    tmp_path: Path,
) -> None:
    workspace = _mixed_workspace_handle(tmp_path)

    result = asyncio.run(
        build_runtime_system_prompt_result(
            RuntimePromptBuildInput(
                role=_role("writer_agent"),
                task=_task(),
                shared_state_snapshot=(),
                working_directory=workspace.resolve_workdir(),
                worktree_root=workspace.scope_root,
                workspace=workspace,
                ssh_profile_metadata=(
                    WorkspaceSshProfilePromptMetadata(
                        ssh_profile_id="prod-profile",
                        host="prod.example.com",
                        username="deploy",
                        port=2222,
                        remote_shell="zsh",
                    ),
                ),
            )
        )
    )

    assert result.workspace_context.index(
        "## Runtime Environment Information"
    ) < result.workspace_context.index("## Workspace Environments")
    assert result.workspace_context.index("## Workspace Environments") < (
        result.workspace_context.index("## Execution Surface")
    )
    assert "- Workspace ID: mixed" in result.workspace_context
    assert "- Default Mount: app" in result.workspace_context
    assert "- Active Execution Mount: app" in result.workspace_context
    assert "use `<mount_name>:/path` for non-default mounts" in result.workspace_context
    assert "### Mount: app (default)" in result.workspace_context
    assert "- Provider: local" in result.workspace_context
    assert "- Working Directory: src" in result.workspace_context
    assert "- Readable Paths: ., shared" in result.workspace_context
    assert "- Writable Paths: src" in result.workspace_context
    assert "### Mount: prod" in result.workspace_context
    assert "- Provider: ssh" in result.workspace_context
    assert "- SSH Profile ID: prod-profile" in result.workspace_context
    assert "- SSH Host: prod.example.com" in result.workspace_context
    assert "- SSH Username: deploy" in result.workspace_context
    assert "- SSH Port: 2222" in result.workspace_context
    assert "- SSH Remote Shell: zsh" in result.workspace_context
    assert "use the SSH Username shown above" in result.workspace_context
    assert (
        "Do not substitute the local operating-system user" in result.workspace_context
    )
    assert "not the remote user's home directory" in result.workspace_context
    assert "- Remote Root: /srv/app" in result.workspace_context
    assert "Materialized Local Root:" in result.workspace_context


def test_workspace_ssh_profile_prompt_metadata_excludes_secret_fields(
    tmp_path: Path,
) -> None:
    workspace = _mixed_workspace_handle(tmp_path)
    service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "ssh_profiles.db"),
        config_dir=tmp_path,
        secret_store=SshProfileSecretStore(secret_store=_FileOnlySecretStore()),
    )
    _ = service.save_profile(
        ssh_profile_id="prod-profile",
        config=SshProfileConfig(
            host="prod.example.com",
            username="deploy",
            password="prompt-password",
            port=2222,
            remote_shell="zsh",
            private_key="-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n",
            private_key_name="prod-key.pem",
        ),
    )

    metadata = build_workspace_ssh_profile_prompt_metadata(
        workspace=workspace,
        ssh_profile_service=service,
        consumer="tests.unit_tests.agents.execution.test_prompts",
    )
    prompt = system_prompts.build_workspace_environments_prompt(
        workspace=workspace,
        ssh_profile_metadata=metadata,
    )

    assert "- SSH Host: prod.example.com" in prompt
    assert "- SSH Username: deploy" in prompt
    assert "prompt-password" not in prompt
    assert "OPENSSH PRIVATE KEY" not in prompt
    assert "prod-key.pem" not in prompt
    assert "password" not in prompt.casefold()
    assert "private_key" not in prompt.casefold()
    assert "private key" not in prompt.casefold()
    assert "has_private_key" not in prompt.casefold()


def test_workspace_ssh_profile_prompt_metadata_skips_profiles_without_username(
    tmp_path: Path,
) -> None:
    workspace = _mixed_workspace_handle(tmp_path)
    repository = SshProfileRepository(tmp_path / "ssh_profiles.db")
    service = SshProfileService(
        repository=repository,
        config_dir=tmp_path,
        secret_store=SshProfileSecretStore(secret_store=_FileOnlySecretStore()),
    )
    _ = repository.save(
        ssh_profile_id="prod-profile",
        config=SshProfileStoredConfig(
            host="prod.example.com",
            port=2222,
            remote_shell="zsh",
        ),
    )

    metadata = build_workspace_ssh_profile_prompt_metadata(
        workspace=workspace,
        ssh_profile_service=service,
        consumer="tests.unit_tests.agents.execution.test_prompts",
    )
    prompt = system_prompts.build_workspace_environments_prompt(
        workspace=workspace,
        ssh_profile_metadata=metadata,
    )

    assert metadata == ()
    assert "- SSH Metadata: unavailable" in prompt
    assert "- SSH Username: none" not in prompt
    assert "local operating-system user" in prompt


def test_runtime_system_prompt_keeps_stable_prefix_across_workspaces(
    tmp_path: Path,
) -> None:
    first_workspace = _mixed_workspace_handle(tmp_path / "first")
    second_workspace = _mixed_workspace_handle(tmp_path / "second")

    first_prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("writer_agent"),
                task=_task(),
                shared_state_snapshot=(),
                working_directory=first_workspace.resolve_workdir(),
                worktree_root=first_workspace.scope_root,
                workspace=first_workspace,
            )
        )
    )
    second_prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("writer_agent"),
                task=_task(),
                shared_state_snapshot=(),
                working_directory=second_workspace.resolve_workdir(),
                worktree_root=second_workspace.scope_root,
                workspace=second_workspace,
            )
        )
    )

    first_prefix = first_prompt.split("## Runtime Environment Information", 1)[0]
    second_prefix = second_prompt.split("## Runtime Environment Information", 1)[0]
    assert first_prefix == second_prefix
    assert "## Workspace Environments" not in first_prefix


def test_runtime_environment_prompt_mentions_github_when_token_and_system_gh_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system_gh_path = Path("/usr/bin/gh")
    monkeypatch.setattr(
        system_prompts,
        "_get_github_cli_environment_status",
        lambda: (True, system_gh_path),
    )

    prompt = system_prompts.build_environment_info_prompt(
        working_directory=Path("/tmp/project")
    )

    assert (
        f"- GitHub CLI: token configured; using system gh at {system_gh_path}" in prompt
    )
    assert "- Current Date: 2026-04-02" in prompt
    assert "- Runtime Timezone: HKT (UTC+08:00)" in prompt
    assert "- Python Package Tool (pip): not found on PATH" in prompt
    assert "- Python Package Tool (uv): not found on PATH" in prompt
    assert (
        "use the runtime date in this section as the default source of truth" in prompt
    )


def test_runtime_environment_prompt_mentions_on_demand_gh_when_only_token_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        system_prompts,
        "_get_github_cli_environment_status",
        lambda: (True, None),
    )

    prompt = system_prompts.build_environment_info_prompt(
        working_directory=Path("/tmp/project")
    )

    assert "- GitHub CLI: token configured; gh will be resolved on demand" in prompt
    assert "- Current Date: 2026-04-02" in prompt
    assert "- Runtime Timezone: HKT (UTC+08:00)" in prompt
    assert "- Python Package Tool (pip): not found on PATH" in prompt
    assert "- Python Package Tool (uv): not found on PATH" in prompt


def test_runtime_environment_prompt_mentions_clawhub_when_token_and_binary_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system_clawhub_path = Path("/usr/bin/clawhub")
    monkeypatch.setattr(
        system_prompts,
        "_get_clawhub_environment_status",
        lambda: (True, system_clawhub_path),
    )

    prompt = system_prompts.build_environment_info_prompt(
        working_directory=Path("/tmp/project")
    )

    assert (
        "- ClawHub CLI: token configured; using system clawhub at "
        f"{system_clawhub_path}" in prompt
    )


def test_runtime_environment_prompt_mentions_clawhub_when_only_token_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        system_prompts,
        "_get_clawhub_environment_status",
        lambda: (True, None),
    )

    prompt = system_prompts.build_environment_info_prompt(
        working_directory=Path("/tmp/project")
    )

    assert "- ClawHub CLI: token configured" in prompt


def test_runtime_environment_prompt_mentions_package_tools_and_uv_fallback_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pip_path = Path("/usr/local/bin/pip")
    uv_path = Path("/usr/local/bin/uv")
    monkeypatch.setattr(
        system_prompts,
        "_resolve_package_tool_path",
        lambda command_names: pip_path if command_names == ("pip", "pip3") else uv_path,
    )

    prompt = system_prompts.build_environment_info_prompt(
        working_directory=Path("/tmp/project")
    )

    assert f"- Python Package Tool (pip): {pip_path}" in prompt
    assert f"- Python Package Tool (uv): {uv_path}" in prompt
    assert (
        "If pip install fails with externally-managed-environment (PEP 668), try "
        "uv pip install <packages>." in prompt
    )


def test_runtime_environment_prompt_omits_uv_fallback_hint_when_uv_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pip_path = Path("/usr/local/bin/pip")
    monkeypatch.setattr(
        system_prompts,
        "_resolve_package_tool_path",
        lambda command_names: pip_path if command_names == ("pip", "pip3") else None,
    )

    prompt = system_prompts.build_environment_info_prompt(
        working_directory=Path("/tmp/project")
    )

    assert f"- Python Package Tool (pip): {pip_path}" in prompt
    assert "- Python Package Tool (uv): not found on PATH" in prompt
    assert "uv pip install <packages>" not in prompt


def test_runtime_environment_prompt_uses_runtime_shell_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from relay_teams.tools.workspace_tools.shell_executor import ShellRuntimeSummary

    monkeypatch.setattr(
        system_prompts,
        "_get_github_cli_environment_status",
        lambda: (False, None),
    )
    monkeypatch.setattr(
        "relay_teams.tools.workspace_tools.shell_executor.describe_runtime_shell",
        lambda: ShellRuntimeSummary(
            shell_info="PowerShell",
            shell_path=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        ),
    )

    prompt = system_prompts.build_environment_info_prompt(
        working_directory=Path("/tmp/project")
    )

    assert "Shell Type: PowerShell" in prompt
    assert "powershell.exe" in prompt
    assert "- Current Date: 2026-04-02" in prompt
    assert "- Runtime Timezone: HKT (UTC+08:00)" in prompt
    assert "- Python Package Tool (pip): not found on PATH" in prompt
    assert "- Python Package Tool (uv): not found on PATH" in prompt
    assert (
        "Do not trust your internal knowledge for the current date or time." in prompt
    )


def test_runtime_system_prompt_layers_keep_base_instructions_before_workspace_context() -> (
    None
):
    result = asyncio.run(
        build_runtime_system_prompt_result(
            RuntimePromptBuildInput(
                role=_role("Coordinator"),
                task=_task(),
                topology=RunTopologySnapshot(
                    session_mode=SessionMode.ORCHESTRATION,
                    main_agent_role_id="MainAgent",
                    normal_root_role_id="MainAgent",
                    coordinator_role_id="Coordinator",
                    orchestration_preset_id="default",
                    orchestration_prompt="Delegate by capability and finalize yourself.",
                    allowed_role_ids=("writer_agent",),
                ),
                shared_state_snapshot=(),
                working_directory=Path("/tmp/workspace-root"),
            ),
            role_registry=_coordinator_registry(),
            mcp_registry=_FakeMcpRegistry(),
        )
    )

    assert result.base_instructions.startswith("You are a focused agent.")
    assert "## Orchestration Rules" in result.base_instructions
    assert "## Available Roles" in result.capability_summary
    assert "## Runtime Environment Information" in result.workspace_context
    assert "## Orchestration Prompt" in result.workspace_context
    assert "- Current Date: 2026-04-02" in result.workspace_context
    assert "- Runtime Timezone: HKT (UTC+08:00)" in result.workspace_context
    assert "- Python Package Tool (pip): not found on PATH" in result.workspace_context
    assert "- Python Package Tool (uv): not found on PATH" in result.workspace_context
    assert result.prompt.index("## Orchestration Rules") < result.prompt.index(
        "## Available Roles"
    )
    assert result.prompt.index("## Available Roles") < result.prompt.index(
        "## Runtime Environment Information"
    )


def test_compose_system_prompt_renders_skill_catalog_when_provided() -> None:
    prompt = compose_system_prompt(
        SystemPromptSectionsInput(
            base_instructions="## Role\nYou are a planner.",
            skill_instructions=(
                PromptSkillInstruction(
                    name="time",
                    description="Normalize all times to UTC.",
                ),
            ),
        )
    )

    assert "## Tool Rules" not in prompt
    assert "## Available Skills" in prompt
    assert "- time: Normalize all times to UTC." in prompt
    assert "Use `load_skill` when a listed skill is relevant" in prompt


def test_compose_system_prompt_places_skill_catalog_before_capability_summary() -> None:
    prompt = compose_system_prompt(
        SystemPromptSectionsInput(
            base_instructions="## Role\nYou are a planner.",
            capability_summary="## Available Roles\n### writer_agent",
            workspace_context=(
                "## Runtime Environment Information\n- Working Directory: /tmp/project"
            ),
            skill_instructions=(
                PromptSkillInstruction(
                    name="time",
                    description="Normalize all times to UTC.",
                ),
            ),
        )
    )

    assert prompt.index("## Available Skills") < prompt.index("## Available Roles")
    assert prompt.index("## Available Roles") < prompt.index(
        "## Runtime Environment Information"
    )


def test_user_prompt_builder_returns_raw_objective() -> None:
    prompt = build_user_prompt(
        UserPromptBuildInput(objective="Draft the release notes.")
    )

    assert prompt == "Draft the release notes."


def test_user_prompt_builder_appends_skill_candidates() -> None:
    prompt = build_user_prompt(
        UserPromptBuildInput(
            objective="Draft the release notes.",
            skill_candidates=(
                UserPromptSkillCandidate(
                    name="planner",
                    description="Break objectives into executable plans.",
                ),
            ),
        )
    )

    assert prompt.startswith("Draft the release notes.")
    assert "## Skill Candidates" in prompt
    assert "- planner: Break objectives into executable plans." in prompt


def test_runtime_system_prompt_for_coordinator_mentions_task_orchestration() -> None:
    prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("Coordinator"),
                task=_task(),
                topology=RunTopologySnapshot(
                    session_mode=SessionMode.ORCHESTRATION,
                    main_agent_role_id="MainAgent",
                    normal_root_role_id="MainAgent",
                    coordinator_role_id="Coordinator",
                    orchestration_preset_id="default",
                    orchestration_prompt="Delegate by capability and finalize yourself.",
                    allowed_role_ids=("writer_agent",),
                ),
                shared_state_snapshot=(),
            ),
            role_registry=_coordinator_registry(),
            mcp_registry=_FakeMcpRegistry(),
        )
    )

    assert "### writer_agent" in prompt
    assert "## Orchestration Rules" in prompt
    assert "Orchestration Prompt" in prompt
    assert "Choose roles by their Description, Tools, MCP Tools, and Skills." in prompt
    assert "orch_list_available_roles" in prompt


def test_runtime_system_prompt_for_main_agent_uses_base_role_prompt_only() -> None:
    prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=_role("MainAgent"),
                topology=RunTopologySnapshot(
                    session_mode=SessionMode.NORMAL,
                    main_agent_role_id="MainAgent",
                    normal_root_role_id="MainAgent",
                    coordinator_role_id="Coordinator",
                    orchestration_preset_id=None,
                    orchestration_prompt="",
                    allowed_role_ids=(),
                ),
                shared_state_snapshot=(),
            )
        )
    )

    assert "## Runtime Rules" in prompt
    assert "## Normal Mode" not in prompt
    assert "You are a focused agent." in prompt
    assert "## Available Roles" not in prompt


def test_runtime_system_prompt_for_normal_mode_root_includes_available_subagents() -> (
    None
):
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="Handles direct runs.",
            version="1",
            tools=("read", "spawn_subagent"),
            model_profile="default",
            system_prompt="You are a focused agent.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="Crafter",
            name="Crafter",
            description="Implements requested changes.",
            version="1",
            tools=("read", "write"),
            mode=RoleMode.SUBAGENT,
            model_profile="default",
            system_prompt="You are a crafter.",
        )
    )

    prompt = asyncio.run(
        build_runtime_system_prompt(
            RuntimePromptBuildInput(
                role=registry.get("MainAgent"),
                topology=RunTopologySnapshot(
                    session_mode=SessionMode.NORMAL,
                    main_agent_role_id="MainAgent",
                    normal_root_role_id="MainAgent",
                    coordinator_role_id="Coordinator",
                    orchestration_preset_id=None,
                    orchestration_prompt="",
                    allowed_role_ids=(),
                ),
                shared_state_snapshot=(),
            ),
            role_registry=registry,
        )
    )

    assert "## Subagent Rules" in prompt
    assert "Inspect the `spawn_subagent` tool description" in prompt
    assert "## Available Subagents" not in prompt


def test_runtime_system_prompt_loads_all_project_agents_files_before_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import relay_teams.agents.execution.prompt_instructions as prompt_instructions

    project_root = tmp_path / "project"
    nested_dir = project_root / "src" / "feature"
    config_dir = tmp_path / "config"
    nested_dir.mkdir(parents=True)
    config_dir.mkdir()
    monkeypatch.setattr(
        prompt_instructions,
        "GLOBAL_CLAUDE_FILE",
        tmp_path / "missing" / "CLAUDE.md",
    )
    monkeypatch.setattr(
        prompt_instructions,
        "GLOBAL_GEMINI_FILE",
        tmp_path / "missing" / "GEMINI.md",
    )
    (project_root / "AGENTS.md").write_text(
        "Root project instructions.", encoding="utf-8"
    )
    (project_root / "CLAUDE.md").write_text(
        "Claude root instructions.", encoding="utf-8"
    )
    (project_root / "src" / "AGENTS.md").write_text(
        "Nested project instructions.", encoding="utf-8"
    )

    result = asyncio.run(
        build_runtime_system_prompt_result(
            RuntimePromptBuildInput(
                role=_role("writer_agent"),
                shared_state_snapshot=(),
                working_directory=nested_dir,
                worktree_root=project_root,
            ),
            instruction_resolver=PromptInstructionResolver(app_config_dir=config_dir),
        )
    )

    assert "Root project instructions." in result.prompt
    assert "Nested project instructions." in result.prompt
    assert "Claude root instructions." not in result.prompt
    assert result.local_instruction_paths == (
        (project_root / "src" / "AGENTS.md").resolve(),
        (project_root / "AGENTS.md").resolve(),
    )


def test_runtime_system_prompt_falls_back_to_global_claude_before_gemini(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import relay_teams.agents.execution.prompt_instructions as prompt_instructions

    config_dir = tmp_path / "config"
    home_dir = tmp_path / "home"
    claude_file = home_dir / ".claude" / "CLAUDE.md"
    gemini_file = home_dir / ".gemini" / "GEMINI.md"
    config_dir.mkdir()
    claude_file.parent.mkdir(parents=True)
    gemini_file.parent.mkdir(parents=True)
    claude_file.write_text("Global Claude instructions.", encoding="utf-8")
    gemini_file.write_text("Global Gemini instructions.", encoding="utf-8")
    monkeypatch.setattr(prompt_instructions, "GLOBAL_CLAUDE_FILE", claude_file)
    monkeypatch.setattr(prompt_instructions, "GLOBAL_GEMINI_FILE", gemini_file)

    result = asyncio.run(
        build_runtime_system_prompt_result(
            RuntimePromptBuildInput(
                role=_role("writer_agent"),
                shared_state_snapshot=(),
                working_directory=tmp_path,
                worktree_root=tmp_path,
            ),
            instruction_resolver=PromptInstructionResolver(app_config_dir=config_dir),
        )
    )

    assert "Global Claude instructions." in result.prompt
    assert "Global Gemini instructions." not in result.prompt
    assert result.local_instruction_paths == (claude_file.resolve(),)


def test_runtime_system_prompt_loads_configured_instruction_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    working_dir = project_root / "src"
    config_dir = tmp_path / "config"
    configured_file = project_root / "notes" / "prompt.md"
    working_dir.mkdir(parents=True)
    config_dir.mkdir()
    configured_file.parent.mkdir(parents=True)
    configured_file.write_text("Configured local instructions.", encoding="utf-8")
    resolver = PromptInstructionResolver(
        app_config_dir=config_dir,
        instructions=("notes/*.md", "https://example.test/prompt.md"),
    )

    async def fake_fetch(_url: str) -> str:
        return "Configured remote instructions."

    monkeypatch.setattr(resolver, "_fetch_url", fake_fetch)

    result = asyncio.run(
        build_runtime_system_prompt_result(
            RuntimePromptBuildInput(
                role=_role("writer_agent"),
                shared_state_snapshot=(),
                working_directory=working_dir,
                worktree_root=project_root,
            ),
            instruction_resolver=resolver,
        )
    )

    assert "Configured local instructions." in result.prompt
    assert "Configured remote instructions." in result.prompt
    assert configured_file.resolve() in result.local_instruction_paths
