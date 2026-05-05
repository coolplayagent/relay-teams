# -*- coding: utf-8 -*-
"""Additional coverage tests for failure_mode_classifier.py missing lines."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.agents.evaluation.failure_mode_classifier import (
    FailureModeClassifier,
)
from relay_teams.agents.evaluation.failure_modes import FailureMode
from relay_teams.agents.orchestration.llm_evaluator import LLMEvaluator
from relay_teams.memory.service import MemoryBankService
from relay_teams.sessions.runs.event_log import EventLog


def _make_classifier(
    *,
    llm_evaluator: LLMEvaluator | None = None,
    event_log: EventLog | None = None,
    memory_bank_service: MemoryBankService | None = None,
    tool_count_threshold: int = 100,
    unique_tool_threshold: int = 15,
    token_usage_threshold: int = 200_000,
    approval_deny_rate_threshold: float = 0.5,
) -> FailureModeClassifier:
    return FailureModeClassifier(
        llm_evaluator=llm_evaluator or MagicMock(spec=LLMEvaluator),
        event_log=event_log or MagicMock(spec=EventLog),
        memory_bank_service=memory_bank_service or MagicMock(spec=MemoryBankService),
        tool_count_threshold=tool_count_threshold,
        unique_tool_threshold=unique_tool_threshold,
        token_usage_threshold=token_usage_threshold,
        approval_deny_rate_threshold=approval_deny_rate_threshold,
    )


def _event(
    event_type: str, payload: object = "{}", event_id: str = "e1"
) -> dict[str, object]:
    return {
        "event_type": event_type,
        "payload_json": json.dumps(payload) if isinstance(payload, dict) else payload,
        "id": event_id,
    }


@pytest.mark.asyncio
async def test_guardrail_blocked_adds_permission_friction() -> None:
    events = [
        _event("runtime_guardrail_report", {"status": "blocked", "blocked_count": 3})
    ]
    event_log = MagicMock()
    event_log.list_by_session_run_ids_event_types_async = AsyncMock(return_value=events)
    clf = _make_classifier(event_log=event_log)
    result = await clf.classify_run(run_id="r1", session_id="s1", workspace_id="w1")
    assert FailureMode.PERMISSION_FRICTION in (
        result.primary_mode,
        *result.secondary_modes,
    )


@pytest.mark.asyncio
async def test_tool_sprawl_high_count_few_unique() -> None:
    events = [
        _event("tool_call", {"tool_name": f"tool_{i % 3}"}, f"e{i}") for i in range(120)
    ]
    event_log = MagicMock()
    event_log.list_by_session_run_ids_event_types_async = AsyncMock(return_value=events)
    clf = _make_classifier(
        event_log=event_log, tool_count_threshold=100, unique_tool_threshold=50
    )
    result = await clf.classify_run(run_id="r2", session_id="s1", workspace_id="w1")
    assert FailureMode.TOOL_SPRAWL in (result.primary_mode, *result.secondary_modes)


@pytest.mark.asyncio
async def test_context_rot_single_signal() -> None:
    events = [_event("token_usage", {"total_tokens": 250_000})]
    event_log = MagicMock()
    event_log.list_by_session_run_ids_event_types_async = AsyncMock(return_value=events)
    clf = _make_classifier(event_log=event_log)
    result = await clf.classify_run(run_id="r3", session_id="s1", workspace_id="w1")
    assert result.primary_mode == FailureMode.CONTEXT_ROT
    assert result.confidence_score <= 0.7


@pytest.mark.asyncio
async def test_spec_drift_with_failed_verification() -> None:
    event_log = MagicMock()
    event_log.list_by_session_run_ids_event_types_async = AsyncMock(return_value=[])
    clf = _make_classifier(event_log=event_log)
    vr = MagicMock()
    vr.passed = False
    result = await clf.classify_run(
        run_id="r4", session_id="s1", workspace_id="w1", verification_report=vr
    )
    assert result.primary_mode == FailureMode.SPEC_DRIFT


@pytest.mark.asyncio
async def test_verification_miss_guardrail_only() -> None:
    events = [
        _event("runtime_guardrail_report", {"status": "blocked", "blocked_count": 3})
    ]
    event_log = MagicMock()
    event_log.list_by_session_run_ids_event_types_async = AsyncMock(return_value=events)
    clf = _make_classifier(event_log=event_log)
    vr = MagicMock()
    vr.passed = True
    result = await clf.classify_run(
        run_id="r5", session_id="s1", workspace_id="w1", verification_report=vr
    )
    assert FailureMode.VERIFICATION_MISS in (
        result.primary_mode,
        *result.secondary_modes,
    )


@pytest.mark.asyncio
async def test_llm_classification_no_heuristic_trigger() -> None:
    events = [_event("tool_call", {"tool_name": "read"}, "e1")]
    event_log = MagicMock()
    event_log.list_by_session_run_ids_event_types_async = AsyncMock(return_value=events)
    llm = MagicMock()
    llm_result = MagicMock()
    llm_result.summary = json.dumps(
        {
            "primary_mode": "tool_sprawl",
            "secondary_modes": ["context_rot"],
            "confidence": 0.6,
            "evidence": "test",
        }
    )
    llm.run_custom_evaluation = AsyncMock(return_value=llm_result)
    clf = _make_classifier(event_log=event_log, llm_evaluator=llm)
    result = await clf.classify_run(run_id="r6", session_id="s1", workspace_id="w1")
    assert result.classifier_version == "1.0.0"


@pytest.mark.asyncio
async def test_llm_classification_json_in_text() -> None:
    events = [_event("tool_call", {"tool_name": "read"}, "e1")]
    event_log = MagicMock()
    event_log.list_by_session_run_ids_event_types_async = AsyncMock(return_value=events)
    llm = MagicMock()
    llm_result = MagicMock()
    llm_result.summary = (
        "Here is my analysis:\n"
        '{"primary_mode": "spec_drift", "secondary_modes": [], "confidence": 0.7, "evidence": "test"}'
    )
    llm.run_custom_evaluation = AsyncMock(return_value=llm_result)
    clf = _make_classifier(event_log=event_log, llm_evaluator=llm)
    result = await clf.classify_run(run_id="r7", session_id="s1", workspace_id="w1")
    assert result.primary_mode == FailureMode.SPEC_DRIFT


@pytest.mark.asyncio
async def test_llm_classification_invalid_mode() -> None:
    events = [_event("tool_call", {"tool_name": "read"}, "e1")]
    event_log = MagicMock()
    event_log.list_by_session_run_ids_event_types_async = AsyncMock(return_value=events)
    llm = MagicMock()
    llm_result = MagicMock()
    llm_result.summary = json.dumps(
        {
            "primary_mode": "unknown_mode",
            "secondary_modes": [],
            "confidence": 0.5,
            "evidence": "test",
        }
    )
    llm.run_custom_evaluation = AsyncMock(return_value=llm_result)
    clf = _make_classifier(event_log=event_log, llm_evaluator=llm)
    result = await clf.classify_run(run_id="r8", session_id="s1", workspace_id="w1")
    assert result.primary_mode == FailureMode.CONTEXT_ROT


@pytest.mark.asyncio
async def test_llm_secondary_modes_parsed() -> None:
    events = [_event("tool_call", {"tool_name": "read"}, "e1")]
    event_log = MagicMock()
    event_log.list_by_session_run_ids_event_types_async = AsyncMock(return_value=events)
    llm = MagicMock()
    llm_result = MagicMock()
    llm_result.summary = json.dumps(
        {
            "primary_mode": "tool_sprawl",
            "secondary_modes": ["permission_friction", "invalid_mode"],
            "confidence": 0.7,
            "evidence": "test",
        }
    )
    llm.run_custom_evaluation = AsyncMock(return_value=llm_result)
    clf = _make_classifier(event_log=event_log, llm_evaluator=llm)
    result = await clf.classify_run(run_id="r9", session_id="s1", workspace_id="w1")
    assert result.primary_mode == FailureMode.TOOL_SPRAWL
    assert FailureMode.PERMISSION_FRICTION in result.secondary_modes


@pytest.mark.asyncio
async def test_llm_no_json_no_match() -> None:
    events = [_event("tool_call", {"tool_name": "read"}, "e1")]
    event_log = MagicMock()
    event_log.list_by_session_run_ids_event_types_async = AsyncMock(return_value=events)
    llm = MagicMock()
    llm_result = MagicMock()
    llm_result.summary = "Just some plain text without any JSON."
    llm.run_custom_evaluation = AsyncMock(return_value=llm_result)
    clf = _make_classifier(event_log=event_log, llm_evaluator=llm)
    result = await clf.classify_run(run_id="r10", session_id="s1", workspace_id="w1")
    assert result.primary_mode == FailureMode.CONTEXT_ROT


@pytest.mark.asyncio
async def test_classify_batch_with_error() -> None:
    event_log = MagicMock()
    call_count = 0
    original_events = [_event("tool_call", {"tool_name": "read"}, "e1")]

    async def mock_events(**kwargs: object) -> list[dict[str, object]]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("DB error")
        return original_events

    event_log.list_by_session_run_ids_event_types_async = mock_events
    clf = _make_classifier(event_log=event_log)

    from relay_teams.agents.evaluation.run_sampling_service import SampledRun
    from datetime import datetime, timezone

    runs = (
        SampledRun(
            run_id="r-fail",
            session_id="s1",
            workspace_id="w1",
            role_id=None,
            status="failed",
            completed_at=datetime.now(timezone.utc),
            event_count=5,
            has_verification_report=False,
        ),
        SampledRun(
            run_id="r-ok",
            session_id="s1",
            workspace_id="w1",
            role_id=None,
            status="failed",
            completed_at=datetime.now(timezone.utc),
            event_count=5,
            has_verification_report=False,
        ),
    )
    batch = await clf.classify_batch(sampled_runs=runs)
    assert batch.classified_count == 1
    assert len(batch.errors) == 1


@pytest.mark.asyncio
async def test_invalid_payload_json_handled() -> None:
    events: list[dict[str, object]] = [
        {
            "event_type": "runtime_guardrail_report",
            "payload_json": "not-valid-json{{{",
            "id": "e-bad",
        },
        {"event_type": "token_usage", "payload_json": "also-bad", "id": "e-bad2"},
    ]
    event_log = MagicMock()
    event_log.list_by_session_run_ids_event_types_async = AsyncMock(return_value=events)
    clf = _make_classifier(event_log=event_log)
    result = await clf.classify_run(
        run_id="r-badjson", session_id="s1", workspace_id="w1"
    )
    assert result.classifier_version == "1.0.0"


@pytest.mark.asyncio
async def test_context_rot_two_signals() -> None:
    events = [
        _event("token_usage", {"total_tokens": 300_000}, "e1"),
        _event("spec_checkpoint_applied", {}, "e2"),
        _event("spec_checkpoint_applied", {}, "e3"),
    ]
    event_log = MagicMock()
    event_log.list_by_session_run_ids_event_types_async = AsyncMock(return_value=events)
    clf = _make_classifier(event_log=event_log)
    result = await clf.classify_run(
        run_id="r-ctxrot", session_id="s1", workspace_id="w1"
    )
    assert result.primary_mode == FailureMode.CONTEXT_ROT
    assert result.confidence_score >= 0.7


@pytest.mark.asyncio
async def test_multiple_modes_simultaneously() -> None:
    events: list[dict[str, object]] = []
    for i in range(12):
        events.append(_event("tool_approval_requested", {}, f"ar-{i}"))
        events.append(
            _event(
                "tool_approval_resolved",
                {"approved": False, "resolution": "denied"},
                f"ad-{i}",
            )
        )
    for i in range(110):
        events.append(_event("tool_call", {"tool_name": f"tool_{i % 20}"}, f"tc-{i}"))
    event_log = MagicMock()
    event_log.list_by_session_run_ids_event_types_async = AsyncMock(return_value=events)
    clf = _make_classifier(event_log=event_log)
    result = await clf.classify_run(
        run_id="r-multi", session_id="s1", workspace_id="w1"
    )
    modes = {result.primary_mode, *result.secondary_modes}
    assert FailureMode.PERMISSION_FRICTION in modes
    assert FailureMode.TOOL_SPRAWL in modes
