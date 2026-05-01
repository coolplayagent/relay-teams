# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, SkipValidation
from pydantic_ai import RunContext

from relay_teams.audit import AuditService
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
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
from relay_teams.reminders import SystemReminderService
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.background_tasks import BackgroundTaskService
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
    def send_text(
        self,
        *,
        session_id: str,
        text: str,
        run_id: str | None = None,
    ) -> str: ...

    def send_file(
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
    def list_accounts(self) -> tuple[XiaolubanNotifyAccountLike, ...]:
        raise NotImplementedError  # pragma: no cover

    def get_account(self, account_id: str) -> XiaolubanNotifyAccountLike:
        raise NotImplementedError  # pragma: no cover

    def has_usable_credentials(self, account_id: str) -> bool:
        raise NotImplementedError  # pragma: no cover

    def send_notification_message(
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


class ToolDeps(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    task_repo: SkipValidation[TaskRepository]
    shared_store: SkipValidation[SharedStateRepository]
    event_bus: SkipValidation[EventLog]
    message_repo: SkipValidation[MessageRepository]
    approval_ticket_repo: SkipValidation[ApprovalTicketRepository]
    user_question_repo: SkipValidation[UserQuestionRepository | None] = None
    run_runtime_repo: SkipValidation[RunRuntimeRepository]
    injection_manager: SkipValidation[RunInjectionManager]
    run_event_hub: Annotated[RunEventHub, SkipValidation]
    agent_repo: SkipValidation[AgentInstanceRepository]
    workspace: SkipValidation[WorkspaceHandle]
    role_memory: SkipValidation[RoleMemoryService | None] = None
    media_asset_service: SkipValidation[MediaAssetService | None] = None
    computer_runtime: SkipValidation[ComputerRuntime | None] = None
    background_task_service: SkipValidation[BackgroundTaskService | None] = None
    monitor_service: SkipValidation[MonitorService | None] = None
    todo_service: SkipValidation[TodoService | None] = None
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
    role_registry: SkipValidation[RoleRegistry]
    runtime_role_resolver: SkipValidation[RuntimeRoleResolver | None] = None
    skill_registry: SkipValidation[SkillRegistryLike | None] = None
    mcp_registry: SkipValidation[McpRegistry]
    task_service: SkipValidation[TaskOrchestrationServiceLike]
    task_execution_service: SkipValidation[TaskExecutionServiceLike]
    run_control_manager: SkipValidation[RunControlManager]
    tool_approval_manager: SkipValidation[ToolApprovalManager]
    user_question_manager: SkipValidation[UserQuestionManager | None] = None
    tool_approval_policy: SkipValidation[ToolApprovalPolicy]
    shell_approval_repo: SkipValidation[ShellApprovalRepository | None] = None
    metric_recorder: SkipValidation[MetricRecorder | None] = None
    notification_service: SkipValidation[NotificationService | None] = None
    im_tool_service: SkipValidation[ImToolServiceLike | None] = None
    xiaoluban_notify_service: XiaolubanNotifyServiceLike | None = None
    gateway_session_lookup: GatewaySessionLookupLike | None = None
    hook_service: SkipValidation[HookService | None] = None
    reminder_service: SkipValidation[SystemReminderService | None] = None
    auto_harness_service: SkipValidation[object | None] = None
    audit_service: SkipValidation[AuditService | None] = None
    model_capabilities: SkipValidation[ModelCapabilities] = Field(
        default_factory=ModelCapabilities
    )
    hook_runtime_env: dict[str, str] = Field(default_factory=dict)


ToolContext = RunContext[ToolDeps]
