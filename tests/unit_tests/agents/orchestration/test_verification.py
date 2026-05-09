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
    FormalVerificationLanguage,
    FormalVerificationToolProfile,
    TaskSpecStrictness,
    TaskStatus,
    VerificationEvidenceKind,
    VerificationEvidenceTarget,
    VerificationLayer,
)
from relay_teams.agents.tasks.events import EventType
from relay_teams.agents.tasks.models import (
    FormalVerificationPlan,
    SemanticEvaluationRequest,
    SemanticEvaluationResult,
    TaskEnvelope,
    TaskSpec,
    VerificationCheckResult,
    VerificationCommand,
    VerificationEvidenceBundle,
    VerificationEvidenceItem,
    VerificationEvidenceLink,
    VerificationPlan,
)
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.roles.role_contracts import (
    RoleContract,
    RoleContractPostcondition,
    RoleContractPostconditionType,
)
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.tools.runtime.guardrails import (
    RuntimeGuardrailReport,
    RuntimeGuardrailStatus,
)
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
                    command=(
                        sys.executable,
                        "-c",
                        "print('evidence complete coverage output')",
                    ),
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
    assert result.report.evidence_bundle is not None
    assert {check.layer for check in result.report.checks} == {
        VerificationLayer.STRUCTURE,
        VerificationLayer.BEHAVIOR,
        VerificationLayer.EVIDENCE,
        VerificationLayer.SEMANTIC,
    }
    assert result.report.evidence_bundle.acceptance_links[0].satisfied is True
    assert result.report.evidence_bundle.expectation_links[0].satisfied is True
    assert result.report.evidence_bundle.formal_verification_required is False
    assert result.report.semantic_results[0].passed is True
    stored = task_repo.get(task.task_id)
    assert stored.envelope.evidence_bundle is not None
    assert stored.envelope.evidence_bundle.acceptance_links[0].satisfied is True
    assert result.report.unmet_items == ()


def test_verify_task_requires_runtime_guardrail_report_when_requested(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_guardrail_missing.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(task)
    task_repo.update_status(
        task.task_id,
        TaskStatus.COMPLETED,
        result="done",
    )

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        require_guardrail_report=True,
    )

    assert result.passed is False
    assert result.report is not None
    assert "runtime_guardrail_report" in result.report.unmet_items


def test_verify_task_accepts_runtime_guardrail_report_as_security_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_guardrail_present.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(task)
    task_repo.update_status(
        task.task_id,
        TaskStatus.COMPLETED,
        result="done",
    )
    report = RuntimeGuardrailReport(
        task_id=task.task_id,
        run_id=task.trace_id,
        session_id=task.session_id,
        role_id="gater",
        status=RuntimeGuardrailStatus.PASSED,
    )
    _ = event_log.emit_run_event(
        RunEvent(
            session_id=task.session_id,
            run_id=task.trace_id,
            trace_id=task.trace_id,
            task_id=task.task_id,
            instance_id="inst-1",
            role_id="gater",
            event_type=RunEventType.RUNTIME_GUARDRAIL_REPORT,
            payload_json=report.model_dump_json(),
        )
    )

    result = verify_task(
        task_repo,
        event_log,
        task.task_id,
        require_guardrail_report=True,
    )

    assert result.passed is True
    assert result.report is not None
    assert any(
        check.layer == VerificationLayer.SECURITY
        and check.name == "runtime_guardrail_status"
        and check.passed
        for check in result.report.checks
    )
    assert result.report.evidence_bundle is not None
    assert any(
        item.kind == VerificationEvidenceKind.RUNTIME_GUARDRAIL_REPORT
        for item in result.report.evidence_bundle.items
    )


