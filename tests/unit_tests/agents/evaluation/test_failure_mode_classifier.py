# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from relay_teams.agents.evaluation.failure_mode_classifier import (
    FailureModeClassifier,
)
from relay_teams.agents.evaluation.failure_modes import (
    FailureMode,
    FailureModeClassification,
)
from relay_teams.agents.evaluation.run_sampling_service import SampledRun
from relay_teams.agents.tasks.enums import VerificationLayer
from relay_teams.agents.tasks.models import (
    SpecCheckpointEvaluation,
    TaskSpec,
    VerificationCheckResult,
    VerificationReport,
)
from relay_teams.sessions.runs.enums import RunEventType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    event_type: str,
    payload: dict[str, object] | None = None,
    *,
    event_id: str = "evt-1",
) -> dict[str, object]:
    import json

    return {
        "event_type": event_type,
        "payload_json": json.dumps(payload or {}),
        "id": event_id,
    }


def _make_classifier(
    *,
    event_log_events: tuple[dict[str, object], ...] = (),
    event_log_side_effect: object | None = None,
) -> FailureModeClassifier:
    event_log = MagicMock()
    event_log.list_by_session_run_ids_event_types_async = AsyncMock(
        return_value=tuple(event_log_events) if event_log_side_effect is None else None
    )
    if event_log_side_effect is not None:
        event_log.list_by_session_run_ids_event_types_async = AsyncMock(
            side_effect=event_log_side_effect
        )

    llm_evaluator = MagicMock()
    memory_bank_service = MagicMock()
    return FailureModeClassifier(
        llm_evaluator=llm_evaluator,
        event_log=event_log,
        memory_bank_service=memory_bank_service,
        classifier_version="1.0.0-test",
    )


def _make_sampled_run(
    run_id: str = "run-1",
    session_id: str = "sess-1",
    workspace_id: str = "ws-1",
) -> SampledRun:
    return SampledRun(
        run_id=run_id,
        session_id=session_id,
        workspace_id=workspace_id,
        role_id="role-1",
        status="failed",
        completed_at=datetime.now(timezone.utc),
        event_count=10,
        has_verification_report=False,
    )


def _make_task_spec() -> TaskSpec:
    return TaskSpec(
        summary="Test spec",
        requirements=("r1",),
        constraints=("c1",),
        acceptance_criteria=("a1",),
    )


