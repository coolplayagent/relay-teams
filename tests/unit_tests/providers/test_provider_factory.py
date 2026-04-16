# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

import relay_teams.providers.provider_factory as runtime_factory_module
from relay_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from relay_teams.agents.orchestration.task_execution_service import TaskExecutionService
from relay_teams.media import MediaAssetService
from relay_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.notifications import NotificationService
from relay_teams.providers.provider_contracts import (
    EchoProvider,
    LLMRequest,
    MisconfiguredProvider,
)
from relay_teams.providers.model_config import ModelEndpointConfig, ProviderType
from relay_teams.providers.model_fallback import LlmFallbackMiddleware
from relay_teams.providers.provider_factory import create_provider_factory
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.sessions.runs.background_tasks import BackgroundTaskService
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.runtime_config import RuntimeConfig, RuntimePaths
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.skills.discovery import SkillsDirectory
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.tools.registry import ToolRegistry
from relay_teams.tools.runtime import ToolApprovalManager, ToolApprovalPolicy
from relay_teams.workspace import WorkspaceManager


class _CapturingProviderRegistry:
    def __init__(self) -> None:
        self.created_config: ModelEndpointConfig | None = None

    def create(self, config: ModelEndpointConfig) -> EchoProvider:
        self.created_config = config
        return EchoProvider()


class _BuilderCallingProviderRegistry:
    def __init__(self, builder) -> None:
        self._builder = builder
        self.created_config: ModelEndpointConfig | None = None

    def create(self, config: ModelEndpointConfig):
        self.created_config = config
        return self._builder(config)


class _CapturingOpenAICompatibleProvider:
    def __init__(self, config: ModelEndpointConfig, **kwargs: object) -> None:
        self.config = config
        self.kwargs = kwargs


def _build_runtime(
    *,
    profiles: dict[str, ModelEndpointConfig],
    default_model_profile: str | None = None,
) -> RuntimeConfig:
    return RuntimeConfig(
        paths=RuntimePaths(
            config_dir=Path(".agent_teams"),
            env_file=Path(".agent_teams/.env"),
            db_path=Path(".agent_teams/relay_teams.db"),
            roles_dir=Path(".agent_teams/roles"),
        ),
        llm_profiles=profiles,
        default_model_profile=default_model_profile,
    )


def _build_role(*, model_profile: str) -> RoleDefinition:
    return RoleDefinition(
        role_id="spec_coder",
        name="Spec Coder",
        description="Implements requested changes.",
        version="1.0.0",
        tools=(),
        mcp_servers=(),
        skills=(),
        model_profile=model_profile,
        system_prompt="Implement code.",
    )


def _build_factory(
    *,
    monkeypatch: pytest.MonkeyPatch,
    runtime: RuntimeConfig,
    provider_registry: _CapturingProviderRegistry,
):
    monkeypatch.setattr(
        runtime_factory_module,
        "create_default_provider_registry",
        lambda **kwargs: provider_registry,
    )
    return create_provider_factory(
        runtime=runtime,
        task_repo=cast(TaskRepository, object()),
        shared_store=cast(SharedStateRepository, object()),
        event_log=cast(EventLog, object()),
        injection_manager=cast(RunInjectionManager, object()),
        run_event_hub=cast(RunEventHub, object()),
        agent_repo=cast(AgentInstanceRepository, object()),
        approval_ticket_repo=cast(ApprovalTicketRepository, object()),
        run_runtime_repo=cast(RunRuntimeRepository, object()),
        run_intent_repo=cast(RunIntentRepository, object()),
        background_task_service=None,
        workspace_manager=cast(WorkspaceManager, object()),
        media_asset_service=cast(MediaAssetService, object()),
        tool_registry=ToolRegistry({}),
        mcp_registry=McpRegistry(),
        skill_registry=SkillRegistry(
            directory=SkillsDirectory(base_dir=Path.cwd() / ".missing-skills")
        ),
        message_repo=cast(MessageRepository, object()),
        session_history_marker_repo=cast(SessionHistoryMarkerRepository, object()),
        role_registry=cast(RoleRegistry, object()),
        get_task_service=lambda: cast(TaskOrchestrationService, object()),
        run_control_manager=cast(RunControlManager, object()),
        tool_approval_manager=cast(ToolApprovalManager, object()),
        tool_approval_policy=cast(ToolApprovalPolicy, object()),
        notification_service=cast(NotificationService | None, None),
        get_task_execution_service=lambda: cast(TaskExecutionService, object()),
        token_usage_repo=cast(TokenUsageRepository | None, None),
        external_agent_session_manager=None,
    )


