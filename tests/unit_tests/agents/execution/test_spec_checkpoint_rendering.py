from __future__ import annotations

from pathlib import Path

from relay_teams.agents.execution.spec_checkpoint import (
    render_spec_checkpoint,
    task_spec_has_content,
)
from relay_teams.agents.tasks.enums import (
    FormalVerificationLanguage,
    FormalVerificationToolProfile,
    TaskSpecStrictness,
    TaskSpecSyncStatus,
)
from relay_teams.agents.tasks.models import (
    FormalVerificationPlan,
    SpecCheckpointPolicy,
    TaskEnvelope,
    TaskLifecyclePolicy,
    TaskSpec,
    VerificationCommand,
    VerificationPlan,
)


def test_render_includes_reasons_canvas_fields() -> None:
    spec = TaskSpec(
        summary="Test summary",
        requirements=("req-1",),
        entities=("UserService", "OrderService"),
        approach=("event-driven architecture",),
        structure=("src/services/",),
        operations=("create order", "cancel order"),
        norms=("no direct DB access outside repository layer",),
        safeguards=("rollback on partial failure",),
        constraints=("must be backwards compatible",),
        strictness=TaskSpecStrictness.HIGH,
    )
    task = _task(spec=spec)
    content = _render(task)

    assert "- Entities:" in content
    assert "  - UserService" in content
    assert "  - OrderService" in content
    assert "- Approach:" in content
    assert "  - event-driven architecture" in content
    assert "- Structure:" in content
    assert "  - src/services/" in content
    assert "- Operations:" in content
    assert "  - create order" in content
    assert "  - cancel order" in content
    assert "- Norms:" in content
    assert "  - no direct DB access outside repository layer" in content
    assert "- Safeguards:" in content
    assert "  - rollback on partial failure" in content

    lines = content.split("\n")
    req_idx = _section_start_index(lines, "- Requirements:")
    entities_idx = _section_start_index(lines, "- Entities:")
    approach_idx = _section_start_index(lines, "- Approach:")
    structure_idx = _section_start_index(lines, "- Structure:")
    operations_idx = _section_start_index(lines, "- Operations:")
    norms_idx = _section_start_index(lines, "- Norms:")
    safeguards_idx = _section_start_index(lines, "- Safeguards:")
    constraints_idx = _section_start_index(lines, "- Constraints:")

    assert req_idx < entities_idx < approach_idx < structure_idx
    assert structure_idx < operations_idx < norms_idx < safeguards_idx
    assert safeguards_idx < constraints_idx


def test_render_includes_formal_verification() -> None:
    spec = TaskSpec(
        requirements=("req-1",),
        strictness=TaskSpecStrictness.HIGH,
        formal_verification=FormalVerificationPlan(
            spec_language=FormalVerificationLanguage.TLA_PLUS,
            tool_profile=FormalVerificationToolProfile.TLC,
            properties=(
                "NoMsgLost == <>[](sent => received)",
                "OrderConsistency == <>[](order => confirmed)",
            ),
            proof_artifacts=(Path("specs/order.tla"), Path("specs/order.cfg")),
        ),
    )
    task = _task(spec=spec)
    content = _render(task)

    assert "- Formal Verification:" in content
    assert "  - Spec Language: tla_plus" in content
    assert "  - Tool Profile: tlc" in content
    assert "  - Properties:" in content
    assert "    - NoMsgLost == <>[](sent => received)" in content
    assert "    - OrderConsistency == <>[](order => confirmed)" in content
    assert "  - Proof Artifacts:" in content
    assert "    - specs/order.tla" in content
    assert "    - specs/order.cfg" in content


