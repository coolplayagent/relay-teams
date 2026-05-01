# -*- coding: utf-8 -*-
from __future__ import annotations

from json import dumps
import subprocess
import sys
import threading
from pathlib import Path
from typing import cast

import pytest

from relay_teams.agents.orchestration import verification as verification_module
from relay_teams.agents.orchestration.verification import verify_task
from relay_teams.agents.tasks.enums import TaskStatus, VerificationLayer
from relay_teams.agents.tasks.models import (
    SemanticEvaluationRequest,
    SemanticEvaluationResult,
    TaskEnvelope,
    VerificationCommand,
    VerificationPlan,
)
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.tools.runtime.policy import ToolApprovalPolicy

YOLO_TOOL_APPROVAL_POLICY = ToolApprovalPolicy(yolo=True)


class _SlowTerminationProcess:
    returncode: int | None = None

    def __init__(self) -> None:
        self.killed = False
        self.wait_timeouts: list[float | None] = []

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if timeout is not None:
            raise subprocess.TimeoutExpired(cmd=("fake-verification",), timeout=timeout)
        self.returncode = -9
        return self.returncode


class _ClosableStream:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _ProcessWithStreams:
    def __init__(self) -> None:
        self.stdout = _ClosableStream()
        self.stderr = _ClosableStream()


class _SlowOutputThread:
    def __init__(self) -> None:
        self.join_timeouts: list[float | None] = []

    def join(self, timeout: float | None = None) -> None:
        self.join_timeouts.append(timeout)


def test_verify_task_builds_structured_report(tmp_path: Path) -> None:
    db_path = tmp_path / "verification.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    evidence = workspace / "evidence.txt"
    evidence.write_text("ok", encoding="utf-8")
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(
            checklist=("non_empty_response",),
            required_files=(Path("evidence.txt"),),
            command_checks=(
                VerificationCommand(
                    command=(sys.executable, "-c", "raise SystemExit(0)"),
                    timeout_seconds=5,
                ),
            ),
            acceptance_criteria=("evidence complete",),
            evidence_expectations=("coverage output",),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(
        task.task_id,
        TaskStatus.COMPLETED,
        result="The evidence complete criterion is satisfied. coverage output attached.",
    )

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        allowed_tools=("shell",),
        tool_approval_policy=YOLO_TOOL_APPROVAL_POLICY,
        workspace_root=workspace,
    )

    assert result.passed is True
    assert result.report is not None
    assert {check.layer for check in result.report.checks} == {
        VerificationLayer.STRUCTURE,
        VerificationLayer.BEHAVIOR,
        VerificationLayer.EVIDENCE,
        VerificationLayer.SEMANTIC,
    }
    assert result.report.evidence_bundle is not None
    assert result.report.evidence_bundle.acceptance_links[0].satisfied is True
    assert result.report.evidence_bundle.expectation_links[0].satisfied is True
    assert result.report.semantic_results[0].passed is True
    assert result.report.unmet_items == ()


def test_verify_task_links_command_output_to_spec_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_command_evidence.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Run tests",
        verification=VerificationPlan(
            command_checks=(
                VerificationCommand(
                    command=(
                        sys.executable,
                        "-c",
                        "print('1 passed in 0.01s')",
                    ),
                    timeout_seconds=5,
                ),
            ),
            acceptance_criteria=("unit tests pass",),
            evidence_expectations=("pytest output",),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        allowed_tools=("shell",),
        tool_approval_policy=YOLO_TOOL_APPROVAL_POLICY,
        workspace_root=tmp_path,
    )

    assert result.passed is True
    assert result.report is not None
    assert result.report.evidence_bundle is not None
    command_evidence = next(
        item
        for item in result.report.evidence_bundle.items
        if item.output_excerpt == "1 passed in 0.01s"
    )
    assert command_evidence.kind.value == "test_result"
    assert command_evidence.metrics[0].name == "tests_passed"
    assert command_evidence.metrics[0].value == 1
    assert result.report.evidence_bundle.acceptance_links[0].evidence_ids == (
        command_evidence.evidence_id,
    )
    assert result.report.evidence_bundle.expectation_links[0].evidence_ids == (
        command_evidence.evidence_id,
    )
    assert result.report.semantic_results[0].passed is True
    assert result.report.semantic_results[0].confidence == 0.85