def test_create_provider_factory_uses_role_model_profile_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_registry = _CapturingProviderRegistry()
    default_config = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="default-model",
        base_url="https://default.example/v1",
        api_key="default-key",
    )
    kimi_config = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="kimi-model",
        base_url="https://kimi.example/v1",
        api_key="kimi-key",
    )
    factory = _build_factory(
        monkeypatch=monkeypatch,
        runtime=_build_runtime(
            profiles={
                "default": default_config,
                "kimi": kimi_config,
            },
            default_model_profile="default",
        ),
        provider_registry=provider_registry,
    )

    provider = factory(_build_role(model_profile="kimi"), None)

    assert isinstance(provider, EchoProvider)
    assert provider_registry.created_config is kimi_config


def test_create_provider_factory_falls_back_to_default_when_profile_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_registry = _CapturingProviderRegistry()
    default_config = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="default-model",
        base_url="https://default.example/v1",
        api_key="default-key",
    )
    factory = _build_factory(
        monkeypatch=monkeypatch,
        runtime=_build_runtime(
            profiles={"default": default_config},
            default_model_profile="default",
        ),
        provider_registry=provider_registry,
    )

    provider = factory(_build_role(model_profile="kimi"), None)

    assert isinstance(provider, EchoProvider)
    assert provider_registry.created_config is default_config


def test_create_provider_factory_resolves_default_alias_to_explicit_default_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_registry = _CapturingProviderRegistry()
    alpha_config = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="alpha-model",
        base_url="https://alpha.example/v1",
        api_key="alpha-key",
    )
    kimi_config = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="kimi-model",
        base_url="https://kimi.example/v1",
        api_key="kimi-key",
    )
    factory = _build_factory(
        monkeypatch=monkeypatch,
        runtime=_build_runtime(
            profiles={
                "default": alpha_config,
                "kimi": kimi_config,
            },
            default_model_profile="kimi",
        ),
        provider_registry=provider_registry,
    )

    provider = factory(_build_role(model_profile="default"), None)

    assert isinstance(provider, EchoProvider)
    assert provider_registry.created_config is kimi_config


def test_create_provider_factory_uses_session_override_for_default_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_registry = _CapturingProviderRegistry()
    default_config = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="default-model",
        base_url="https://default.example/v1",
        api_key="default-key",
    )
    override_config = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="override-model",
        base_url="https://override.example/v1",
        api_key="override-key",
    )
    monkeypatch.setattr(
        runtime_factory_module,
        "create_default_provider_registry",
        lambda **kwargs: provider_registry,
    )
    factory = create_provider_factory(
        runtime=_build_runtime(
            profiles={"default": default_config},
            default_model_profile="default",
        ),
        task_repo=cast(TaskRepository, object()),
        shared_store=cast(SharedStateRepository, object()),
        event_log=cast(EventLog, object()),
        injection_manager=cast(RunInjectionManager, object()),
        run_event_hub=cast(RunEventHub, object()),
        agent_repo=cast(AgentInstanceRepository, object()),
        approval_ticket_repo=cast(ApprovalTicketRepository, object()),
        run_runtime_repo=cast(RunRuntimeRepository, object()),
        run_intent_repo=cast(RunIntentRepository, object()),
        background_task_service=None,
        workspace_manager=cast(WorkspaceManager, object()),
        media_asset_service=cast(MediaAssetService, object()),
        tool_registry=ToolRegistry({}),
        mcp_registry=McpRegistry(),
        skill_registry=SkillRegistry(
            directory=SkillsDirectory(base_dir=Path.cwd() / ".missing-skills")
        ),
        message_repo=cast(MessageRepository, object()),
        session_history_marker_repo=cast(SessionHistoryMarkerRepository, object()),
        role_registry=cast(RoleRegistry, object()),
        get_task_service=lambda: cast(TaskOrchestrationService, object()),
        run_control_manager=cast(RunControlManager, object()),
        tool_approval_manager=cast(ToolApprovalManager, object()),
        tool_approval_policy=cast(ToolApprovalPolicy, object()),
        notification_service=cast(NotificationService | None, None),
        get_task_execution_service=lambda: cast(TaskExecutionService, object()),
        token_usage_repo=cast(TokenUsageRepository | None, None),
        session_model_profile_lookup=lambda session_id: (
            override_config if session_id == "session-1" else None
        ),
    )

    provider = factory(_build_role(model_profile="default"), "session-1")

    assert isinstance(provider, EchoProvider)
    assert provider_registry.created_config is override_config


