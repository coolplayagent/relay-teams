# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

import agent_teams.providers.provider_factory as runtime_factory_module
from agent_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from agent_teams.agents.orchestration.task_execution_service import TaskExecutionService
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.notifications import NotificationService
from agent_teams.providers.provider_contracts import (
    EchoProvider,
    LLMRequest,
    MisconfiguredProvider,
)
from agent_teams.providers.model_config import ModelEndpointConfig, ProviderType
from agent_teams.providers.provider_factory import create_provider_factory
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.runtime_config import RuntimeConfig, RuntimePaths
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.providers.token_usage_repo import TokenUsageRepository
from agent_teams.tools.registry import ToolRegistry
from agent_teams.tools.runtime import ToolApprovalManager, ToolApprovalPolicy
from agent_teams.workspace import WorkspaceManager


class _CapturingProviderRegistry:
    def __init__(self) -> None:
        self.created_config: ModelEndpointConfig | None = None

    def create(self, config: ModelEndpointConfig) -> EchoProvider:
        self.created_config = config
        return EchoProvider()


def _build_runtime(
    *,
    profiles: dict[str, ModelEndpointConfig],
    default_model_profile: str | None = None,
) -> RuntimeConfig:
    return RuntimeConfig(
        paths=RuntimePaths(
            config_dir=Path(".agent_teams"),
            env_file=Path(".agent_teams/.env"),
            db_path=Path(".agent_teams/agent_teams.db"),
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
        workspace_manager=cast(WorkspaceManager, object()),
        tool_registry=cast(ToolRegistry, object()),
        mcp_registry=cast(McpRegistry, object()),
        skill_registry=cast(SkillRegistry, object()),
        message_repo=cast(MessageRepository, object()),
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

    provider = factory(_build_role(model_profile="kimi"))

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

    provider = factory(_build_role(model_profile="kimi"))

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

    provider = factory(_build_role(model_profile="default"))

    assert isinstance(provider, EchoProvider)
    assert provider_registry.created_config is kimi_config


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

    provider = factory(_build_role(model_profile="default"))

    assert isinstance(provider, MisconfiguredProvider)
    with pytest.raises(
        RuntimeError,
        match=r"No model profile is configured",
    ):
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
