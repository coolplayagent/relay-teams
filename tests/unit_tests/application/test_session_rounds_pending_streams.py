from agent_teams.application.service import AgentTeamsService
from agent_teams.core.enums import RunEventType


def test_collect_pending_stream_snapshots_deduplicates_persisted_text() -> None:
    parsed_events = [
        (
            {
                "trace_id": "run-1",
                "event_type": RunEventType.MODEL_STEP_STARTED.value,
                "instance_id": "inst-coord",
            },
            {
                "instance_id": "inst-coord",
                "role_id": "coordinator_agent",
            },
        ),
        (
            {
                "trace_id": "run-1",
                "event_type": RunEventType.TEXT_DELTA.value,
                "instance_id": "inst-coord",
            },
            {
                "instance_id": "inst-coord",
                "role_id": "coordinator_agent",
                "text": "hello world",
            },
        ),
    ]
    session_messages = [
        {
            "trace_id": "run-1",
            "instance_id": "inst-coord",
            "role": "assistant",
            "role_id": "coordinator_agent",
            "message": {
                "parts": [
                    {
                        "part_kind": "text",
                        "content": "hello world",
                    }
                ]
            },
        }
    ]

    result = AgentTeamsService._collect_pending_stream_snapshots(
        parsed_events,
        session_messages,
        {"run-1": {"inst-coord": "coordinator_agent"}},
    )
    assert result == {}


def test_collect_pending_stream_snapshots_returns_only_unpersisted_delta() -> None:
    parsed_events = [
        (
            {
                "trace_id": "run-1",
                "event_type": RunEventType.MODEL_STEP_STARTED.value,
                "instance_id": "inst-coord",
            },
            {
                "instance_id": "inst-coord",
                "role_id": "coordinator_agent",
            },
        ),
        (
            {
                "trace_id": "run-1",
                "event_type": RunEventType.TEXT_DELTA.value,
                "instance_id": "inst-coord",
            },
            {
                "instance_id": "inst-coord",
                "role_id": "coordinator_agent",
                "text": "hello world",
            },
        ),
        (
            {
                "trace_id": "run-1",
                "event_type": RunEventType.TEXT_DELTA.value,
                "instance_id": "inst-coord",
            },
            {
                "instance_id": "inst-coord",
                "role_id": "coordinator_agent",
                "text": "\nmore",
            },
        ),
    ]
    session_messages = [
        {
            "trace_id": "run-1",
            "instance_id": "inst-coord",
            "role": "assistant",
            "role_id": "coordinator_agent",
            "message": {
                "parts": [
                    {
                        "part_kind": "text",
                        "content": "hello world\n",
                    }
                ]
            },
        }
    ]

    result = AgentTeamsService._collect_pending_stream_snapshots(
        parsed_events,
        session_messages,
        {"run-1": {"inst-coord": "coordinator_agent"}},
    )
    assert result == {
        "run-1": {
            "coordinator_text": "more",
            "coordinator_instance_id": "inst-coord",
            "by_instance": {},
        }
    }


def test_collect_pending_stream_snapshots_tracks_subagent_by_instance() -> None:
    parsed_events = [
        (
            {
                "trace_id": "run-2",
                "event_type": RunEventType.MODEL_STEP_STARTED.value,
                "instance_id": "inst-coder",
            },
            {
                "instance_id": "inst-coder",
                "role_id": "spec_coder",
            },
        ),
        (
            {
                "trace_id": "run-2",
                "event_type": RunEventType.TEXT_DELTA.value,
                "instance_id": "inst-coder",
            },
            {
                "instance_id": "inst-coder",
                "role_id": "spec_coder",
                "text": "partial output",
            },
        ),
    ]

    result = AgentTeamsService._collect_pending_stream_snapshots(
        parsed_events,
        [],
        {"run-2": {"inst-coder": "spec_coder"}},
    )
    assert result == {
        "run-2": {
            "coordinator_text": "",
            "coordinator_instance_id": "",
            "by_instance": {
                "inst-coder": "partial output",
            },
        }
    }
