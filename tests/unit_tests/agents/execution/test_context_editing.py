# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.execution.context_editing import (
    ContextEditJob,
    ContextEditResult,
    build_diff_injection,
    build_injection_message,
)


def test_build_diff_injection_no_changes():
    job = build_diff_injection(
        task_id="task-1",
        session_id="sess-1",
        run_id="run-1",
        old_spec="same content",
        new_spec="same content",
    )
    assert "No changes detected" in job.diff_description


def test_build_diff_injection_with_changes():
    job = build_diff_injection(
        task_id="task-1",
        session_id="sess-1",
        run_id="run-1",
        old_spec="line 1\nline 2",
        new_spec="line 1\nline 3",
    )
    assert "Spec updated" in job.diff_description
    assert job.affected_criteria == ()


def test_build_diff_injection_with_criteria():
    job = build_diff_injection(
        task_id="task-1",
        session_id="sess-1",
        run_id="run-1",
        old_spec="old",
        new_spec="new",
        affected_criteria=("criterion-1", "criterion-2"),
    )
    assert job.affected_criteria == ("criterion-1", "criterion-2")


def test_build_diff_injection_truncates_long_diff():
    old_lines = "\n".join(f"old line {i}" for i in range(100))
    new_lines = "\n".join(f"new line {i}" for i in range(100))
    job = build_diff_injection(
        task_id="task-1",
        session_id="sess-1",
        run_id="run-1",
        old_spec=old_lines,
        new_spec=new_lines,
    )
    assert "truncated" in job.diff_description


def test_build_injection_message_basic():
    job = ContextEditJob(
        task_id="task-1",
        session_id="sess-1",
        run_id="run-1",
        old_spec_summary="old",
        new_spec_summary="new",
        diff_description="changed",
        affected_criteria=("crit-1",),
        injected_at="2024-01-01T00:00:00",
    )
    msg = build_injection_message(job)
    assert "[CONTEXT EDIT" in msg
    assert "task-1" in msg
    assert "crit-1" in msg


def test_build_injection_message_no_criteria():
    job = ContextEditJob(
        task_id="task-1",
        session_id="sess-1",
        run_id="run-1",
        old_spec_summary="old",
        new_spec_summary="new",
        diff_description="changed",
        affected_criteria=(),
        injected_at="2024-01-01T00:00:00",
    )
    msg = build_injection_message(job)
    assert "Affected acceptance criteria" not in msg


def test_context_edit_result_model():
    job = ContextEditJob(
        task_id="task-1",
        session_id="sess-1",
        run_id="run-1",
        old_spec_summary="old",
        new_spec_summary="new",
        diff_description="changed",
        affected_criteria=(),
        injected_at="2024-01-01T00:00:00",
    )
    result = ContextEditResult(
        job=job,
        injection_message="msg",
        accepted=True,
        reason="ok",
    )
    assert result.accepted is True
    assert result.job.task_id == "task-1"


def test_context_edit_integration_session_runtime_wiring():
    """Integration: verify that context_editing functions are importable
    and compatible with the session_runtime wiring pattern."""
    from relay_teams.agents.execution.context_editing import (
        build_diff_injection,
        build_injection_message,
    )

    decision_content = "Updated spec content with new acceptance criteria"
    old_spec_content = "Original spec content"

    job = build_diff_injection(
        task_id="task-integration",
        session_id="sess-integration",
        run_id="run-integration",
        old_spec=old_spec_content,
        new_spec=decision_content,
    )
    assert job is not None
    assert "Spec updated" in job.diff_description

    edit_message = build_injection_message(job)
    assert "[CONTEXT EDIT" in edit_message
    assert "task-integration" in edit_message


def test_context_edit_no_injection_when_unchanged():
    """Integration: when old and new specs are identical,
    the diff should indicate no changes and the message should still build."""
    from relay_teams.agents.execution.context_editing import (
        build_diff_injection,
        build_injection_message,
    )

    job = build_diff_injection(
        task_id="task-unchanged",
        session_id="sess-unchanged",
        run_id="run-unchanged",
        old_spec="identical spec",
        new_spec="identical spec",
    )
    msg = build_injection_message(job)
    assert "[CONTEXT EDIT" in msg
    assert "No changes detected" in msg