def test_render_includes_formal_verification_optional_fields() -> None:
    spec = TaskSpec(
        requirements=("req-1",),
        strictness=TaskSpecStrictness.HIGH,
        formal_verification=FormalVerificationPlan(
            spec_language=FormalVerificationLanguage.TLA_PLUS,
            tool_profile=FormalVerificationToolProfile.TLC,
            counterexample_path=Path("counterexamples/order_violation.txt"),
            replay_command=VerificationCommand(
                command=("tlc2", "-counterexample", "specs/order.tla")
            ),
        ),
    )
    task = _task(spec=spec)
    content = _render(task)

    assert "  - Counterexample Path: counterexamples/order_violation.txt" in content
    assert "  - Replay Command: tlc2 -counterexample specs/order.tla" in content


def test_render_includes_artifact_metadata() -> None:
    spec = TaskSpec(
        summary="Test spec",
        requirements=("req-1",),
        strictness=TaskSpecStrictness.MEDIUM,
    )
    task = _task(
        spec=spec,
        spec_artifact_id="artifact-abc-123",
        spec_source_task_id="task-designer-456",
    )
    content = _render(task)

    assert "- Spec Artifact ID: artifact-abc-123" in content
    assert "- Spec Source Task ID: task-designer-456" in content


def test_render_includes_sync_status() -> None:
    spec = TaskSpec(
        summary="Test spec",
        requirements=("req-1",),
        strictness=TaskSpecStrictness.MEDIUM,
        prompt_artifact_version=3,
        prompt_code_sync_status=TaskSpecSyncStatus.SPEC_AHEAD,
    )
    task = _task(spec=spec)
    content = _render(task)

    assert "- Prompt Artifact Version: 3" in content
    assert "- Prompt/Code Sync Status: spec_ahead" in content


def test_render_baseline_only_backward_compatible() -> None:
    spec = TaskSpec(
        summary="Build the endpoint",
        requirements=("return HTTP 201",),
        constraints=("do not change the public route",),
        acceptance_criteria=("new API test passes",),
        verification_commands=("pytest tests/unit_tests/api",),
        evidence_expectations=("pytest output",),
        strictness=TaskSpecStrictness.HIGH,
    )
    task = _task(spec=spec)
    content = _render(task)

    assert "- Summary: Build the endpoint" in content
    assert "- Requirements:" in content
    assert "- Constraints:" in content
    assert "- Acceptance Criteria:" in content
    assert "- Verification Commands:" in content
    assert "- Evidence Expectations:" in content
    assert "- Strictness: high" in content

    assert "- Entities:" not in content
    assert "- Approach:" not in content
    assert "- Structure:" not in content
    assert "- Operations:" not in content
    assert "- Norms:" not in content
    assert "- Safeguards:" not in content
    assert "- Formal Verification:" not in content
    assert "- Spec Artifact ID:" not in content
    assert "- Spec Source Task ID:" not in content

    assert not any(
        "  - " in line and "Entities" not in line and line.strip().startswith("- ")
        for line in content.split("\n")
        if line.startswith("- ")
        and line not in ("- Summary: Build the endpoint",)
        and "Strictness" not in line
        and "Prompt" not in line
        and "Completion" not in line
    )


def test_render_empty_reasons_canvas_no_extra_lines() -> None:
    spec = TaskSpec(
        summary="Test",
        requirements=("req-1",),
        entities=(),
        approach=(),
        structure=(),
        operations=(),
        norms=(),
        safeguards=(),
        strictness=TaskSpecStrictness.MEDIUM,
    )
    task = _task(spec=spec)
    content = _render(task)

    assert "- Entities:" not in content
    assert "- Approach:" not in content
    assert "- Structure:" not in content
    assert "- Operations:" not in content
    assert "- Norms:" not in content
    assert "- Safeguards:" not in content


def test_spec_has_content_with_only_reasons_canvas() -> None:
    spec = TaskSpec(
        entities=("UserService",),
        approach=("event-driven",),
        strictness=TaskSpecStrictness.MEDIUM,
    )
    assert task_spec_has_content(spec) is True

    spec_canvas_only = TaskSpec(
        entities=("EntityA",),
        strictness=TaskSpecStrictness.MEDIUM,
    )
    assert task_spec_has_content(spec_canvas_only) is True