def test_verify_task_fails_acceptance_without_matching_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_missing_evidence.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Run migration",
        verification=VerificationPlan(
            acceptance_criteria=("database migration runs",),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    result = verify_task(task_repo, event_log, task.task_id)

    assert result.passed is False
    assert result.report is not None
    assert result.report.evidence_bundle is not None
    assert result.report.evidence_bundle.acceptance_links[0].satisfied is False
    failed_checks = [check for check in result.report.checks if not check.passed]
    assert "acceptance_evidence:database migration runs" in {
        check.name for check in failed_checks
    }
    assert "semantic_acceptance:database migration runs" in {
        check.name for check in failed_checks
    }


def test_verify_task_uses_tool_result_events_as_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_tool_event_evidence.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Read artifact",
        verification=VerificationPlan(
            acceptance_criteria=("read evidence file",),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")
    event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            task_id="task-1",
            event_type=RunEventType.TOOL_RESULT,
            payload_json=dumps(
                {
                    "tool_name": "read",
                    "tool_call_id": "call-1",
                    "result": "read evidence file contents",
                    "error": False,
                }
            ),
        )
    )

    result = verify_task(task_repo, event_log, task.task_id)

    assert result.passed is True
    assert result.report is not None
    assert result.report.evidence_bundle is not None
    linked_id = result.report.evidence_bundle.acceptance_links[0].evidence_ids[0]
    linked_item = next(
        item
        for item in result.report.evidence_bundle.items
        if item.evidence_id == linked_id
    )
    assert linked_item.kind.value == "tool_result"
    assert result.report.semantic_results[0].evidence_ids == (linked_id,)


def test_verify_task_uses_rule_fallback_when_semantic_evaluator_fails(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_semantic_fallback.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Run tests",
        verification=VerificationPlan(
            command_checks=(
                VerificationCommand(
                    command=(
                        sys.executable,
                        "-c",
                        "print('1 passed in 0.01s')",
                    ),
                    timeout_seconds=5,
                ),
            ),
            acceptance_criteria=("unit tests pass",),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    def failing_evaluator(
        _request: SemanticEvaluationRequest,
    ) -> SemanticEvaluationResult:
        raise RuntimeError("model unavailable")

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        allowed_tools=("shell",),
        tool_approval_policy=YOLO_TOOL_APPROVAL_POLICY,
        workspace_root=tmp_path,
        semantic_evaluator=failing_evaluator,
    )

    assert result.passed is True
    assert result.report is not None
    semantic_result = result.report.semantic_results[0]
    assert semantic_result.passed is True
    assert semantic_result.evaluator == "rule"
    assert "rule fallback used" in semantic_result.reason


def test_verify_task_required_file_rejects_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "verification_required_file_directory.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact_path = workspace / "artifact.txt"
    artifact_path.mkdir()
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return artifact",
        verification=VerificationPlan(required_files=(Path("artifact.txt"),)),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        workspace_root=workspace,
    )

    assert result.passed is False
    assert result.report is not None
    structure_check = next(
        check
        for check in result.report.checks
        if check.name == "required_file:artifact.txt"
    )
    assert structure_check.layer == VerificationLayer.STRUCTURE
    assert structure_check.passed is False
    assert "not a file" in structure_check.details


def test_verify_task_required_file_fails_without_workspace(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_required_file_no_workspace.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return artifact",
        verification=VerificationPlan(required_files=(Path("artifact.txt"),)),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    result = verify_task(task_repo, event_log, task.task_id)

    assert result.passed is False
    assert result.report is not None
    structure_check = next(
        check
        for check in result.report.checks
        if check.name == "required_file:artifact.txt"
    )
    assert structure_check.passed is False
    assert "requires a resolved workspace" in structure_check.details


def test_verify_task_required_file_rejects_workspace_escape(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_required_file_escape.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("not task evidence", encoding="utf-8")
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return artifact",
        verification=VerificationPlan(required_files=(Path("../outside.txt"),)),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        workspace_root=workspace,
    )

    assert result.passed is False
    assert result.report is not None
    expected_check_name = f"required_file:{Path('../outside.txt')}"
    structure_check = next(
        check for check in result.report.checks if check.name == expected_check_name
    )
    assert structure_check.passed is False
    assert "escapes the workspace" in structure_check.details


def test_verify_task_reports_incomplete_task(tmp_path: Path) -> None:
    db_path = tmp_path / "verification_incomplete.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(),
    )
    _ = task_repo.create(task)

    result = verify_task(task_repo, event_log, task.task_id)

    assert result.passed is False
    assert result.report is not None
    assert result.details == ("Task not completed yet",)
    assert result.report.checks[0].name == "completed_status"