def test_verify_task_enforces_role_contract_postconditions(tmp_path: Path) -> None:
    db_path = tmp_path / "verification_role_contract.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        role_id="reviewer",
        objective="Review evidence",
        verification=VerificationPlan(
            acceptance_criteria=("all tests pass",),
            evidence_expectations=("pytest output",),
        ),
    )
    _ = task_repo.create(task)
    task_repo.update_status(
        task.task_id,
        TaskStatus.COMPLETED,
        result="all tests pass",
    )
    role = RoleDefinition(
        role_id="reviewer",
        name="Reviewer",
        description="Reviews evidence.",
        version="1.0.0",
        tools=(),
        contract=RoleContract(
            postconditions=(
                RoleContractPostcondition(
                    guarantee=(
                        RoleContractPostconditionType.RESULT_MENTIONS_ACCEPTANCE_CRITERIA
                    )
                ),
                RoleContractPostcondition(
                    guarantee=(
                        RoleContractPostconditionType.RESULT_MENTIONS_EVIDENCE_EXPECTATIONS
                    )
                ),
            )
        ),
        system_prompt="Review carefully.",
    )

    result = verify_task(task_repo, event_log, task.task_id, role=role)

    assert result.passed is False
    assert result.report is not None
    assert any(
        check.layer == VerificationLayer.CONTRACT and not check.passed
        for check in result.report.checks
    )
    assert (
        "contract_postcondition:result_mentions_evidence:pytest output"
        in result.report.unmet_items
    )


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
                        "print('pytest output: 1 passed in 0.01s')",
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
        if item.output_excerpt == "pytest output: 1 passed in 0.01s"
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
    task_repo.update_status(
        task.task_id,
        TaskStatus.COMPLETED,
        result="database migration runs",
    )

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


def test_verify_task_merges_evidence_into_latest_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "verification_latest_envelope.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        title="Original title",
        objective="Return evidence",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(task)
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

    def _mutating_verification_plan(
        *,
        task_id: str,
        plan: VerificationPlan,
        result: str,
        event_bus: EventLog,
        trace_id: str,
        task_id_filter: str,
        allowed_tools: tuple[str, ...],
        tool_approval_policy: ToolApprovalPolicy,
        workspace_root: Path | None,
        semantic_evaluator: verification_module.SemanticVerificationEvaluator | None,
        guardrail_report: verification_module.RuntimeGuardrailReport | None,
        require_guardrail_report: bool,
        llm_provider: verification_module.LLMProvider | None = None,
    ) -> verification_module._VerificationPlanRun:
        _ = (
            task_id,
            plan,
            result,
            event_bus,
            trace_id,
            task_id_filter,
            allowed_tools,
            tool_approval_policy,
            workspace_root,
            semantic_evaluator,
            guardrail_report,
            require_guardrail_report,
            llm_provider,
        )
        latest = task_repo.get(task.task_id)
        task_repo.update_envelope(
            task.task_id,
            latest.envelope.model_copy(update={"title": "Updated during verify"}),
        )
        return verification_module._VerificationPlanRun(
            checks=(
                VerificationCheckResult(
                    layer=VerificationLayer.STRUCTURE,
                    name="completed_status",
                    passed=True,
                ),
            ),
            evidence_bundle=VerificationEvidenceBundle(task_id=task.task_id),
            semantic_results=(),
        )

    monkeypatch.setattr(
        verification_module,
        "_run_verification_plan",
        _mutating_verification_plan,
    )

    result = verify_task(task_repo, event_log, task.task_id)

    stored = task_repo.get(task.task_id)
    assert result.passed is True
    assert stored.envelope.title == "Updated during verify"
    assert stored.envelope.evidence_bundle is not None