def _make_verification_report(*, passed: bool = True) -> VerificationReport:
    return VerificationReport(
        task_id="task-1",
        passed=passed,
        checks=(
            VerificationCheckResult(
                layer=VerificationLayer.SPEC,
                name="spec-check",
                passed=passed,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# classify_run tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_run_permission_friction() -> None:
    """Mock approval denied events; verify primary_mode=PERMISSION_FRICTION."""
    events: list[dict[str, object]] = []
    for i in range(12):
        events.append(
            _make_event(
                RunEventType.TOOL_APPROVAL_REQUESTED.value, event_id=f"evt-req-{i}"
            )
        )
    for i in range(10):
        events.append(
            _make_event(
                RunEventType.TOOL_APPROVAL_RESOLVED.value,
                payload={"approved": False, "resolution": "denied"},
                event_id=f"evt-res-{i}",
            )
        )

    classifier = _make_classifier(event_log_events=tuple(events))
    result = await classifier.classify_run(
        run_id="run-1",
        session_id="sess-1",
        workspace_id="ws-1",
    )

    assert result.primary_mode == FailureMode.PERMISSION_FRICTION
    assert result.confidence_score >= 0.7
    assert result.classifier_version == "1.0.0-test"


@pytest.mark.asyncio
async def test_classify_run_tool_sprawl() -> None:
    """Mock many unique TOOL_CALL events; verify tool_sprawl detection."""
    events: list[dict[str, object]] = []
    for i in range(120):
        tool_name = f"tool_{(i % 18)}"
        events.append(
            _make_event(
                RunEventType.TOOL_CALL.value,
                payload={"tool_name": tool_name},
                event_id=f"evt-tc-{i}",
            )
        )

    classifier = _make_classifier(event_log_events=tuple(events))
    result = await classifier.classify_run(
        run_id="run-1",
        session_id="sess-1",
        workspace_id="ws-1",
    )

    assert result.primary_mode == FailureMode.TOOL_SPRAWL
    assert result.confidence_score >= 0.6


@pytest.mark.asyncio
async def test_classify_run_context_rot() -> None:
    """Mock high token usage + compaction events; verify context_rot detection."""
    events: tuple[dict[str, object], ...] = (
        _make_event(
            RunEventType.TOKEN_USAGE.value,
            payload={"total_tokens": 300_000},
            event_id="evt-tok",
        ),
        _make_event(
            RunEventType.SPEC_CHECKPOINT_APPLIED.value,
            payload={},
            event_id="evt-cp1",
        ),
        _make_event(
            RunEventType.SPEC_CHECKPOINT_APPLIED.value,
            payload={},
            event_id="evt-cp2",
        ),
    )

    classifier = _make_classifier(event_log_events=events)
    result = await classifier.classify_run(
        run_id="run-1",
        session_id="sess-1",
        workspace_id="ws-1",
    )

    assert result.primary_mode == FailureMode.CONTEXT_ROT
    assert result.confidence_score >= 0.6


@pytest.mark.asyncio
async def test_classify_run_spec_drift() -> None:
    """Mock evaluate_spec_drift returning drift_detected=True; verify SPEC_DRIFT."""
    drift_result = SpecCheckpointEvaluation(
        evaluation_id="speval-test",
        task_id="task-1",
        artifact_id="art-1",
        session_id="sess-1",
        trace_id="run-1",
        checkpoint_seq=0,
        overall_score=2.0,
        drift_detected=True,
        drift_detail="Requirements not fully covered",
        scores_json="[]",
    )

    classifier = _make_classifier(event_log_events=())
    task_spec = _make_task_spec()

    with patch(
        "relay_teams.agents.execution.spec_drift_evaluator.evaluate_spec_drift",
        new=AsyncMock(return_value=drift_result),
    ):
        result = await classifier.classify_run(
            run_id="run-1",
            session_id="sess-1",
            workspace_id="ws-1",
            task_spec=task_spec,
        )

    assert result.primary_mode == FailureMode.SPEC_DRIFT
    assert result.confidence_score >= 0.7


@pytest.mark.asyncio
async def test_classify_run_verification_miss() -> None:
    """Provide VerificationReport(passed=True) + tool_sprawl signals; verify VERIFICATION_MISS."""
    events: list[dict[str, object]] = []
    for i in range(110):
        events.append(
            _make_event(
                RunEventType.TOOL_CALL.value,
                payload={"tool_name": f"tool_{(i % 16)}"},
                event_id=f"evt-tc-{i}",
            )
        )

    classifier = _make_classifier(event_log_events=tuple(events))
    verification_report = _make_verification_report(passed=True)

    result = await classifier.classify_run(
        run_id="run-1",
        session_id="sess-1",
        workspace_id="ws-1",
        verification_report=verification_report,
    )

    modes_present = {result.primary_mode, *result.secondary_modes}
    assert FailureMode.VERIFICATION_MISS in modes_present
    assert FailureMode.TOOL_SPRAWL in modes_present


@pytest.mark.asyncio
async def test_classify_run_no_signals() -> None:
    """Mock minimal event data; verify classification with primary_mode and low confidence."""
    events: tuple[dict[str, object], ...] = (
        _make_event(
            RunEventType.TOOL_CALL.value, payload={"tool_name": "t1"}, event_id="evt-1"
        ),
    )

    classifier = _make_classifier(event_log_events=events)
    # Mock _classify_with_llm to return None (no LLM result)
    classifier._classify_with_llm = AsyncMock(return_value=None)  # noqa: SLF001

    result = await classifier.classify_run(
        run_id="run-1",
        session_id="sess-1",
        workspace_id="ws-1",
    )

    assert result.primary_mode is not None
    assert result.confidence_score <= 0.5
    assert result.classifier_version == "1.0.0-test"


@pytest.mark.asyncio
async def test_classify_run_missing_optional_inputs() -> None:
    """Call with task_spec=None, verification_report=None; verify no error."""
    classifier = _make_classifier(event_log_events=())
    classifier._classify_with_llm = AsyncMock(return_value=None)  # noqa: SLF001

    result = await classifier.classify_run(
        run_id="run-1",
        session_id="sess-1",
        workspace_id="ws-1",
        task_spec=None,
        verification_report=None,
    )

    assert isinstance(result, FailureModeClassification)
    assert result.run_id == "run-1"


@pytest.mark.asyncio
async def test_classify_run_llm_fallback() -> None:
    """Mock LLM evaluator raising; verify heuristic-only result with confidence <= 0.6."""
    events: tuple[dict[str, object], ...] = (
        _make_event(
            RunEventType.TOOL_CALL.value,
            payload={"tool_name": "tool_x"},
            event_id="evt-tc1",
        ),
    )

    classifier = _make_classifier(event_log_events=events)
    # Simulate no heuristic signals → LLM called → LLM raises
    classifier._classify_with_llm = AsyncMock(side_effect=RuntimeError("LLM down"))  # noqa: SLF001

    result = await classifier.classify_run(
        run_id="run-1",
        session_id="sess-1",
        workspace_id="ws-1",
    )

    # Should fall through to default: context_rot with 0.3 confidence
    assert result.primary_mode == FailureMode.CONTEXT_ROT
    assert result.confidence_score <= 0.6
    assert result.confidence_score == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# classify_batch tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_batch_all_success() -> None:
    """All runs classify successfully; verify classified_count == total_runs."""
    events: tuple[dict[str, object], ...] = ()
    classifier = _make_classifier(event_log_events=events)
    classifier._classify_with_llm = AsyncMock(return_value=None)  # noqa: SLF001

    runs = (
        _make_sampled_run(run_id="run-a"),
        _make_sampled_run(run_id="run-b"),
        _make_sampled_run(run_id="run-c"),
    )

    result = await classifier.classify_batch(sampled_runs=runs)

    assert result.classified_count == 3
    assert result.total_runs == 3
    assert result.skipped_count == 0
    assert len(result.errors) == 0
    assert len(result.classifications) == 3


@pytest.mark.asyncio
async def test_classify_batch_partial_failure() -> None:
    """Some runs raise; verify errors captured and successful classifications still returned."""
    events: tuple[dict[str, object], ...] = ()
    classifier = _make_classifier(event_log_events=events)

    # First run: success
    # Second run: raise
    call_count = 0
    original_classify_run = classifier.classify_run

    async def _flaky_classify_run(**kwargs: Any) -> FailureModeClassification:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("Boom")
        return await original_classify_run(**kwargs)  # noqa: SLF001

    object.__setattr__(classifier, "classify_run", _flaky_classify_run)
    classifier._classify_with_llm = AsyncMock(return_value=None)  # noqa: SLF001

    runs = (
        _make_sampled_run(run_id="run-ok"),
        _make_sampled_run(run_id="run-fail"),
        _make_sampled_run(run_id="run-ok2"),
    )

    result = await classifier.classify_batch(sampled_runs=runs)

    assert result.classified_count == 2
    assert result.total_runs == 3
    assert len(result.errors) == 1
    assert "run-fail" in result.errors[0]


@pytest.mark.asyncio
async def test_classify_batch_empty() -> None:
    """Empty input tuple; verify empty batch result with all counts zero."""
    classifier = _make_classifier(event_log_events=())

    result = await classifier.classify_batch(sampled_runs=())

    assert result.total_runs == 0
    assert result.classified_count == 0
    assert result.skipped_count == 0
    assert len(result.errors) == 0
    assert len(result.classifications) == 0


@pytest.mark.asyncio
async def test_classify_batch_multiple_runs() -> None:
    from relay_teams.agents.evaluation.run_sampling_service import SampledRun
    from datetime import datetime, timezone

    classifier = _make_classifier(event_log_events=())
    runs = tuple(
        SampledRun(
            run_id=f"batch-{i}",
            session_id="ses1",
            workspace_id="ws1",
            role_id=None,
            status="failed",
            completed_at=datetime.now(timezone.utc),
            event_count=1,
            has_verification_report=False,
        )
        for i in range(3)
    )
    result = await classifier.classify_batch(sampled_runs=runs)
    assert result.total_runs == 3
    assert result.classified_count == 3
    assert len(result.errors) == 0
    assert result.batch_id.startswith("fcb-")
