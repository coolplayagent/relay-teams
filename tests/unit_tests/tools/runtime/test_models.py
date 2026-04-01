# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest
from pydantic import ValidationError
from typing import cast

from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.agents.orchestration.task_execution_service import (
    TaskExecutionService,
)
from agent_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.tools.runtime.approval_state import ToolApprovalManager
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.tools.runtime.policy import ToolApprovalPolicy
from agent_teams.tools.runtime import (
    ToolError,
    ToolDeps,
    ToolInternalRecord,
    ToolResultEnvelope,
    ToolResultProjection,
)
from agent_teams.workspace import WorkspaceHandle


def test_tool_result_envelope_serializes_nested_error() -> None:
    error = ToolError(
        type="validation_error",
        message="bad input",
        retryable=True,
    )

    envelope = ToolResultEnvelope(
        ok=False,
        error=error,
    )

    payload = envelope.model_dump(mode="json")

    assert payload["error"] == {
        "type": "validation_error",
        "message": "bad input",
        "retryable": True,
    }


def test_tool_result_envelope_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ToolResultEnvelope.model_validate(
            {
                "ok": True,
                "extra_field": "unexpected",
            }
        )


def test_tool_internal_record_stores_visible_result_and_runtime_meta() -> None:
    record = ToolInternalRecord(
        tool="shell",
        visible_result=ToolResultEnvelope(
            ok=True,
            data={"output": "/tmp", "exit_code": 0},
            error=None,
        ),
        internal_data={"stdout": "/tmp\n", "stderr": ""},
        runtime_meta={"approval_status": "not_required"},
    )

    payload = record.model_dump(mode="json")

    assert payload["tool"] == "shell"
    assert payload["visible_result"]["data"]["output"] == "/tmp"
    assert payload["runtime_meta"]["approval_status"] == "not_required"


def test_tool_result_projection_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ToolResultProjection.model_validate(
            {
                "visible_data": {"output": "ok"},
                "internal_data": {"stdout": "ok"},
                "unexpected": True,
            }
        )


def test_tool_deps_can_be_instantiated_without_model_rebuild() -> None:
    deps = ToolDeps(
        task_repo=cast(TaskRepository, object()),
        shared_store=cast(SharedStateRepository, object()),
        event_bus=cast(EventLog, object()),
        message_repo=cast(MessageRepository, object()),
        approval_ticket_repo=cast(ApprovalTicketRepository, object()),
        run_runtime_repo=cast(RunRuntimeRepository, object()),
        injection_manager=cast(RunInjectionManager, object()),
        run_event_hub=cast(RunEventHub, object()),
        agent_repo=cast(AgentInstanceRepository, object()),
        workspace=cast(WorkspaceHandle, object()),
        role_memory=None,
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        instance_id="instance-1",
        role_id="role-1",
        role_registry=cast(RoleRegistry, object()),
        mcp_registry=cast(McpRegistry, object()),
        task_service=cast(TaskOrchestrationService, object()),
        task_execution_service=cast(TaskExecutionService, object()),
        run_control_manager=cast(RunControlManager, object()),
        tool_approval_manager=cast(ToolApprovalManager, object()),
        tool_approval_policy=cast(ToolApprovalPolicy, object()),
        metric_recorder=None,
        notification_service=None,
        im_tool_service=None,
    )

    assert deps.session_id == "session-1"