def test_verify_task_reports_failed_command(tmp_path: Path) -> None:
    db_path = tmp_path / "verification_failed.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(
            command_checks=(
                VerificationCommand(
                    command=(sys.executable, "-c", "raise SystemExit(7)"),
                    timeout_seconds=5,
                ),
            ),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        allowed_tools=("shell",),
        tool_approval_policy=YOLO_TOOL_APPROVAL_POLICY,
        workspace_root=tmp_path,
    )

    assert result.passed is False
    assert result.report is not None
    failed = [check for check in result.report.checks if not check.passed]
    assert len(failed) == 1
    assert failed[0].layer == VerificationLayer.BEHAVIOR
    assert failed[0].exit_code == 7


def test_verify_task_splits_string_command_checks(tmp_path: Path) -> None:
    db_path = tmp_path / "verification_string_command.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(
            command_checks=(
                VerificationCommand.model_validate(
                    {
                        "command": f'{sys.executable} -c "raise SystemExit(0)"',
                        "timeout_seconds": 5,
                    }
                ),
            ),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        allowed_tools=("shell",),
        tool_approval_policy=YOLO_TOOL_APPROVAL_POLICY,
        workspace_root=tmp_path,
    )

    assert result.passed is True
    assert result.report is not None
    command_check = result.report.checks[-1]
    assert command_check.command == (
        sys.executable,
        "-c",
        "raise SystemExit(0)",
    )