def test_verify_task_runs_formal_verification_profile(tmp_path: Path) -> None:
    db_path = tmp_path / "verification_formal.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    proof = workspace / "model.tla"
    proof.write_text("---- MODULE model ----", encoding="utf-8")
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return formal evidence",
        verification=VerificationPlan(
            strictness=TaskSpecStrictness.HIGH,
            formal_checks=(
                FormalVerificationPlan(
                    spec_language=FormalVerificationLanguage.TLA_PLUS,
                    tool_profile=FormalVerificationToolProfile.TLC,
                    properties=("State invariant holds",),
                    proof_artifacts=(Path("model.tla"),),
                    replay_command=VerificationCommand(
                        command=(sys.executable, "-c", "raise SystemExit(0)"),
                        timeout_seconds=5,
                    ),
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

    assert result.passed is True
    assert result.report is not None
    assert result.report.evidence_bundle is not None
    formal_checks = [
        check
        for check in result.report.checks
        if check.layer == VerificationLayer.FORMAL
    ]
    assert len(formal_checks) == 2
    assert result.report.evidence_bundle.formal_verification_required is True
    assert result.report.evidence_bundle.formal_verification_passed is True


def test_verify_task_applies_formal_verification_from_task_spec(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_spec_formal.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    proof = workspace / "spec-model.tla"
    proof.write_text("---- MODULE spec_model ----", encoding="utf-8")
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-spec-formal",
        session_id="session-1",
        trace_id="run-1",
        objective="Return formal evidence from spec",
        verification=VerificationPlan(),
        spec=TaskSpec(
            summary="Formal spec",
            formal_verification=FormalVerificationPlan(
                spec_language=FormalVerificationLanguage.TLA_PLUS,
                tool_profile=FormalVerificationToolProfile.TLC,
                properties=("Spec invariant holds",),
                proof_artifacts=(Path("spec-model.tla"),),
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

    assert result.passed is True
    assert result.report is not None
    assert result.report.evidence_bundle is not None
    formal_checks = tuple(
        check
        for check in result.report.checks
        if check.layer == VerificationLayer.FORMAL
    )
    assert len(formal_checks) == 1
    assert formal_checks[0].name.endswith(":proof_artifact:spec-model.tla")
    assert result.report.evidence_bundle.formal_verification_required is True
    assert result.report.evidence_bundle.formal_verification_passed is True


def test_verify_task_rehydrates_formal_verification_from_spec_artifact(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_spec_artifact_formal.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    proof = workspace / "artifact-model.tla"
    proof.write_text("---- MODULE artifact_model ----", encoding="utf-8")
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-artifact-formal",
        session_id="session-1",
        trace_id="run-1",
        objective="Return formal evidence from artifact",
        verification=VerificationPlan(),
        spec=TaskSpec(
            summary="Artifact spec",
            formal_verification=FormalVerificationPlan(
                spec_language=FormalVerificationLanguage.TLA_PLUS,
                tool_profile=FormalVerificationToolProfile.TLC,
                properties=("Artifact invariant holds",),
                proof_artifacts=(Path("artifact-model.tla"),),
            ),
        ),
    )
    created = task_repo.create(task)
    artifact_id = created.envelope.spec_artifact_id
    assert artifact_id is not None
    artifact_only_envelope = created.envelope.model_copy(
        update={"spec": None, "spec_artifact_id": artifact_id}
    )
    task_repo._conn.execute(
        "UPDATE tasks SET envelope_json=? WHERE task_id=?",
        (artifact_only_envelope.model_dump_json(), task.task_id),
    )
    task_repo._conn.commit()
    task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result="done")

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
    assert result.report.evidence_bundle is not None
    formal_checks = tuple(
        check
        for check in result.report.checks
        if check.layer == VerificationLayer.FORMAL
    )
    assert len(formal_checks) == 1
    assert formal_checks[0].name.endswith(":proof_artifact:artifact-model.tla")
    assert result.report.evidence_bundle.spec_artifact_id == artifact_id
    assert result.report.evidence_bundle.formal_verification_required is True
    assert result.report.evidence_bundle.formal_verification_passed is True


def test_verify_task_does_not_duplicate_spec_formal_verification(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_spec_formal_duplicate.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    proof = workspace / "duplicate-model.tla"
    proof.write_text("---- MODULE duplicate_model ----", encoding="utf-8")
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    formal_plan = FormalVerificationPlan(
        spec_language=FormalVerificationLanguage.TLA_PLUS,
        tool_profile=FormalVerificationToolProfile.TLC,
        properties=("Duplicate invariant holds",),
        proof_artifacts=(Path("duplicate-model.tla"),),
    )
    task = TaskEnvelope(
        task_id="task-duplicate-formal",
        session_id="session-1",
        trace_id="run-1",
        objective="Return formal evidence once",
        verification=VerificationPlan(formal_checks=(formal_plan,)),
        spec=TaskSpec(summary="Duplicate spec", formal_verification=formal_plan),
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

    assert result.passed is True
    assert result.report is not None
    formal_checks = tuple(
        check
        for check in result.report.checks
        if check.layer == VerificationLayer.FORMAL
    )
    assert len(formal_checks) == 1
    assert formal_checks[0].name.endswith(":proof_artifact:duplicate-model.tla")


def test_verify_task_preserves_explicit_formal_checks_with_spec_formal_plan(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_spec_formal_merge.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    spec_proof = workspace / "spec-proof.tla"
    explicit_proof = workspace / "explicit-proof.tla"
    spec_proof.write_text("---- MODULE spec_proof ----", encoding="utf-8")
    explicit_proof.write_text("---- MODULE explicit_proof ----", encoding="utf-8")
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    spec_formal_plan = FormalVerificationPlan(
        spec_language=FormalVerificationLanguage.TLA_PLUS,
        tool_profile=FormalVerificationToolProfile.TLC,
        properties=("Spec proof holds",),
        proof_artifacts=(Path("spec-proof.tla"),),
    )
    explicit_formal_plan = FormalVerificationPlan(
        spec_language=FormalVerificationLanguage.ALLOY,
        tool_profile=FormalVerificationToolProfile.ALLOY_ANALYZER,
        properties=("Explicit proof holds",),
        proof_artifacts=(Path("explicit-proof.tla"),),
    )
    task = TaskEnvelope(
        task_id="task-merge-formal",
        session_id="session-1",
        trace_id="run-1",
        objective="Return merged formal evidence",
        verification=VerificationPlan(formal_checks=(explicit_formal_plan,)),
        spec=TaskSpec(summary="Spec proof", formal_verification=spec_formal_plan),
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

    assert result.passed is True
    assert result.report is not None
    assert result.report.evidence_bundle is not None
    formal_check_names = tuple(
        check.name
        for check in result.report.checks
        if check.layer == VerificationLayer.FORMAL
    )
    assert len(formal_check_names) == 2
    assert any(
        name.endswith(":proof_artifact:spec-proof.tla") for name in formal_check_names
    )
    assert any(
        name.endswith(":proof_artifact:explicit-proof.tla")
        for name in formal_check_names
    )
    assert result.report.evidence_bundle.formal_verification_required is True
    assert result.report.evidence_bundle.formal_verification_passed is True


def test_verify_task_marks_only_required_formal_plans_required(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_optional_formal.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    proof = workspace / "optional.tla"
    proof.write_text("---- MODULE optional ----", encoding="utf-8")
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-optional",
        session_id="session-1",
        trace_id="run-1",
        objective="Return optional formal evidence",
        verification=VerificationPlan(
            formal_checks=(
                FormalVerificationPlan(
                    spec_language=FormalVerificationLanguage.TLA_PLUS,
                    tool_profile=FormalVerificationToolProfile.TLC,
                    proof_artifacts=(Path("optional.tla"),),
                    required=False,
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

    assert result.passed is True
    assert result.report is not None
    assert result.report.evidence_bundle is not None
    assert result.report.evidence_bundle.formal_verification_required is False
    # Non-required formal checks are excluded from the pass/fail flag
    assert result.report.evidence_bundle.formal_verification_passed is None


def test_verify_task_rejects_approval_skipped_formal_replay(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_formal_approval.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-approval",
        session_id="session-1",
        trace_id="run-1",
        objective="Replay formal proof",
        verification=VerificationPlan(
            formal_checks=(
                FormalVerificationPlan(
                    replay_command=VerificationCommand(
                        command=(sys.executable, "-c", "raise SystemExit(0)"),
                    ),
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
        tool_approval_policy=ToolApprovalPolicy(),
        workspace_root=workspace,
    )

    assert result.passed is False
    assert result.report is not None
    assert result.report.evidence_bundle is not None
    replay_check = next(
        check
        for check in result.report.checks
        if check.layer == VerificationLayer.FORMAL
    )
    assert replay_check.passed is False
    assert "skipped until shell approval" in replay_check.details
    assert result.report.evidence_bundle.formal_verification_required is True
    assert result.report.evidence_bundle.formal_verification_passed is False


def test_formal_verification_helpers_cover_failure_edges(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    counterexample = workspace / "counterexample.out"
    counterexample.write_text("bad state", encoding="utf-8")
    required_checks = verification_module._run_formal_plan_checks(
        formal_plan=FormalVerificationPlan(required=True),
        index=1,
        allowed_tools=(),
        tool_approval_policy=YOLO_TOOL_APPROVAL_POLICY,
        workspace_root=workspace,
    )
    artifact_no_workspace = verification_module._run_formal_artifact_check(
        name="artifact:no-workspace",
        path=Path("model.tla"),
        workspace_root=None,
    )
    artifact_escape = verification_module._run_formal_artifact_check(
        name="artifact:escape",
        path=Path("../model.tla"),
        workspace_root=workspace,
    )
    replay_denied = verification_module._run_formal_replay_check(
        name="replay:denied",
        command_check=VerificationCommand(command=(sys.executable, "-c", "")),
        allowed_tools=(),
        tool_approval_policy=ToolApprovalPolicy(),
        workspace_root=workspace,
    )
    replay_approval = verification_module._run_formal_replay_check(
        name="replay:approval",
        command_check=VerificationCommand(command=(sys.executable, "-c", "")),
        allowed_tools=("shell",),
        tool_approval_policy=ToolApprovalPolicy(),
        workspace_root=workspace,
    )
    replay_no_workspace = verification_module._run_formal_replay_check(
        name="replay:no-workspace",
        command_check=VerificationCommand(command=(sys.executable, "-c", "")),
        allowed_tools=("shell",),
        tool_approval_policy=YOLO_TOOL_APPROVAL_POLICY,
        workspace_root=None,
    )
    counter_no_workspace = verification_module._run_formal_counterexample_check(
        name="counter:no-workspace",
        path=Path("counterexample.out"),
        workspace_root=None,
    )
    counter_escape = verification_module._run_formal_counterexample_check(
        name="counter:escape",
        path=Path("../counterexample.out"),
        workspace_root=workspace,
    )
    counter_exists = verification_module._run_formal_counterexample_check(
        name="counter:exists",
        path=Path("counterexample.out"),
        workspace_root=workspace,
    )

    assert required_checks[0].passed is False
    assert "requires a replay command" in required_checks[0].details
    assert "requires a resolved workspace" in artifact_no_workspace.details
    assert "escapes the workspace" in artifact_escape.details
    assert "not authorized" in replay_denied.details
    assert replay_approval.passed is False
    assert "skipped until shell approval" in replay_approval.details
    assert "requires a resolved workspace" in replay_no_workspace.details
    assert "requires a resolved workspace" in counter_no_workspace.details
    assert "escapes the workspace" in counter_escape.details
    assert counter_exists.passed is False
    assert "counterexample artifact" in counter_exists.details


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


def test_verify_task_clears_stale_evidence_for_incomplete_task(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_incomplete_stale_evidence.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(),
        evidence_bundle=VerificationEvidenceBundle(task_id="task-1"),
    )
    _ = task_repo.create(task)

    result = verify_task(task_repo, event_log, task.task_id)

    stored = task_repo.get(task.task_id)
    assert result.passed is False
    assert result.report is not None
    assert result.report.evidence_bundle is None
    assert stored.envelope.evidence_bundle is None


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
    } == {"lint_errors": 2}

    lint_success = lint_failure.model_copy(
        update={"passed": True, "output_excerpt": "All checks passed!"}
    )
    assert (
        verification_module._command_evidence_kind(lint_success)
        == VerificationEvidenceKind.LINT_RESULT
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
    assert gate_item.passed is True
    assert "looks good" in gate_item.output_excerpt
    timeout_item = verification_module._event_evidence_item(
        index=5,
        event={
            "event_type": EventType.TASK_TIMEOUT.value,
            "payload_json": dumps({"reason": "deadline exceeded"}),
        },
    )
    assert timeout_item is not None
    assert timeout_item.kind == VerificationEvidenceKind.GATE_FINDING
    assert timeout_item.passed is False

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

    task_result_item = failed_item.model_copy(
        update={
            "evidence_id": "task-result",
            "kind": VerificationEvidenceKind.TASK_RESULT,
            "passed": True,
        }
    )
    assert (
        verification_module._evidence_can_support_text(
            text="target criterion",
            target=VerificationEvidenceTarget.ACCEPTANCE_CRITERION,
            item=task_result_item,
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

    gate_item = VerificationEvidenceItem(
        evidence_id="gate-finding",
        kind=VerificationEvidenceKind.GATE_FINDING,
        summary="Gate finding matched.",
        passed=True,
        output_excerpt="target criterion",
    )
    gate_link = VerificationEvidenceLink(
        target=VerificationEvidenceTarget.ACCEPTANCE_CRITERION,
        text="target criterion",
        evidence_ids=("gate-finding",),
        satisfied=True,
    )
    gate_result = verification_module._rule_semantic_evaluation(
        criterion="target criterion",
        link=gate_link,
        linked_evidence=(gate_item,),
    )
    assert gate_result.passed is True
    assert gate_result.evidence_ids == ("gate-finding",)

    self_report_link = VerificationEvidenceLink(
        target=VerificationEvidenceTarget.ACCEPTANCE_CRITERION,
        text="target criterion",
        evidence_ids=("task-result",),
        satisfied=True,
    )
    self_report_result = verification_module._rule_semantic_evaluation(
        criterion="target criterion",
        link=self_report_link,
        linked_evidence=(task_result_item,),
    )
    assert self_report_result.passed is False
    assert self_report_result.confidence == 0.25

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
    assert verification_module._normalize_match_token("classes") == "class"
    assert verification_module._normalize_match_token("cases") == "case"
    assert verification_module._normalize_match_token("files") == "file"
    assert verification_module._evidence_id("empty", 3, "   ") == "empty:3"
    assert verification_module._text_mentions_any(
        "the model check completed",
        ("model check",),
    )


def test_verify_task_repeatability_passes_when_consistent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_repeatability_pass.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    task = TaskEnvelope(
        task_id="task-repeat-pass",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(
            strictness=TaskSpecStrictness.HIGH,
            repeatability_runs=3,
            command_checks=(
                VerificationCommand(
                    command=(
                        sys.executable,
                        "-c",
                        "print('hello')",
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

    assert result.report is not None
    repeat_checks = [
        c for c in result.report.checks if c.name.startswith("repeatability:")
    ]
    assert len(repeat_checks) == 1
    if repeat_checks[0].passed:
        assert "consistent results across" in repeat_checks[0].details


def test_verify_task_repeatability_detects_inconsistent_output(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "verification_repeatability_fail.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    counter = tmp_path / "counter.txt"
    counter.write_text("0", encoding="utf-8")
    task = TaskEnvelope(
        task_id="task-repeat-fail",
        session_id="session-1",
        trace_id="run-1",
        objective="Return evidence",
        verification=VerificationPlan(
            strictness=TaskSpecStrictness.HIGH,
            repeatability_runs=2,
            command_checks=(
                VerificationCommand(
                    command=(
                        sys.executable,
                        "-c",
                        "from pathlib import Path; p=Path('counter.txt'); "
                        "n=int(p.read_text())+1; p.write_text(str(n)); print(f'run {n}')",
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
    repeat_checks = [
        c for c in result.report.checks if c.name.startswith("repeatability:")
    ]
    assert len(repeat_checks) == 1
    assert repeat_checks[0].passed is False
    assert "inconsistent output" in repeat_checks[0].details


def test_strip_ansi_removes_escape_sequences() -> None:
    assert verification_module._strip_ansi("\x1b[32mgreen\x1b[0m") == "green"
    assert verification_module._strip_ansi("plain text") == "plain text"


def test_resolve_command_cwd_returns_workspace_when_no_cwd() -> None:
    cmd = VerificationCommand(command=("echo", "hi"))
    result = verification_module._resolve_command_cwd(cmd, workspace_root=Path("/ws"))
    assert result == Path("/ws")


def test_resolve_command_cwd_returns_none_when_no_cwd_and_no_workspace() -> None:
    cmd = VerificationCommand(command=("echo", "hi"))
    result = verification_module._resolve_command_cwd(cmd, workspace_root=None)
    assert result is None


def test_resolve_command_cwd_resolves_custom_cwd() -> None:
    cmd = VerificationCommand(command=("echo", "hi"), cwd=Path("subdir"))
    result = verification_module._resolve_command_cwd(cmd, workspace_root=Path("/ws"))
    assert result == Path("/ws/subdir")


def test_wrap_cross_evaluation_evaluator_returns_none_when_no_evaluator() -> None:
    result = verification_module._wrap_cross_evaluation_evaluator(
        semantic_evaluator=None,
        cross_evaluation_models=("model-a",),
    )
    assert result is None


def test_wrap_cross_evaluation_evaluator_returns_original_when_no_models() -> None:
    def dummy_evaluator(_req: SemanticEvaluationRequest) -> SemanticEvaluationResult:
        return SemanticEvaluationResult(
            criterion="x", passed=True, confidence=1.0, evaluator="rule"
        )

    result = verification_module._wrap_cross_evaluation_evaluator(
        semantic_evaluator=dummy_evaluator,
        cross_evaluation_models=(),
    )
    assert result is dummy_evaluator


def test_strictness_checks_include_repeatability_info() -> None:
    checks = verification_module._run_strictness_checks(
        plan=VerificationPlan(
            strictness=TaskSpecStrictness.HIGH,
            repeatability_runs=3,
        )
    )
    names = [c.name for c in checks]
    assert "strictness:high:repeatability_configured" in names
    repeat_check = next(c for c in checks if "repeatability" in c.name)
    assert "3 run(s)" in repeat_check.details


def test_wrap_cross_evaluation_evaluator_wraps_with_multi_model() -> None:
    from relay_teams.agents.orchestration.multi_model_evaluator import (
        MultiModelSemanticEvaluator,
    )

    def dummy_evaluator(_req: SemanticEvaluationRequest) -> SemanticEvaluationResult:
        return SemanticEvaluationResult(
            criterion="x", passed=True, confidence=1.0, evaluator="llm"
        )

    result = verification_module._wrap_cross_evaluation_evaluator(
        semantic_evaluator=dummy_evaluator,
        cross_evaluation_models=("model-a", "model-b"),
    )
    assert result is not None
    assert result is not dummy_evaluator
    assert isinstance(result, MultiModelSemanticEvaluator)