def test_create_provider_factory_keeps_fallback_middleware_for_session_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_config = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="default-model",
        base_url="https://default.example/v1",
        api_key="default-key",
        fallback_policy_id="same_provider_then_other_provider",
    )
    override_config = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="override-model",
        base_url="https://override.example/v1",
        api_key="override-key",
        fallback_policy_id="same_provider_then_other_provider",
    )
    monkeypatch.setattr(
        runtime_factory_module,
        "OpenAICompatibleProvider",
        _CapturingOpenAICompatibleProvider,
    )
    monkeypatch.setattr(
        runtime_factory_module,
        "create_default_provider_registry",
        lambda **kwargs: _BuilderCallingProviderRegistry(
            kwargs["openai_compatible_builder"]
        ),
    )
    factory = create_provider_factory(
        runtime=_build_runtime(
            profiles={"default": default_config},
            default_model_profile="default",
        ),
        task_repo=cast(TaskRepository, object()),
        shared_store=cast(SharedStateRepository, object()),
        event_log=cast(EventLog, object()),
        injection_manager=cast(RunInjectionManager, object()),
        run_event_hub=cast(RunEventHub, object()),
        agent_repo=cast(AgentInstanceRepository, object()),
        approval_ticket_repo=cast(ApprovalTicketRepository, object()),
        run_runtime_repo=cast(RunRuntimeRepository, object()),
        run_intent_repo=cast(RunIntentRepository, object()),
        background_task_service=None,
        workspace_manager=cast(WorkspaceManager, object()),
        media_asset_service=cast(MediaAssetService, object()),
        tool_registry=ToolRegistry({}),
        mcp_registry=McpRegistry(),
        skill_registry=SkillRegistry(
            directory=SkillsDirectory(base_dir=Path.cwd() / ".missing-skills")
        ),
        message_repo=cast(MessageRepository, object()),
        session_history_marker_repo=cast(SessionHistoryMarkerRepository, object()),
        role_registry=cast(RoleRegistry, object()),
        get_task_service=lambda: cast(TaskOrchestrationService, object()),
        run_control_manager=cast(RunControlManager, object()),
        tool_approval_manager=cast(ToolApprovalManager, object()),
        tool_approval_policy=cast(ToolApprovalPolicy, object()),
        notification_service=cast(NotificationService | None, None),
        get_task_execution_service=lambda: cast(TaskExecutionService, object()),
        token_usage_repo=cast(TokenUsageRepository | None, None),
        external_agent_session_manager=None,
        session_model_profile_lookup=lambda session_id: (
            override_config if session_id == "session-1" else None
        ),
    )

    provider = factory(_build_role(model_profile="default"), "session-1")

    assert isinstance(provider, _CapturingOpenAICompatibleProvider)
    assert provider.kwargs["profile_name"] == "default"
    assert isinstance(provider.kwargs["fallback_middleware"], LlmFallbackMiddleware)


@pytest.mark.asyncio
async def test_create_provider_factory_returns_misconfigured_provider_when_no_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_registry = _CapturingProviderRegistry()
    factory = _build_factory(
        monkeypatch=monkeypatch,
        runtime=_build_runtime(profiles={}, default_model_profile=None),
        provider_registry=provider_registry,
    )

    provider = factory(_build_role(model_profile="default"), None)

    assert isinstance(provider, MisconfiguredProvider)
    with pytest.raises(RuntimeError) as exc_info:
        await provider.generate(
            LLMRequest(
                run_id="run-1",
                trace_id="run-1",
                task_id="task-1",
                session_id="session-1",
                workspace_id="workspace-1",
                conversation_id="conversation-1",
                instance_id="instance-1",
                role_id="spec_coder",
                system_prompt="system",
                user_prompt="hello",
            )
        )

    message = str(exc_info.value)
    assert "No model profile is configured." in message
    assert ".agent_teams" in message
    assert "model.json" in message


