# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.orchestration.llm_semantic_evaluator import (
    _LlmEvaluationOutput,
    _build_semantic_evaluation_prompt,
    _passed_label,
    _to_semantic_result,
)
from relay_teams.agents.tasks.enums import VerificationEvidenceKind
from relay_teams.agents.tasks.models import (
    SemanticEvaluationRequest,
    VerificationEvidenceItem,
)


def test_passed_label() -> None:
    assert _passed_label(True) == "PASS"
    assert _passed_label(False) == "FAIL"
    assert _passed_label(None) == "N/A"


def test_to_semantic_result_pass() -> None:
    output = _LlmEvaluationOutput(
        verdict="PASS", confidence=0.85, reason="Looks good", evidence_ids=("ev1",)
    )
    result = _to_semantic_result(output, "criterion-a")
    assert result.passed is True
    assert result.confidence == 0.85
    assert result.criterion == "criterion-a"
    assert result.evaluator == "llm"
    assert result.evidence_ids == ("ev1",)


def test_to_semantic_result_fail() -> None:
    output = _LlmEvaluationOutput(
        verdict="FAIL", confidence=0.3, reason="Missing evidence"
    )
    result = _to_semantic_result(output, "criterion-b")
    assert result.passed is False


def test_to_semantic_result_clamps_confidence() -> None:
    high = _LlmEvaluationOutput(verdict="PASS", confidence=1.5, reason="ok")
    assert _to_semantic_result(high, "x").confidence == 1.0
    low = _LlmEvaluationOutput(verdict="PASS", confidence=-0.5, reason="ok")
    assert _to_semantic_result(low, "x").confidence == 0.0


def test_build_semantic_evaluation_prompt_includes_criterion() -> None:
    request = SemanticEvaluationRequest(
        task_id="t1",
        criterion="all tests pass",
        result_excerpt="tests pass",
    )
    prompt = _build_semantic_evaluation_prompt(request)
    assert "all tests pass" in prompt
    assert "Result Excerpt" in prompt


def test_build_semantic_evaluation_prompt_includes_evidence() -> None:
    item = VerificationEvidenceItem(
        evidence_id="ev1",
        kind=VerificationEvidenceKind.TEST_RESULT,
        summary="1 test passed",
        passed=True,
    )
    request = SemanticEvaluationRequest(
        task_id="t1",
        criterion="tests pass",
        result_excerpt="done",
        evidence=(item,),
    )
    prompt = _build_semantic_evaluation_prompt(request)
    assert "[ev1] (PASS)" in prompt
    assert "1 test passed" in prompt


def test_build_semantic_evaluation_prompt_truncates_long_excerpt() -> None:
    request = SemanticEvaluationRequest(
        task_id="t1",
        criterion="c",
        result_excerpt="x" * 3000,
    )
    prompt = _build_semantic_evaluation_prompt(request)
    assert "[truncated]" in prompt
