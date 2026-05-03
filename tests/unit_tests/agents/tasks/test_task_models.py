from __future__ import annotations

import pytest

from pydantic import ValidationError

from relay_teams.agents.tasks.enums import (
    FormalVerificationLanguage,
    FormalVerificationToolProfile,
    TaskSpecStrictness,
    TaskSpecSyncStatus,
    TaskTimeoutAction,
    VerificationEvidenceKind,
    VerificationEvidenceTarget,
    VerificationLayer,
)
from relay_teams.agents.tasks.models import (
    FormalVerificationPlan,
    SemanticEvaluationResult,
    SpecArtifactDiffFieldChange,
    SpecArtifactDiffResult,
    SpecArtifactVersionSummary,
    SpecCheckpointEvaluation,
    SpecCheckpointPolicy,
    TaskEnvelope,
    TaskHandoff,
    TaskLifecyclePolicy,
    TaskSpec,
    VerificationCommand,
    VerificationCheckResult,
    VerificationEvidenceBundle,
    VerificationEvidenceItem,
    VerificationEvidenceLink,
    VerificationEvidenceMetric,
    VerificationPlan,
    VerificationReport,
    _split_command_string,
)


def test_task_envelope_requires_fields() -> None:
    with pytest.raises(ValidationError):
        TaskEnvelope(
            task_id="",
            session_id="s1",
            trace_id="t1",
            objective="obj",
            verification=VerificationPlan(checklist=("echo",)),
        )


def test_task_envelope_accepts_spec_lifecycle_and_handoff() -> None:
    envelope = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Implement endpoint",
        verification=VerificationPlan(),
        spec=TaskSpec(
            summary="Add API endpoint",
            requirements=("persist state", ""),
            constraints=("use pathlib",),
            acceptance_criteria=("unit tests pass",),
            evidence_expectations=("pytest output",),
            strictness=TaskSpecStrictness.HIGH,
            entities=("TaskSpec",),
            approach=("persist the contract as an artifact",),
            structure=("agents/tasks owns the task lifecycle contract",),
            operations=("create artifact",),
            norms=("use typed Pydantic models",),
            safeguards=("do not accept drift without review",),
            prompt_code_sync_status=TaskSpecSyncStatus.IN_SYNC,
            formal_verification=FormalVerificationPlan(
                spec_language=FormalVerificationLanguage.TLA_PLUS,
                tool_profile=FormalVerificationToolProfile.TLC,
                properties=("Task eventually verifies",),
                replay_command=VerificationCommand(command=("true",)),
            ),
        ),
        spec_artifact_id="spec-1",
        spec_source_task_id="task-designer",
        lifecycle=TaskLifecyclePolicy(
            timeout_seconds=30,
            heartbeat_interval_seconds=5,
            on_timeout=TaskTimeoutAction.HUMAN_GATE,
        ),
        handoff=TaskHandoff(next_steps=("rerun tests",), reason="paused"),
    )

    assert envelope.spec is not None
    assert envelope.spec.requirements == ("persist state",)
    assert envelope.spec.entities == ("TaskSpec",)
    assert envelope.spec.formal_verification is not None
    assert envelope.spec.formal_verification.properties == ("Task eventually verifies",)
    assert envelope.spec_artifact_id == "spec-1"
    assert envelope.spec_source_task_id == "task-designer"
    assert envelope.lifecycle.on_timeout == TaskTimeoutAction.HUMAN_GATE
    assert envelope.lifecycle.spec_checkpoint.enabled is True
    assert envelope.handoff is not None
    assert envelope.handoff.next_steps == ("rerun tests",)


def test_task_contract_models_normalize_optional_text_inputs() -> None:
    command = VerificationCommand.model_validate({"command": "pytest -q"})
    verification = VerificationPlan.model_validate(
        {
            "checklist": None,
            "acceptance_criteria": "unit tests pass",
            "evidence_expectations": (" coverage output ", ""),
        }
    )
    spec = TaskSpec.model_validate({"summary": None, "requirements": "persist state"})
    handoff = TaskHandoff.model_validate({"reason": None, "completed": "implemented"})

    assert command.command == ("pytest", "-q")
    assert verification.checklist == ("non_empty_response",)
    assert verification.acceptance_criteria == ("unit tests pass",)
    assert verification.evidence_expectations == ("coverage output",)
    assert spec.summary == ""
    assert spec.requirements == ("persist state",)
    assert spec.strictness == TaskSpecStrictness.MEDIUM
    assert handoff.reason == ""
    assert handoff.completed == ("implemented",)


