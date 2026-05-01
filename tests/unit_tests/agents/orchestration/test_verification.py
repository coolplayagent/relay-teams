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
from relay_teams.agents.tasks.enums import (
    TaskStatus,
    VerificationEvidenceKind,
    VerificationEvidenceTarget,
    VerificationLayer,
)
from relay_teams.agents.tasks.models import (
    SemanticEvaluationRequest,
    SemanticEvaluationResult,
    TaskEnvelope,
    VerificationCommand,
    VerificationCheckResult,
    VerificationEvidenceItem,
    VerificationEvidenceLink,
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


def test_verify_task_ignores_unscoped_tool_result_events(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_unscoped_tool_event_evidence.db"
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

    assert result.passed is False
    assert result.report is not None
    assert result.report.evidence_bundle is not None
    assert result.report.evidence_bundle.acceptance_links[0].satisfied is False
    evidence_kinds = {item.kind.value for item in result.report.evidence_bundle.items}
    assert "tool_result" not in evidence_kinds


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


def test_evidence_helpers_classify_metrics_and_generic_checks() -> None:
    generic_check = VerificationCheckResult(
        layer=VerificationLayer.EVIDENCE,
        name="manual",
        passed=True,
        details="manual evidence",
    )
    assert (
        verification_module._evidence_kind_from_check(generic_check)
        == VerificationEvidenceKind.COMMAND
    )
    assert (
        verification_module._check_evidence_summary(generic_check) == "manual evidence"
    )

    lint_failure = VerificationCheckResult(
        layer=VerificationLayer.BEHAVIOR,
        name="command:ruff",
        passed=False,
        command=("ruff", "check"),
        output_excerpt="Found 2 errors",
    )
    assert (
        verification_module._command_evidence_kind(lint_failure)
        == VerificationEvidenceKind.LINT_RESULT
    )
    assert {
        metric.name: metric.value
        for metric in verification_module._evidence_metrics_for_check(lint_failure)
    } == {"lint_errors": 2, "test_errors": 2}

    lint_success = lint_failure.model_copy(
        update={"passed": True, "output_excerpt": "All checks passed!"}
    )
    assert {
        metric.name: metric.value
        for metric in verification_module._evidence_metrics_for_check(lint_success)
    } == {"lint_errors": 0}

    diff_check = VerificationCheckResult(
        layer=VerificationLayer.BEHAVIOR,
        name="command:diff",
        passed=True,
        command=("git", "diff", "--stat"),
        output_excerpt="3 files changed, 4 insertions(+), 5 deletions(-)",
    )
    assert (
        verification_module._command_evidence_kind(diff_check)
        == VerificationEvidenceKind.DIFF_SUMMARY
    )
    assert {
        metric.name: metric.value
        for metric in verification_module._evidence_metrics_for_check(diff_check)
    } == {"deletions": 5, "files_changed": 3, "insertions": 4}

    formal_check = VerificationCheckResult(
        layer=VerificationLayer.BEHAVIOR,
        name="command:tla",
        passed=False,
        command=("tla", "check"),
        output_excerpt="proof completed",
    )
    assert (
        verification_module._command_evidence_kind(formal_check)
        == VerificationEvidenceKind.FORMAL_PROOF
    )
    assert {
        metric.name: metric.value
        for metric in verification_module._evidence_metrics_for_check(formal_check)
    } == {"formal_checks_passed": 1}

    unittest_check = VerificationCheckResult(
        layer=VerificationLayer.BEHAVIOR,
        name="command:unittest",
        passed=True,
        command=("python", "-m", "unittest"),
        output_excerpt="Ran 7 tests in 0.01s",
    )
    assert {
        metric.name: metric.value
        for metric in verification_module._evidence_metrics_for_check(unittest_check)
    } == {"tests_total": 7}


def test_event_evidence_helpers_parse_scoped_events() -> None:
    invalid_tool_call = verification_module._event_evidence_item(
        index=1,
        event={
            "event_type": RunEventType.TOOL_CALL.value,
            "payload_json": dumps({"tool_name": "", "tool_call_id": ""}),
        },
    )
    assert invalid_tool_call is None

    tool_call = verification_module._event_evidence_item(
        index=2,
        event={
            "event_type": RunEventType.TOOL_CALL.value,
            "payload_json": dumps(
                {"tool_name": "read", "tool_call_id": "call-1", "args": {"path": "a"}}
            ),
        },
    )
    assert tool_call is not None
    assert tool_call.kind == VerificationEvidenceKind.TOOL_CALL
    assert tool_call.output_excerpt == '{"path": "a"}'

    invalid_tool_result = verification_module._event_evidence_item(
        index=3,
        event={
            "event_type": RunEventType.TOOL_RESULT.value,
            "payload_json": dumps({"tool_name": "read"}),
        },
    )
    assert invalid_tool_result is None

    gate_item = verification_module._event_evidence_item(
        index=4,
        event={
            "event_type": "gate_finished",
            "payload_json": dumps({"role_id": "Gater", "findings": ["looks good"]}),
        },
    )
    assert gate_item is not None
    assert gate_item.kind == VerificationEvidenceKind.GATE_FINDING
    assert "looks good" in gate_item.output_excerpt

    assert verification_module._parse_event_payload(None) == {}
    assert verification_module._parse_event_payload("not-json") == {}
    assert verification_module._parse_event_payload("[]") == {}
    assert verification_module._json_excerpt(None) == ""
    assert verification_module._json_excerpt({"bad": {1, 2}})


def test_evidence_linking_and_semantic_helper_edges() -> None:
    failed_item = VerificationEvidenceItem(
        evidence_id="failed",
        kind=VerificationEvidenceKind.COMMAND,
        summary="failed",
        passed=False,
        output_excerpt="target criterion",
    )
    assert (
        verification_module._evidence_can_support_text(
            text="target criterion",
            target=VerificationEvidenceTarget.ACCEPTANCE_CRITERION,
            item=failed_item,
        )
        is False
    )

    skipped_item = failed_item.model_copy(
        update={
            "evidence_id": "skipped",
            "source": "verification_check_skipped",
            "passed": None,
        }
    )
    assert (
        verification_module._evidence_can_support_text(
            text="target criterion",
            target=VerificationEvidenceTarget.EVIDENCE_EXPECTATION,
            item=skipped_item,
        )
        is False
    )

    tool_call_item = VerificationEvidenceItem(
        evidence_id="tool-call",
        kind=VerificationEvidenceKind.TOOL_CALL,
        summary="Tool read was called.",
        passed=None,
        output_excerpt="target criterion",
    )
    assert (
        verification_module._evidence_can_support_text(
            text="target criterion",
            target=VerificationEvidenceTarget.ACCEPTANCE_CRITERION,
            item=tool_call_item,
        )
        is False
    )
    assert (
        verification_module._evidence_link_reason(
            target=VerificationEvidenceTarget.EVIDENCE_EXPECTATION,
            evidence_ids=(),
        )
        == "No concrete evidence item matched this expected evidence."
    )
    assert (
        verification_module._linked_evidence_items(link=None, evidence_items=()) == ()
    )

    weak_link = VerificationEvidenceLink(
        target=VerificationEvidenceTarget.ACCEPTANCE_CRITERION,
        text="target criterion",
        evidence_ids=("tool-call",),
        satisfied=True,
    )
    weak_result = verification_module._rule_semantic_evaluation(
        criterion="target criterion",
        link=weak_link,
        linked_evidence=(tool_call_item,),
    )
    assert weak_result.passed is False
    assert weak_result.evidence_ids == ("tool-call",)

    def evaluator(
        _request: SemanticEvaluationRequest,
    ) -> SemanticEvaluationResult:
        return SemanticEvaluationResult(
            criterion="external criterion",
            passed=True,
            confidence=0.9,
            evaluator="rule",
        )

    evaluated = verification_module._run_optional_semantic_evaluator(
        task_id="task-1",
        criterion="target criterion",
        result="result text",
        linked_evidence=(),
        baseline=weak_result,
        semantic_evaluator=evaluator,
    )
    assert evaluated.criterion == "target criterion"
    assert evaluated.evaluator == "external"

    assert (
        verification_module._text_matches_evidence(text="", item=tool_call_item)
        is False
    )
    assert verification_module._normalize_match_token("fails") == "fail"
    assert verification_module._normalize_match_token("stories") == "story"
    assert verification_module._normalize_match_token("boxes") == "box"
    assert verification_module._evidence_id("empty", 3, "   ") == "empty:3"
    assert verification_module._text_mentions_any(
        "the model check completed",
        ("model check",),
    )
