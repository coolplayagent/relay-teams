# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from relay_teams.sessions.runs.background_tasks.projection import (
    build_background_task_completion_message,
    build_background_task_payload,
    build_background_task_result_payload,
)


def _build_record(*, output_excerpt: str) -> BackgroundTaskRecord:
    return BackgroundTaskRecord(
        background_task_id="background_task_123",
        run_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="writer",
        tool_call_id="call-1",
        command="python worker.py",
        cwd="/workspace",
        status=BackgroundTaskStatus.COMPLETED,
        output_excerpt=output_excerpt,
        log_path="tmp/background_tasks/background_task_123.log",
    )


def test_build_background_task_payload_truncates_large_output_excerpt() -> None:
    record = _build_record(output_excerpt="a" * 40000)

    payload = build_background_task_payload(record)

    assert payload["output_truncated"] is True
    assert isinstance(payload["output_excerpt"], str)
    assert len(payload["output_excerpt"]) <= 32000
    assert "see log_path for full output" in payload["output_excerpt"]


def test_build_background_task_result_payload_reuses_truncated_visible_output() -> None:
    record = _build_record(output_excerpt="b" * 40000)

    payload = build_background_task_result_payload(
        record,
        completed=True,
        include_task_id=False,
    )

    assert payload["output_truncated"] is True
    assert payload["background_task_id"] is None
    assert payload["output"] == payload["output_excerpt"]


def test_build_background_task_completion_message_starts_with_followup_instruction() -> (
    None
):
    record = _build_record(output_excerpt="done")

    message = build_background_task_completion_message(record)

    assert message.startswith(
        "A managed background task finished. Respond to the user with one short status update"
    )
    assert "<background-task-notification>" in message
    assert "<status>completed</status>" in message