def test_spec_has_content_with_formal_verification() -> None:
    spec = TaskSpec(
        formal_verification=FormalVerificationPlan(
            spec_language=FormalVerificationLanguage.TLA_PLUS,
            tool_profile=FormalVerificationToolProfile.TLC,
        ),
        strictness=TaskSpecStrictness.MEDIUM,
    )
    assert task_spec_has_content(spec) is True


def test_spec_has_content_empty_spec_returns_false() -> None:
    spec = TaskSpec()
    assert task_spec_has_content(spec) is False


def test_spec_has_content_baseline_fields_still_detected() -> None:
    spec = TaskSpec(
        summary="A summary",
        strictness=TaskSpecStrictness.MEDIUM,
    )
    assert task_spec_has_content(spec) is True

    spec_req = TaskSpec(
        requirements=("req-1",),
        strictness=TaskSpecStrictness.MEDIUM,
    )
    assert task_spec_has_content(spec_req) is True


def test_truncation_cuts_extended_fields_first() -> None:
    policy = SpecCheckpointPolicy(max_summary_chars=600)
    spec = TaskSpec(
        summary="Core summary",
        requirements=("req-1", "req-2"),
        constraints=("constraint-1",),
        acceptance_criteria=("ac-1",),
        out_of_scope=tuple(f"out of scope item {i}" for i in range(80)),
        strictness=TaskSpecStrictness.HIGH,
    )
    task = _task(spec=spec, policy=policy)
    content = _render(task, policy=policy)

    assert "- Summary: Core summary" in content
    assert "- Requirements:" in content
    assert "  - req-1" in content
    assert "- Constraints:" in content
    assert "- Acceptance Criteria:" in content
    assert content.endswith("[spec checkpoint truncated]")
    assert len(content) <= policy.max_summary_chars
    assert "- Out of Scope:" in content


def test_render_includes_completion_evidence_line() -> None:
    spec = TaskSpec(
        acceptance_criteria=("ac-1",),
        strictness=TaskSpecStrictness.HIGH,
    )
    task = _task(spec=spec)
    content = _render(task)

    assert "- Completion Evidence: cite each acceptance criterion" in content


def test_render_no_completion_evidence_without_criteria_or_expectations() -> None:
    spec = TaskSpec(
        requirements=("req-1",),
        strictness=TaskSpecStrictness.HIGH,
    )
    task = _task(spec=spec)
    content = _render(task)

    assert "Completion Evidence" not in content


def test_render_all_fields_populated() -> None:
    spec = TaskSpec(
        summary="Full spec test",
        requirements=("req-1",),
        entities=("EntityA",),
        approach=("approach-1",),
        structure=("struct-1",),
        operations=("op-1",),
        norms=("norm-1",),
        safeguards=("safe-1",),
        constraints=("const-1",),
        acceptance_criteria=("ac-1",),
        out_of_scope=("oos-1",),
        verification_commands=("vc-1",),
        evidence_expectations=("ee-1",),
        strictness=TaskSpecStrictness.HIGH,
        prompt_artifact_version=2,
        prompt_code_sync_status=TaskSpecSyncStatus.IN_SYNC,
        formal_verification=FormalVerificationPlan(
            spec_language=FormalVerificationLanguage.TLA_PLUS,
            tool_profile=FormalVerificationToolProfile.TLC,
            properties=("prop-1",),
            proof_artifacts=(Path("proof.tla"),),
        ),
    )
    task = _task(
        spec=spec,
        spec_artifact_id="artifact-xyz",
        spec_source_task_id="task-src",
    )
    content = _render(task)

    assert "- Spec Artifact ID: artifact-xyz" in content
    assert "- Spec Source Task ID: task-src" in content
    assert "- Summary: Full spec test" in content
    assert "- Requirements:" in content and "  - req-1" in content
    assert "- Entities:" in content and "  - EntityA" in content
    assert "- Approach:" in content and "  - approach-1" in content
    assert "- Structure:" in content and "  - struct-1" in content
    assert "- Operations:" in content and "  - op-1" in content
    assert "- Norms:" in content and "  - norm-1" in content
    assert "- Safeguards:" in content and "  - safe-1" in content
    assert "- Constraints:" in content and "  - const-1" in content
    assert "- Acceptance Criteria:" in content and "  - ac-1" in content
    assert "- Out of Scope:" in content and "  - oos-1" in content
    assert "- Verification Commands:" in content and "  - vc-1" in content
    assert "- Evidence Expectations:" in content and "  - ee-1" in content
    assert "- Strictness: high" in content
    assert "- Prompt Artifact Version: 2" in content
    assert "- Prompt/Code Sync Status: in_sync" in content
    assert "- Formal Verification:" in content
    assert "  - Spec Language: tla_plus" in content
    assert "  - Tool Profile: tlc" in content
    assert "  - Properties:" in content
    assert "    - prop-1" in content
    assert "  - Proof Artifacts:" in content
    assert "    - proof.tla" in content
    assert "- Completion Evidence:" in content


