# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, JsonValue, SkipValidation
from pydantic_ai import RunContext

from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.agents.tasks.models import TaskEnvelope
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.agents.orchestration.task_orchestration_service import (
    TaskDraft,
    TaskUpdate,
)
from agent_teams.computer import ComputerRuntime
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.metrics import MetricRecorder
from agent_teams.media import MediaAssetService
from agent_teams.notifications import NotificationService
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.sessions.runs.background_terminal_manager import (
    BackgroundTerminalManager,
)
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.tools.runtime.approval_state import ToolApprovalManager
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.tools.runtime.policy import ToolApprovalPolicy
from agent_teams.workspace import WorkspaceHandle


class TaskOrchestrationServiceLike(Protocol):
    async def create_tasks(
        self,
        *,
        run_id: str,
        tasks: list[TaskDraft],
    ) -> dict[str, JsonValue]: ...

    def update_task(
        self,
        *,
        run_id: str | None,
        task_id: str,
        update: TaskUpdate,
    ) -> dict[str, JsonValue]: ...

    def list_delegated_tasks(
        self,
        *,
        run_id: str,
        include_root: bool = False,
    ) -> dict[str, JsonValue]: ...

    def list_run_tasks(
        self,
        *,
        run_id: str,
        include_root: bool = False,
    ) -> dict[str, JsonValue]: ...

    def dispatch_task(
        self,
        *,
        run_id: str | None,
        task_id: str,
        role_id: str,
        prompt: str = "",
    ) -> Awaitable[dict[str, JsonValue]]: ...


class TaskExecutionServiceLike(Protocol):
    async def execute(
        self,
        *,
        instance_id: str,
        role_id: str,
        task: TaskEnvelope,
        user_prompt_override: str | None = None,
    ) -> str: ...


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
    run_runtime_repo: SkipValidation[RunRuntimeRepository]
    injection_manager: SkipValidation[RunInjectionManager]
    run_event_hub: SkipValidation[RunEventHub]
    agent_repo: SkipValidation[AgentInstanceRepository]
    workspace: SkipValidation[WorkspaceHandle]
    role_memory: SkipValidation[RoleMemoryService | None] = None
    media_asset_service: SkipValidation[MediaAssetService | None] = None
    computer_runtime: SkipValidation[ComputerRuntime | None] = None
    background_terminal_manager: SkipValidation[BackgroundTerminalManager | None] = None
    run_id: str
    trace_id: str
    task_id: str
    session_id: str
    workspace_id: str
    conversation_id: str
    instance_id: str
    role_id: str
    role_registry: SkipValidation[RoleRegistry]
    runtime_role_resolver: SkipValidation[RuntimeRoleResolver | None] = None
    mcp_registry: SkipValidation[McpRegistry]
    task_service: SkipValidation[TaskOrchestrationServiceLike]
    task_execution_service: SkipValidation[TaskExecutionServiceLike]
    run_control_manager: SkipValidation[RunControlManager]
    tool_approval_manager: SkipValidation[ToolApprovalManager]
    tool_approval_policy: SkipValidation[ToolApprovalPolicy]
    metric_recorder: SkipValidation[MetricRecorder | None] = None
    notification_service: SkipValidation[NotificationService | None] = None
    im_tool_service: SkipValidation[ImToolServiceLike | None] = None


ToolContext = RunContext[ToolDeps]