def test_verification_report_accepts_evidence_bundle() -> None:
    evidence_item = VerificationEvidenceItem(
        evidence_id="command-1",
        kind=VerificationEvidenceKind.TEST_RESULT,
        summary="pytest passed",
        source="verification_check",
        passed=True,
        output_excerpt="1 passed",
        metrics=(VerificationEvidenceMetric(name="tests_passed", value=1),),
        supports=("unit tests pass",),
    )
    report = VerificationReport(
        task_id="task-1",
        passed=True,
        checks=(
            VerificationCheckResult(
                layer=VerificationLayer.SEMANTIC,
                name="semantic_acceptance:unit tests pass",
                passed=True,
            ),
        ),
        evidence_bundle=VerificationEvidenceBundle(
            task_id="task-1",
            items=(evidence_item,),
            acceptance_links=(
                VerificationEvidenceLink(
                    target=VerificationEvidenceTarget.ACCEPTANCE_CRITERION,
                    text="unit tests pass",
                    evidence_ids=("command-1",),
                    satisfied=True,
                ),
            ),
        ),
        semantic_results=(
            SemanticEvaluationResult(
                criterion="unit tests pass",
                passed=True,
                confidence=0.85,
                evidence_ids=("command-1",),
            ),
        ),
    )

    assert report.evidence_bundle is not None
    assert report.evidence_bundle.items[0].metrics[0].value == 1
    assert report.semantic_results[0].evidence_ids == ("command-1",)


def test_task_lifecycle_accepts_spec_checkpoint_policy() -> None:
    lifecycle = TaskLifecyclePolicy.model_validate(
        {
            "spec_checkpoint": {
                "refresh_interval_tool_calls": 3,
                "refresh_interval_messages": 12,
                "refresh_interval_history_tokens": 2000,
                "max_summary_chars": 1200,
            }
        }
    )
    defaulted = TaskLifecyclePolicy.model_validate({"spec_checkpoint": None})

    assert lifecycle.spec_checkpoint == SpecCheckpointPolicy(
        refresh_interval_tool_calls=3,
        refresh_interval_messages=12,
        refresh_interval_history_tokens=2000,
        max_summary_chars=1200,
    )
    assert defaulted.spec_checkpoint.enabled is True


def test_verification_command_uses_windows_aware_string_splitting() -> None:
    assert _split_command_string(
        r"C:\tmp\check.py --flag",
        platform="win32",
    ) == (r"C:\tmp\check.py", "--flag")
    assert _split_command_string(
        r'"C:\Program Files\Python\python.exe" "C:\tmp\check.py"',
        platform="win32",
    ) == (r"C:\Program Files\Python\python.exe", r"C:\tmp\check.py")
    assert _split_command_string(
        r'python -c "print(\"hi\")"',
        platform="win32",
    ) == ("python", "-c", 'print("hi")')


def test_task_contract_models_reject_non_text_sequences() -> None:
    with pytest.raises(TypeError, match="checklist"):
        VerificationPlan.model_validate({"checklist": object()})


def test_spec_checkpoint_policy_new_fields_default_false() -> None:
    policy = SpecCheckpointPolicy()
    assert policy.refresh_on_version_change is False
    assert policy.auto_evaluate_drift is False
    assert policy.drift_score_threshold == 3.0


def test_spec_checkpoint_policy_accepts_new_fields() -> None:
    policy = SpecCheckpointPolicy(
        refresh_on_version_change=True,
        auto_evaluate_drift=True,
        drift_score_threshold=2.5,
    )
    assert policy.refresh_on_version_change is True
    assert policy.auto_evaluate_drift is True
    assert policy.drift_score_threshold == 2.5


def test_spec_checkpoint_policy_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SpecCheckpointPolicy(unknown_field=True)  # type: ignore[call-arg]


def test_spec_artifact_diff_result_model() -> None:
    result = SpecArtifactDiffResult(
        task_id="task-1",
        from_artifact_id="art-1",
        to_artifact_id="art-2",
        from_version=1,
        to_version=2,
        field_changes=(
            SpecArtifactDiffFieldChange(
                field_name="summary",
                field_label="Summary",
                change_type="modified",
                old_value="old",
                new_value="new",
            ),
        ),
        has_changes=True,
        summary="1 field changed: Summary (modified)",
    )
    assert result.has_changes is True
    assert result.from_version == 1
    assert result.to_version == 2
    assert len(result.field_changes) == 1


def test_spec_artifact_version_summary_model() -> None:
    from datetime import datetime, timezone

    summary = SpecArtifactVersionSummary(
        artifact_id="art-1",
        task_id="task-1",
        session_id="sess-1",
        trace_id="trace-1",
        version=1,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )
    assert summary.version == 1
    assert summary.source_task_id is None


def test_spec_checkpoint_evaluation_model() -> None:

    evaluation = SpecCheckpointEvaluation(
        evaluation_id="speval-abc123",
        task_id="task-1",
        artifact_id="art-1",
        session_id="sess-1",
        trace_id="trace-1",
        checkpoint_seq=3,
        evaluator="llm",
        overall_score=4.2,
        drift_detected=False,
    )
    assert evaluation.evaluation_id == "speval-abc123"
    assert evaluation.fallback is False
    assert evaluation.drift_detected is False
    assert evaluation.overall_score == 4.2
