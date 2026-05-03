# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from relay_teams.agents.orchestration.llm_semantic_evaluator import (
    LlmSemanticEvaluator,
    _LlmEvaluationOutput,
    _build_semantic_evaluation_prompt,
    _passed_label,
    _run_evaluator_streaming,
    _to_semantic_result,
)
from relay_teams.agents.tasks.enums import VerificationEvidenceKind
from relay_teams.agents.tasks.models import (
    SemanticEvaluationRequest,
    VerificationEvidenceItem,
)
from relay_teams.providers.model_config import ModelEndpointConfig


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


def test_build_semantic_evaluation_prompt_includes_output_excerpt() -> None:
    item = VerificationEvidenceItem(
        evidence_id="ev2",
        kind=VerificationEvidenceKind.COMMAND,
        summary="command ran",
        passed=True,
        output_excerpt="stdout: hello world",
    )
    request = SemanticEvaluationRequest(
        task_id="t2",
        criterion="runs correctly",
        result_excerpt="done",
        evidence=(item,),
    )
    prompt = _build_semantic_evaluation_prompt(request)
    assert "Output: stdout: hello world" in prompt


def test_llm_semantic_evaluator_raises_on_missing_config() -> None:
    def resolver() -> tuple[ModelEndpointConfig | None, str | None]:
        return None, None

    evaluator = LlmSemanticEvaluator(resolve_model_config=resolver)
    request = SemanticEvaluationRequest(
        task_id="t1", criterion="test", result_excerpt="output"
    )
    with pytest.raises(RuntimeError, match="could not resolve model configuration"):
        evaluator(request)


def test_run_evaluator_streaming_raises_on_null_result() -> None:
    agent = MagicMock()

    class FakeRun:
        result = None

        async def __aenter__(self) -> FakeRun:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def __aiter__(self) -> object:
            return
            yield

    agent.iter = MagicMock(return_value=FakeRun())

    with pytest.raises(RuntimeError, match="did not produce a result"):
        asyncio.run(_run_evaluator_streaming(agent=agent, prompt="test"))


def test_llm_semantic_evaluator_success_path_from_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import relay_teams.agents.orchestration.llm_semantic_evaluator as mod

    config = ModelEndpointConfig(
        model="test-model",
        base_url="http://localhost:11434/v1",
        api_key="key",
    )

    def resolver() -> tuple[ModelEndpointConfig | None, str | None]:
        return config, "profile-1"

    fake_output = _LlmEvaluationOutput(
        verdict="PASS", confidence=0.95, reason="Looks good"
    )

    async def fake_streaming(**kwargs: object) -> _LlmEvaluationOutput:
        return fake_output

    monkeypatch.setattr(mod, "_run_evaluator_streaming", fake_streaming)

    evaluator = LlmSemanticEvaluator(resolve_model_config=resolver)
    request = SemanticEvaluationRequest(
        task_id="t1", criterion="test criterion", result_excerpt="result text"
    )
    result = evaluator(request)
    assert result.passed is True
    assert result.confidence == 0.95
    assert result.criterion == "test criterion"
    assert result.evaluator == "llm"