def test_verify_task_denies_command_checks_without_shell_authorization(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_policy.db"
    marker = tmp_path / "marker.txt"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(
            command_checks=(
                VerificationCommand(
                    command=(
                        sys.executable,
                        "-c",
                        f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
                    ),
                    timeout_seconds=5,
                ),
            ),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    result = verify_task(task_repo, event_log, task.task_id)

    assert result.passed is False
    assert result.report is not None
    failed = [check for check in result.report.checks if not check.passed]
    assert len(failed) == 1
    assert "not authorized" in failed[0].details
    assert not marker.exists()


def test_verify_task_skips_command_checks_requiring_shell_approval(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_approval_policy.db"
    marker = tmp_path / "approval-marker.txt"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(
            command_checks=(
                VerificationCommand(
                    command=(
                        sys.executable,
                        "-c",
                        f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
                    ),
                    timeout_seconds=5,
                ),
            ),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        allowed_tools=("shell",),
        workspace_root=tmp_path,
    )

    assert result.passed is True
    assert result.report is not None
    command_check = result.report.checks[-1]
    assert command_check.passed is True
    assert "skipped until shell approval" in command_check.details
    assert not marker.exists()


def test_verify_task_denies_command_checks_without_workspace(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_workspace_policy.db"
    marker = tmp_path / "workspace-marker.txt"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(
            command_checks=(
                VerificationCommand(
                    command=(
                        sys.executable,
                        "-c",
                        f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
                    ),
                    timeout_seconds=5,
                ),
            ),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        allowed_tools=("shell",),
        tool_approval_policy=YOLO_TOOL_APPROVAL_POLICY,
    )

    assert result.passed is False
    assert result.report is not None
    failed = [check for check in result.report.checks if not check.passed]
    assert len(failed) == 1
    assert "requires a resolved workspace" in failed[0].details
    assert not marker.exists()


def test_verify_task_denies_command_cwd_workspace_escape(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_workspace_escape.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker.txt"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(
            command_checks=(
                VerificationCommand(
                    command=(
                        sys.executable,
                        "-c",
                        "from pathlib import Path; Path('marker.txt').write_text('ran')",
                    ),
                    cwd=Path("../outside"),
                    timeout_seconds=5,
                ),
            ),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        allowed_tools=("shell",),
        tool_approval_policy=YOLO_TOOL_APPROVAL_POLICY,
        workspace_root=workspace,
    )

    assert result.passed is False
    assert result.report is not None
    failed = [check for check in result.report.checks if not check.passed]
    assert len(failed) == 1
    assert "escapes the workspace" in failed[0].details
    assert not marker.exists()


def test_verify_task_handles_non_utf8_command_output(tmp_path: Path) -> None:
    db_path = tmp_path / "verification_binary.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(
            command_checks=(
                VerificationCommand(
                    command=(
                        sys.executable,
                        "-c",
                        "import sys; sys.stdout.buffer.write(b'\\xff')",
                    ),
                    timeout_seconds=5,
                ),
            ),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        allowed_tools=("shell",),
        tool_approval_policy=YOLO_TOOL_APPROVAL_POLICY,
        workspace_root=tmp_path,
    )

    assert result.passed is True
    assert result.report is not None
    command_check = result.report.checks[-1]
    assert command_check.output_excerpt == "\ufffd"


def test_kill_process_waits_until_process_exits() -> None:
    process = _SlowTerminationProcess()

    returncode = verification_module._kill_process_and_wait(
        cast(subprocess.Popen[bytes], process)
    )

    assert process.killed is True
    assert process.wait_timeouts == [None]
    assert returncode == -9


def test_finish_process_output_closes_streams_after_join_timeout() -> None:
    process = _ProcessWithStreams()
    thread = _SlowOutputThread()

    verification_module._finish_process_output(
        cast(subprocess.Popen[bytes], process),
        (cast(threading.Thread, thread),),
    )

    assert process.stdout.closed is True
    assert process.stderr.closed is True
    assert thread.join_timeouts == [
        verification_module._OUTPUT_READER_JOIN_TIMEOUT_SECONDS,
        verification_module._OUTPUT_READER_JOIN_TIMEOUT_SECONDS,
    ]


@pytest.mark.timeout(3)
def test_verify_task_reports_command_timeout(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_timeout.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(
            command_checks=(
                VerificationCommand(
                    command=(
                        sys.executable,
                        "-c",
                        "import sys, time; "
                        "sys.stdout.write('partial stdout'); "
                        "sys.stdout.flush(); "
                        "sys.stderr.write('partial stderr'); "
                        "sys.stderr.flush(); "
                        "time.sleep(5)",
                    ),
                    timeout_seconds=1.0,
                ),
            ),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        allowed_tools=("shell",),
        tool_approval_policy=YOLO_TOOL_APPROVAL_POLICY,
        workspace_root=tmp_path,
    )

    assert result.passed is False
    assert result.report is not None
    command_check = result.report.checks[-1]
    assert "timed out" in command_check.details
    assert command_check.output_excerpt == "partial stdout\npartial stderr"


def test_verify_task_reports_command_os_error(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_os_error.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(
            command_checks=(
                VerificationCommand(
                    command=("definitely-missing-verification-command-7d7f1e65",),
                    timeout_seconds=1,
                ),
            ),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        allowed_tools=("shell",),
        tool_approval_policy=YOLO_TOOL_APPROVAL_POLICY,
        workspace_root=tmp_path,
    )

    assert result.passed is False
    assert result.report is not None
    command_check = result.report.checks[-1]
    assert command_check.details


def test_verify_task_truncates_long_command_output(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_long_output.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(
            command_checks=(
                VerificationCommand(
                    command=(
                        sys.executable,
                        "-c",
                        "import sys; sys.stdout.write('x' * 2001)",
                    ),
                    timeout_seconds=5,
                ),
            ),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        allowed_tools=("shell",),
        tool_approval_policy=YOLO_TOOL_APPROVAL_POLICY,
        workspace_root=tmp_path,
    )

    assert result.passed is True
    assert result.report is not None
    assert result.report.checks[-1].output_excerpt.endswith("...(truncated)")


def test_verify_task_fails_when_command_output_exceeds_capture_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verification_module, "_COMMAND_OUTPUT_CAPTURE_BYTES", 32)
    db_path = tmp_path / "verification_output_limit.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(
            command_checks=(
                VerificationCommand(
                    command=(
                        sys.executable,
                        "-c",
                        "import sys; sys.stdout.write('x' * 128)",
                    ),
                    timeout_seconds=5,
                ),
            ),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        allowed_tools=("shell",),
        tool_approval_policy=YOLO_TOOL_APPROVAL_POLICY,
        workspace_root=tmp_path,
    )

    assert result.passed is False
    assert result.report is not None
    command_check = result.report.checks[-1]
    assert "output exceeded 32 byte" in command_check.details
    assert command_check.output_excerpt.endswith("...(truncated)")
