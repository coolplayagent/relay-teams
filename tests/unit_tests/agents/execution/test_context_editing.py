# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.execution.context_editing import (
    ContextEditJob,
    build_diff_injection,
    build_injection_message,
)


class TestBuildDiffInjection:
    """Cover build_diff_injection branches."""

    def test_no_changes(self) -> None:
        job = build_diff_injection(
            task_id="t1",
            session_id="s1",
            run_id="r1",
            old_spec="same content",
            new_spec="same content",
        )
        assert "No changes detected" in job.diff_description
        assert job.task_id == "t1"
        assert len(job.old_spec_summary) > 0

    def test_short_diff(self) -> None:
        job = build_diff_injection(
            task_id="t1",
            session_id="s1",
            run_id="r1",
            old_spec="line a",
            new_spec="line b",
        )
        assert "Spec updated" in job.diff_description
        assert "line b" in job.diff_description

    def test_long_diff_truncation(self) -> None:
        old_lines = [f"old line {i}" for i in range(60)]
        new_lines = [f"new line {i}" for i in range(60)]
        job = build_diff_injection(
            task_id="t1",
            session_id="s1",
            run_id="r1",
            old_spec="\n".join(old_lines),
            new_spec="\n".join(new_lines),
        )
        assert "truncated" in job.diff_description

    def test_affected_criteria(self) -> None:
        job = build_diff_injection(
            task_id="t1",
            session_id="s1",
            run_id="r1",
            old_spec="old",
            new_spec="new",
            affected_criteria=("crit-1", "crit-2"),
        )
        assert job.affected_criteria == ("crit-1", "crit-2")


class TestBuildInjectionMessage:
    """Cover build_injection_message branches."""

    def test_with_affected_criteria(self) -> None:
        job = ContextEditJob(
            task_id="t1",
            session_id="s1",
            run_id="r1",
            old_spec_summary="old",
            new_spec_summary="new",
            diff_description="changed",
            affected_criteria=("c1",),
        )
        msg = build_injection_message(job)
        assert "CONTEXT EDIT" in msg
        assert "c1" in msg

    def test_without_affected_criteria(self) -> None:
        job = ContextEditJob(
            task_id="t1",
            session_id="s1",
            run_id="r1",
            old_spec_summary="old",
            new_spec_summary="new",
            diff_description="changed",
        )
        msg = build_injection_message(job)
        assert "CONTEXT EDIT" in msg
        assert "Affected acceptance" not in msg
