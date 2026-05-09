# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, SkipValidation
from pydantic_ai import RunContext

from relay_teams.audit import AuditService
from relay_teams.agent_runtimes.instances.instance_repository import (
    AgentInstanceRepository,
)
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.orchestration.task_contracts import (
    TaskExecutionServiceLike,
    TaskOrchestrationServiceLike,
)
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.computer import ComputerRuntime
from relay_teams.gateway.gateway_models import GatewaySessionRecord
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.metrics import MetricRecorder
from relay_teams.media import MediaAssetService
from relay_teams.monitors import MonitorService
from relay_teams.notifications import NotificationService
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.providers.model_config import ModelCapabilities
from relay_teams.reminders.service import SystemReminderService
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.background_tasks.service import BackgroundTaskService
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.sessions.runs.user_question_manager import UserQuestionManager
from relay_teams.sessions.runs.user_question_repository import UserQuestionRepository
from relay_teams.sessions.runs.todo_service import TodoService
from relay_teams.tools.runtime.approval_state import ToolApprovalManager
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.tools.runtime.policy import ToolApprovalPolicy
from relay_teams.tools.workspace_tools.shell_approval_repo import (
    ShellApprovalRepository,
)
from relay_teams.workspace import WorkspaceHandle
from relay_teams.hooks import HookService


class ImToolServiceLike(Protocol):
    async def send_text(
        self,
        *,
        session_id: str,
        text: str,
        run_id: str | None = None,
    ) -> str: ...

    async def send_file(
        self,
        *,
        session_id: str,
        file_path: Path,
        run_id: str | None = None,
    ) -> str: ...


@runtime_checkable
class GatewaySessionLookupLike(Protocol):
    def get_by_internal_session_id(
        self,
        internal_session_id: str,
    ) -> GatewaySessionRecord | None:
        raise NotImplementedError  # pragma: no cover


class XiaolubanSecretStatusLike(Protocol):
    token_configured: bool


class XiaolubanNotifyAccountLike(Protocol):
    account_id: str
    display_name: str
    status: object
    derived_uid: str
    notification_receivers: tuple[str, ...]
    secret_status: XiaolubanSecretStatusLike


@runtime_checkable
class XiaolubanNotifyServiceLike(Protocol):
    @staticmethod
    def list_accounts() -> tuple[XiaolubanNotifyAccountLike, ...]:
        raise NotImplementedError  # pragma: no cover

    @staticmethod
    def get_account(account_id: str) -> XiaolubanNotifyAccountLike:
        raise NotImplementedError  # pragma: no cover

    @staticmethod
    def has_usable_credentials(account_id: str) -> bool:
        raise NotImplementedError  # pragma: no cover

    async def send_notification_message(
        self,
        *,
        account_id: str,
        workspace_id: str,
        session_id: str,
        status: str,
        body: str,
        receiver_uid: str | None = None,
    ) -> str:
        raise NotImplementedError  # pragma: no cover


class SkillRegistryLike(Protocol):
    def get_skill_definition(self, name: str) -> object | None: ...

    def resolve_authorized_name_for_role(
        self,
        *,
        role: object,
        requested_name: str,
        consumer: str | None = None,
    ) -> str | None: ...


_SKIP_VALIDATION: object = SkipValidation


class ToolDeps(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    task_repo: Annotated[TaskRepository, _SKIP_VALIDATION]
    shared_store: Annotated[SharedStateRepository, _SKIP_VALIDATION]
    event_bus: Annotated[EventLog, _SKIP_VALIDATION]
    message_repo: Annotated[MessageRepository, _SKIP_VALIDATION]
    approval_ticket_repo: Annotated[ApprovalTicketRepository, _SKIP_VALIDATION]
    user_question_repo: Annotated[UserQuestionRepository | None, _SKIP_VALIDATION] = (
        None
    )
    run_runtime_repo: Annotated[RunRuntimeRepository, _SKIP_VALIDATION]
    injection_manager: Annotated[RunInjectionManager, _SKIP_VALIDATION]
    run_event_hub: Annotated[RunEventHub, _SKIP_VALIDATION]
    agent_repo: Annotated[AgentInstanceRepository, _SKIP_VALIDATION]
    workspace: Annotated[WorkspaceHandle, _SKIP_VALIDATION]
    role_memory: Annotated[RoleMemoryService | None, _SKIP_VALIDATION] = None
    media_asset_service: Annotated[MediaAssetService | None, _SKIP_VALIDATION] = None
    computer_runtime: Annotated[ComputerRuntime | None, _SKIP_VALIDATION] = None
    background_task_service: Annotated[
        BackgroundTaskService | None, _SKIP_VALIDATION
    ] = None
    monitor_service: Annotated[MonitorService | None, _SKIP_VALIDATION] = None
    todo_service: Annotated[TodoService | None, _SKIP_VALIDATION] = None
    run_id: str
    trace_id: str
    task_id: str
    session_id: str
    session_mode: str = "normal"
    run_kind: str = "conversation"
    workspace_id: str
    conversation_id: str
    instance_id: str
    role_id: str
    role_registry: Annotated[RoleRegistry, _SKIP_VALIDATION]
    runtime_role_resolver: Annotated[RuntimeRoleResolver | None, _SKIP_VALIDATION] = (
        None
    )
    skill_registry: Annotated[SkillRegistryLike | None, _SKIP_VALIDATION] = None
    mcp_registry: Annotated[McpRegistry, _SKIP_VALIDATION]
    task_service: Annotated[TaskOrchestrationServiceLike, _SKIP_VALIDATION]
    task_execution_service: Annotated[TaskExecutionServiceLike, _SKIP_VALIDATION]
    run_control_manager: Annotated[RunControlManager, _SKIP_VALIDATION]
    tool_approval_manager: Annotated[ToolApprovalManager, _SKIP_VALIDATION]
    user_question_manager: Annotated[UserQuestionManager | None, _SKIP_VALIDATION] = (
        None
    )
    tool_approval_policy: Annotated[ToolApprovalPolicy, _SKIP_VALIDATION]
    shell_approval_repo: Annotated[ShellApprovalRepository | None, _SKIP_VALIDATION] = (
        None
    )
    metric_recorder: Annotated[MetricRecorder | None, _SKIP_VALIDATION] = None
    notification_service: Annotated[NotificationService | None, _SKIP_VALIDATION] = None
    im_tool_service: Annotated[ImToolServiceLike | None, _SKIP_VALIDATION] = None
    xiaoluban_notify_service: XiaolubanNotifyServiceLike | None = None
    gateway_session_lookup: GatewaySessionLookupLike | None = None
    hook_service: Annotated[HookService | None, _SKIP_VALIDATION] = None
    reminder_service: Annotated[SystemReminderService | None, _SKIP_VALIDATION] = None
    auto_harness_service: Annotated[object | None, _SKIP_VALIDATION] = None
    audit_service: Annotated[AuditService | None, _SKIP_VALIDATION] = None
    model_capabilities: Annotated[ModelCapabilities, _SKIP_VALIDATION] = Field(
        default_factory=ModelCapabilities
    )
    hook_runtime_env: dict[str, str] = Field(default_factory=dict)


ToolContext = RunContext[ToolDeps]