def test_render_spec_has_content_with_all_canvas_fields() -> None:
    for field_name in (
        "entities",
        "approach",
        "structure",
        "operations",
        "norms",
        "safeguards",
    ):
        spec = TaskSpec(
            **{field_name: ("some value",)},  # type: ignore[arg-type]
            strictness=TaskSpecStrictness.MEDIUM,
        )
        assert task_spec_has_content(spec) is True, (
            f"Expected True for field {field_name}"
        )


def test_render_includes_version_change_section() -> None:
    spec = TaskSpec(
        summary="Test",
        requirements=("req-1",),
        strictness=TaskSpecStrictness.MEDIUM,
    )
    task = _task(spec=spec)
    version_change = (1, 2, "2 fields changed: Summary, Requirements")
    content = _render(task, version_change=version_change)

    assert "### Spec Version Change" in content
    assert "- Previous Version: 1" in content
    assert "- Current Version: 2" in content
    assert "- Diff Summary: 2 fields changed: Summary, Requirements" in content


def test_render_no_version_change_section_when_none() -> None:
    spec = TaskSpec(
        summary="Test",
        requirements=("req-1",),
        strictness=TaskSpecStrictness.MEDIUM,
    )
    task = _task(spec=spec)
    content = _render(task, version_change=None)

    assert "### Spec Version Change" not in content
    assert "Previous Version" not in content


def _section_start_index(lines: list[str], prefix: str) -> int:
    for idx, line in enumerate(lines):
        if line == prefix:
            return idx
    return -1


def _render(
    task: TaskEnvelope,
    policy: SpecCheckpointPolicy | None = None,
    version_change: tuple[int, int, str] | None = None,
) -> str:
    resolved_policy = policy or task.lifecycle.spec_checkpoint
    return render_spec_checkpoint(
        task=task,
        role_id="Crafter",
        sequence=1,
        reason="messages>=1",
        policy=resolved_policy,
        tool_calls_since_checkpoint=0,
        messages_since_checkpoint=1,
        tokens_since_checkpoint=12,
        version_change=version_change,
    )


def _task(
    *,
    spec: TaskSpec | None = None,
    policy: SpecCheckpointPolicy | None = None,
    spec_artifact_id: str | None = None,
    spec_source_task_id: str | None = None,
) -> TaskEnvelope:
    return TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        role_id="Crafter",
        objective="Build endpoint",
        verification=VerificationPlan(),
        spec=spec
        or TaskSpec(
            summary="Build the endpoint",
            requirements=("return HTTP 201",),
            constraints=("do not change the public route",),
            acceptance_criteria=("new API test passes",),
            verification_commands=("pytest tests/unit_tests/api",),
            evidence_expectations=("pytest output",),
            strictness=TaskSpecStrictness.HIGH,
        ),
        spec_artifact_id=spec_artifact_id,
        spec_source_task_id=spec_source_task_id,
        lifecycle=TaskLifecyclePolicy(spec_checkpoint=policy or SpecCheckpointPolicy()),
    )
