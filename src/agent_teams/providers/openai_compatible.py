# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, final, override

from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelRequest, ModelResponse

from agent_teams.agents.execution.llm_session import AgentLlmSession
from agent_teams.metrics import MetricRecorder
from agent_teams.providers.provider_contracts import LLMProvider, LLMRequest

if TYPE_CHECKING:
    from agent_teams.agents.orchestration.task_execution_service import (
        TaskExecutionService,
    )
    from agent_teams.agents.orchestration.task_orchestration_service import (
        TaskOrchestrationService,
    )
    from agent_teams.mcp.mcp_registry import McpRegistry
    from agent_teams.notifications import NotificationService
    from agent_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
    from agent_teams.roles.memory_service import RoleMemoryService
    from agent_teams.agents.execution.subagent_reflection import (
        SubagentReflectionService,
    )
    from agent_teams.roles.role_registry import RoleRegistry
    from agent_teams.sessions.runs.run_control_manager import RunControlManager
    from agent_teams.sessions.runs.event_stream import RunEventHub
    from agent_teams.sessions.runs.injection_queue import RunInjectionManager
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
    from agent_teams.tools.runtime import (
        ToolApprovalManager,
        ToolApprovalPolicy,
    )
    from agent_teams.workspace import WorkspaceManager
    from agent_teams.tools.feishu_tools import FeishuToolService


@final
class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        config: ModelEndpointConfig,
        *,
        task_repo: TaskRepository,
        shared_store: SharedStateRepository,
        event_bus: EventLog,
        injection_manager: RunInjectionManager,
        run_event_hub: RunEventHub,
        agent_repo: AgentInstanceRepository,
        approval_ticket_repo: ApprovalTicketRepository,
        run_runtime_repo: RunRuntimeRepository,
        run_intent_repo: RunIntentRepository,
        workspace_manager: WorkspaceManager,
        role_memory_service: RoleMemoryService | None,
        subagent_reflection_service: SubagentReflectionService | None,
        tool_registry: ToolRegistry,
        mcp_registry: McpRegistry,
        skill_registry: SkillRegistry,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
        message_repo: MessageRepository,
        role_registry: RoleRegistry,
        task_execution_service: TaskExecutionService,
        task_service: TaskOrchestrationService,
        run_control_manager: RunControlManager,
        tool_approval_manager: ToolApprovalManager,
        tool_approval_policy: ToolApprovalPolicy,
        notification_service: NotificationService | None = None,
        token_usage_repo: TokenUsageRepository | None = None,
        metric_recorder: MetricRecorder | None = None,
        retry_config: LlmRetryConfig | None = None,
        feishu_tool_service: FeishuToolService | None = None,
    ) -> None:
        self._session = AgentLlmSession(
            config=config,
            task_repo=task_repo,
            shared_store=shared_store,
            event_bus=event_bus,
            injection_manager=injection_manager,
            run_event_hub=run_event_hub,
            agent_repo=agent_repo,
            approval_ticket_repo=approval_ticket_repo,
            run_runtime_repo=run_runtime_repo,
            run_intent_repo=run_intent_repo,
            workspace_manager=workspace_manager,
            role_memory_service=role_memory_service,
            subagent_reflection_service=subagent_reflection_service,
            tool_registry=tool_registry,
            mcp_registry=mcp_registry,
            skill_registry=skill_registry,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
            message_repo=message_repo,
            role_registry=role_registry,
            task_execution_service=task_execution_service,
            task_service=task_service,
            run_control_manager=run_control_manager,
            tool_approval_manager=tool_approval_manager,
            tool_approval_policy=tool_approval_policy,
            notification_service=notification_service,
            token_usage_repo=token_usage_repo,
            metric_recorder=metric_recorder,
            retry_config=retry_config,
            feishu_tool_service=feishu_tool_service,
        )

    @override
    async def generate(self, request: LLMRequest) -> str:
        return await self._session.run(request)

    @property
    def _config(self) -> ModelEndpointConfig:
        return self._session._config

    def _publish_tool_call_events_from_messages(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
    ) -> None:
        self._session._publish_tool_call_events_from_messages(
            request=request,
            messages=messages,
        )

    def _publish_committed_tool_outcome_events_from_messages(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
    ) -> None:
        self._session._publish_committed_tool_outcome_events_from_messages(
            request=request,
            messages=messages,
        )

    def _build_model_api_error_message(self, error: ModelAPIError) -> str:
        return self._session._build_model_api_error_message(error)

    def __getattr__(self, name: str) -> object:
        return getattr(self._session, name)

    def __setattr__(self, name: str, value: object) -> None:
        if name == "_session" or "_session" not in self.__dict__:
            object.__setattr__(self, name, value)
            return
        setattr(self._session, name, value)
