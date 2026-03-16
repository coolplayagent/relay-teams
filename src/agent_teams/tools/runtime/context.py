# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, SkipValidation
from pydantic_ai import RunContext

from agent_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from agent_teams.agents.orchestration.task_execution_service import TaskExecutionService
from agent_teams.notifications import NotificationService
from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.tools.runtime.approval_state import ToolApprovalManager
from agent_teams.tools.runtime.policy import ToolApprovalPolicy
from agent_teams.workspace import WorkspaceHandle


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
    run_id: str
    trace_id: str
    task_id: str
    session_id: str
    workspace_id: str
    conversation_id: str
    instance_id: str
    role_id: str
    role_registry: SkipValidation[RoleRegistry]
    task_service: SkipValidation[TaskOrchestrationService]
    task_execution_service: SkipValidation[TaskExecutionService]
    run_control_manager: SkipValidation[RunControlManager]
    tool_approval_manager: SkipValidation[ToolApprovalManager]
    tool_approval_policy: SkipValidation[ToolApprovalPolicy]
    notification_service: SkipValidation[NotificationService | None] = None


ToolContext = RunContext[ToolDeps]