def test_create_provider_factory_filters_unknown_runtime_capabilities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_config = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="default-model",
        base_url="https://default.example/v1",
        api_key="default-key",
    )
    tool_registry = ToolRegistry({"read": lambda _agent: None})
    mcp_registry = McpRegistry(
        (
            McpServerSpec(
                name="docs",
                config={"mcpServers": {"docs": {"command": "npx"}}},
                server_config={"command": "npx"},
                source=McpConfigScope.APP,
            ),
        )
    )
    skill_dir = tmp_path / "skills" / "time"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: time\n"
        "description: timezone helper\n"
        "---\n"
        "Use UTC for all timestamps.\n",
        encoding="utf-8",
    )
    skill_registry = SkillRegistry(
        directory=SkillsDirectory(base_dir=tmp_path / "skills")
    )
    monkeypatch.setattr(
        runtime_factory_module,
        "OpenAICompatibleProvider",
        _CapturingOpenAICompatibleProvider,
    )
    monkeypatch.setattr(
        runtime_factory_module,
        "create_default_provider_registry",
        lambda **kwargs: _BuilderCallingProviderRegistry(
            kwargs["openai_compatible_builder"]
        ),
    )
    factory = create_provider_factory(
        runtime=_build_runtime(
            profiles={"default": default_config},
            default_model_profile="default",
        ),
        task_repo=cast(TaskRepository, object()),
        shared_store=cast(SharedStateRepository, object()),
        event_log=cast(EventLog, object()),
        injection_manager=cast(RunInjectionManager, object()),
        run_event_hub=cast(RunEventHub, object()),
        agent_repo=cast(AgentInstanceRepository, object()),
        approval_ticket_repo=cast(ApprovalTicketRepository, object()),
        run_runtime_repo=cast(RunRuntimeRepository, object()),
        run_intent_repo=cast(RunIntentRepository, object()),
        background_task_service=None,
        workspace_manager=cast(WorkspaceManager, object()),
        media_asset_service=cast(MediaAssetService, object()),
        tool_registry=tool_registry,
        mcp_registry=mcp_registry,
        skill_registry=skill_registry,
        message_repo=cast(MessageRepository, object()),
        session_history_marker_repo=cast(SessionHistoryMarkerRepository, object()),
        role_registry=cast(RoleRegistry, object()),
        get_task_service=lambda: cast(TaskOrchestrationService, object()),
        run_control_manager=cast(RunControlManager, object()),
        tool_approval_manager=cast(ToolApprovalManager, object()),
        tool_approval_policy=cast(ToolApprovalPolicy, object()),
        notification_service=cast(NotificationService | None, None),
        get_task_execution_service=lambda: cast(TaskExecutionService, object()),
        token_usage_repo=cast(TokenUsageRepository | None, None),
        external_agent_session_manager=None,
    )

    provider = factory(
        RoleDefinition(
            role_id="spec_coder",
            name="Spec Coder",
            description="Implements requested changes.",
            version="1.0.0",
            tools=("read", "missing_tool"),
            mcp_servers=("docs", "missing_server"),
            skills=("time", "missing_skill"),
            model_profile="default",
            system_prompt="Implement code.",
        ),
        None,
    )

    assert isinstance(provider, _CapturingOpenAICompatibleProvider)
    assert provider.kwargs["allowed_tools"] == ("read",)
    assert provider.kwargs["allowed_mcp_servers"] == ("docs",)
    assert provider.kwargs["allowed_skills"] == ("app:time",)


def test_create_provider_factory_passes_background_task_service_to_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_config = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="default-model",
        base_url="https://default.example/v1",
        api_key="default-key",
    )
    background_task_service = cast(BackgroundTaskService, object())
    monkeypatch.setattr(
        runtime_factory_module,
        "OpenAICompatibleProvider",
        _CapturingOpenAICompatibleProvider,
    )
    monkeypatch.setattr(
        runtime_factory_module,
        "create_default_provider_registry",
        lambda **kwargs: _BuilderCallingProviderRegistry(
            kwargs["openai_compatible_builder"]
        ),
    )
    factory = create_provider_factory(
        runtime=_build_runtime(
            profiles={"default": default_config},
            default_model_profile="default",
        ),
        task_repo=cast(TaskRepository, object()),
        shared_store=cast(SharedStateRepository, object()),
        event_log=cast(EventLog, object()),
        injection_manager=cast(RunInjectionManager, object()),
        run_event_hub=cast(RunEventHub, object()),
        agent_repo=cast(AgentInstanceRepository, object()),
        approval_ticket_repo=cast(ApprovalTicketRepository, object()),
        run_runtime_repo=cast(RunRuntimeRepository, object()),
        run_intent_repo=cast(RunIntentRepository, object()),
        background_task_service=background_task_service,
        workspace_manager=cast(WorkspaceManager, object()),
        media_asset_service=cast(MediaAssetService, object()),
        tool_registry=ToolRegistry({}),
        mcp_registry=McpRegistry(),
        skill_registry=SkillRegistry(
            directory=SkillsDirectory(base_dir=Path.cwd() / ".missing-skills")
        ),
        message_repo=cast(MessageRepository, object()),
        session_history_marker_repo=cast(SessionHistoryMarkerRepository, object()),
        role_registry=cast(RoleRegistry, object()),
        get_task_service=lambda: cast(TaskOrchestrationService, object()),
        run_control_manager=cast(RunControlManager, object()),
        tool_approval_manager=cast(ToolApprovalManager, object()),
        tool_approval_policy=cast(ToolApprovalPolicy, object()),
        notification_service=cast(NotificationService | None, None),
        get_task_execution_service=lambda: cast(TaskExecutionService, object()),
        token_usage_repo=cast(TokenUsageRepository | None, None),
        external_agent_session_manager=None,
    )

    provider = factory(_build_role(model_profile="default"), None)

    assert isinstance(provider, _CapturingOpenAICompatibleProvider)
    assert provider.kwargs["background_task_service"] is background_task_service
