# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_teams.tools.runtime import (
    ToolError,
    ToolDeps,
    ToolInternalRecord,
    ToolResultEnvelope,
    ToolResultProjection,
)


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
        task_repo=object(),
        shared_store=object(),
        event_bus=object(),
        message_repo=object(),
        approval_ticket_repo=object(),
        run_runtime_repo=object(),
        injection_manager=object(),
        run_event_hub=object(),
        agent_repo=object(),
        workspace=object(),
        role_memory=None,
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        instance_id="instance-1",
        role_id="role-1",
        role_registry=object(),
        mcp_registry=object(),
        task_service=object(),
        task_execution_service=object(),
        run_control_manager=object(),
        tool_approval_manager=object(),
        tool_approval_policy=object(),
        metric_recorder=None,
        notification_service=None,
        feishu_tool_service=None,
    )

    assert deps.session_id == "session-1"
