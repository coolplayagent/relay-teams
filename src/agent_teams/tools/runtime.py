from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, SkipValidation
from pydantic_ai import RunContext

from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.coordination.task_execution_service import TaskExecutionService
from agent_teams.roles.registry import RoleRegistry
from agent_teams.runtime.run_control_manager import RunControlManager
from agent_teams.runtime.tool_approval_manager import ToolApprovalManager
from agent_teams.state.event_log import EventLog
from agent_teams.runtime.injection_manager import RunInjectionManager
from agent_teams.runtime.run_event_hub import RunEventHub
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository
from agent_teams.tools.policy import ToolApprovalPolicy


class ToolDeps(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    task_repo: SkipValidation[TaskRepository]
    instance_pool: SkipValidation[InstancePool]
    shared_store: SkipValidation[SharedStore]
    event_bus: SkipValidation[EventLog]
    injection_manager: SkipValidation[RunInjectionManager]
    run_event_hub: SkipValidation[RunEventHub]
    agent_repo: SkipValidation[AgentInstanceRepository]
    workspace_root: SkipValidation[Path]
    run_id: str
    trace_id: str
    task_id: str
    session_id: str
    instance_id: str
    role_id: str
    role_registry: SkipValidation[RoleRegistry]
    task_execution_service: SkipValidation[TaskExecutionService]
    run_control_manager: SkipValidation[RunControlManager]
    tool_approval_manager: SkipValidation[ToolApprovalManager]
    tool_approval_policy: SkipValidation[ToolApprovalPolicy]


ToolContext = RunContext[ToolDeps]
