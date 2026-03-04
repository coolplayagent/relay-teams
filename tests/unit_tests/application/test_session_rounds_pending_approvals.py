from agent_teams.application.service import AgentTeamsService
from agent_teams.core.enums import RunEventType


def test_collect_pending_tool_approvals_ignores_validation_failure_events() -> None:
    parsed_events = [
        (
            {
                "trace_id": "run-1",
                "event_type": RunEventType.TOOL_INPUT_VALIDATION_FAILED.value,
                "occurred_at": "2026-03-03T12:00:00+00:00",
                "instance_id": "inst-1",
            },
            {
                "tool_call_id": "call-1",
                "tool_name": "create_workflow_graph",
                "role_id": "coordinator_agent",
            },
        ),
    ]

    result = AgentTeamsService._collect_pending_tool_approvals(parsed_events)
    assert result == {}


def test_collect_pending_tool_approvals_tracks_only_requested_without_result() -> None:
    parsed_events = [
        (
            {
                "trace_id": "run-1",
                "event_type": RunEventType.TOOL_APPROVAL_REQUESTED.value,
                "occurred_at": "2026-03-03T12:00:00+00:00",
                "instance_id": "inst-1",
            },
            {
                "tool_call_id": "call-1",
                "tool_name": "write",
                "role_id": "coordinator_agent",
                "args_preview": "{\"path\":\"a.txt\"}",
            },
        ),
        (
            {
                "trace_id": "run-1",
                "event_type": RunEventType.TOOL_APPROVAL_REQUESTED.value,
                "occurred_at": "2026-03-03T12:00:10+00:00",
                "instance_id": "inst-1",
            },
            {
                "tool_call_id": "call-3",
                "tool_name": "write_stage_doc",
                "role_id": "coordinator_agent",
                "args_preview": "{\"path\":\"stage.md\"}",
            },
        ),
        (
            {
                "trace_id": "run-1",
                "event_type": RunEventType.TOOL_APPROVAL_RESOLVED.value,
                "occurred_at": "2026-03-03T12:00:11+00:00",
                "instance_id": "inst-1",
            },
            {
                "tool_call_id": "call-3",
                "tool_name": "write_stage_doc",
                "role_id": "coordinator_agent",
                "action": "deny",
            },
        ),
        (
            {
                "trace_id": "run-1",
                "event_type": RunEventType.TOOL_APPROVAL_REQUESTED.value,
                "occurred_at": "2026-03-03T12:01:00+00:00",
                "instance_id": "inst-1",
            },
            {
                "tool_call_id": "call-2",
                "tool_name": "shell",
                "role_id": "coordinator_agent",
                "args_preview": "{\"command\":\"echo hi\"}",
            },
        ),
        (
            {
                "trace_id": "run-1",
                "event_type": RunEventType.TOOL_RESULT.value,
                "occurred_at": "2026-03-03T12:01:01+00:00",
                "instance_id": "inst-1",
            },
            {
                "tool_call_id": "call-2",
                "tool_name": "shell",
                "role_id": "coordinator_agent",
            },
        ),
    ]

    result = AgentTeamsService._collect_pending_tool_approvals(parsed_events)
    assert "run-1" in result
    pending = result["run-1"]
    assert pending == [
        {
            "tool_call_id": "call-1",
            "tool_name": "write",
            "args_preview": "{\"path\":\"a.txt\"}",
            "role_id": "coordinator_agent",
            "instance_id": "inst-1",
            "requested_at": "2026-03-03T12:00:00+00:00",
            "status": "requested",
            "feedback": "",
        }
    ]
