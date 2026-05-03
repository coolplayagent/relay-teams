# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from relay_teams.agents.execution.spec_checkpoint import (
    render_spec_checkpoint,
)
from relay_teams.agents.orchestration.llm_semantic_evaluator import (
    LlmSemanticEvaluator,
    _build_semantic_evaluation_prompt,
    _LlmEvaluationOutput,
    _to_semantic_result,
)
from relay_teams.agents.orchestration.multi_model_evaluator import (
    MultiModelSemanticEvaluator,
)
from relay_teams.agents.tasks.enums import (
    EvaluationAggregation,
    TaskSpecStrictness,
    VerificationEvidenceKind,
)
from relay_teams.agents.tasks.models import (
    SemanticEvaluationRequest,
    SemanticEvaluationResult,
    SpecCheckpointPolicy,
    TaskEnvelope,
    TaskSpec,
    VerificationEvidenceItem,
    VerificationPlan,
)


# ---------------------------------------------------------------------------
# FE5-11: LlmSemanticEvaluator tests
# ---------------------------------------------------------------------------


class TestLlmSemanticEvaluator:
    """Tests for the LLM-based semantic evaluator (FE5-11)."""

    def test_prompt_contains_criterion(self) -> None:
        request = SemanticEvaluationRequest(
            task_id="t-1",
            criterion="All API endpoints return 200",
            result_excerpt="All endpoints tested successfully.",
            evidence=(),
        )
        prompt = _build_semantic_evaluation_prompt(request)
        assert "All API endpoints return 200" in prompt
        assert "Result Excerpt" in prompt

    def test_prompt_includes_evidence_items(self) -> None:
        evidence = (
            VerificationEvidenceItem(
                evidence_id="e1",
                kind=VerificationEvidenceKind.COMMAND,
                passed=True,
                summary="Command passed",
                output_excerpt="OK",
            ),
        )
        request = SemanticEvaluationRequest(
            task_id="t-2",
            criterion="Tests pass",
            result_excerpt="",
            evidence=evidence,
        )
        prompt = _build_semantic_evaluation_prompt(request)
        assert "e1" in prompt
        assert "Command passed" in prompt

    def test_to_semantic_result_pass(self) -> None:
        output = _LlmEvaluationOutput(
            verdict="PASS",
            confidence=0.95,
            reason="Clear evidence",
            evidence_ids=("e1",),
        )
        result = _to_semantic_result(output, "Tests pass")
        assert result.passed is True
        assert result.confidence == 0.95
        assert result.evaluator == "llm"
        assert result.criterion == "Tests pass"

    def test_to_semantic_result_fail(self) -> None:
        output = _LlmEvaluationOutput(
            verdict="FAIL",
            confidence=0.8,
            reason="Missing coverage",
            evidence_ids=(),
        )
        result = _to_semantic_result(output, "Full coverage")
        assert result.passed is False

    def test_to_semantic_result_confidence_clamped(self) -> None:
        output = _LlmEvaluationOutput(
            verdict="PASS",
            confidence=1.5,
            reason="ok",
            evidence_ids=(),
        )
        result = _to_semantic_result(output, "criterion")
        assert result.confidence == 1.0

    def test_evaluator_raises_when_no_config(self) -> None:
        def no_config() -> tuple[None, None]:
            return None, None

        evaluator = LlmSemanticEvaluator(resolve_model_config=no_config)
        request = SemanticEvaluationRequest(
            task_id="t-1",
            criterion="test",
            result_excerpt="",
            evidence=(),
        )
        with pytest.raises(RuntimeError, match="could not resolve"):
            evaluator(request)


# ---------------------------------------------------------------------------
# FE5-12: Repeatability tests
# ---------------------------------------------------------------------------


class TestRepeatabilityRuns:
    """Tests for HIGH strictness repeatability (FE5-12)."""

    def test_repeatability_runs_default_is_one(self) -> None:
        plan = VerificationPlan(checklist=("non_empty_response",))
        assert plan.repeatability_runs == 1

    def test_repeatability_runs_set(self) -> None:
        plan = VerificationPlan(
            checklist=("non_empty_response",),
            strictness=TaskSpecStrictness.HIGH,
            repeatability_runs=3,
        )
        assert plan.repeatability_runs == 3

    def test_repeatability_runs_validated(self) -> None:
        with pytest.raises(Exception):
            VerificationPlan(
                checklist=("non_empty_response",),
                repeatability_runs=0,
            )

    def test_repeatability_runs_max(self) -> None:
        with pytest.raises(Exception):
            VerificationPlan(
                checklist=("non_empty_response",),
                repeatability_runs=6,
            )


# ---------------------------------------------------------------------------
# FE5-13: MultiModelSemanticEvaluator tests
# ---------------------------------------------------------------------------


class TestMultiModelSemanticEvaluator:
    """Tests for multi-model aggregation (FE5-13)."""

    def _make_evaluator(
        self, passed: bool, confidence: float, evaluator_name: str = "model-a"
    ) -> MagicMock:
        eval_fn = MagicMock(
            return_value=SemanticEvaluationResult(
                criterion="c",
                passed=passed,
                confidence=confidence,
                reason="ok",
                evaluator=evaluator_name,
            )
        )
        return eval_fn

    def test_single_evaluator_delegates(self) -> None:
        eval_fn = self._make_evaluator(True, 0.9)
        multi = MultiModelSemanticEvaluator(
            evaluators=(eval_fn,),
            aggregation=EvaluationAggregation.MAJORITY,
        )
        request = SemanticEvaluationRequest(
            task_id="t-1", criterion="c", result_excerpt="", evidence=()
        )
        result = multi(request)
        assert result.passed is True
        eval_fn.assert_called_once_with(request)

    def test_majority_passes_with_two_of_three(self) -> None:
        eval_pass = self._make_evaluator(True, 0.9, "a")
        eval_fail = self._make_evaluator(False, 0.3, "b")
        multi = MultiModelSemanticEvaluator(
            evaluators=(eval_pass, eval_fail, eval_pass),
            aggregation=EvaluationAggregation.MAJORITY,
        )
        request = SemanticEvaluationRequest(
            task_id="t-1", criterion="c", result_excerpt="", evidence=()
        )
        result = multi(request)
        assert result.passed is True

    def test_unanimous_fails_with_one_failure(self) -> None:
        eval_pass = self._make_evaluator(True, 0.9, "a")
        eval_fail = self._make_evaluator(False, 0.3, "b")
        multi = MultiModelSemanticEvaluator(
            evaluators=(eval_pass, eval_fail),
            aggregation=EvaluationAggregation.UNANIMOUS,
        )
        request = SemanticEvaluationRequest(
            task_id="t-1", criterion="c", result_excerpt="", evidence=()
        )
        result = multi(request)
        assert result.passed is False

    def test_weighted_passes_by_confidence(self) -> None:
        eval_high = self._make_evaluator(True, 0.95, "a")
        eval_low = self._make_evaluator(False, 0.2, "b")
        multi = MultiModelSemanticEvaluator(
            evaluators=(eval_high, eval_low),
            aggregation=EvaluationAggregation.WEIGHTED,
        )
        request = SemanticEvaluationRequest(
            task_id="t-1", criterion="c", result_excerpt="", evidence=()
        )
        result = multi(request)
        assert result.passed is True

    def test_evaluator_failure_does_not_crash(self) -> None:
        def fail_evaluator(
            request: SemanticEvaluationRequest,
        ) -> SemanticEvaluationResult:
            raise ValueError("model unavailable")

        eval_pass_a = self._make_evaluator(True, 0.9, "a")
        eval_pass_b = self._make_evaluator(True, 0.8, "b")
        multi = MultiModelSemanticEvaluator(
            evaluators=(eval_pass_a, fail_evaluator, eval_pass_b),
            aggregation=EvaluationAggregation.MAJORITY,
        )
        request = SemanticEvaluationRequest(
            task_id="t-1", criterion="c", result_excerpt="", evidence=()
        )
        result = multi(request)
        assert result.passed is True

    def test_empty_evaluators_raises(self) -> None:
        with pytest.raises(ValueError, match="(?i)at least one"):
            MultiModelSemanticEvaluator(
                evaluators=(),
                aggregation=EvaluationAggregation.MAJORITY,
            )

    def test_low_agreement_produces_warning(self) -> None:
        eval_pass = self._make_evaluator(True, 0.6, "a")
        eval_fail = self._make_evaluator(False, 0.5, "b")
        multi = MultiModelSemanticEvaluator(
            evaluators=(eval_pass, eval_fail),
            aggregation=EvaluationAggregation.MAJORITY,
            minimum_agreement=0.8,
        )
        request = SemanticEvaluationRequest(
            task_id="t-1", criterion="c", result_excerpt="", evidence=()
        )
        result = multi(request)
        assert "WARNING" in result.reason


# ---------------------------------------------------------------------------
# FE5-14: SpecCheckpoint REASONS Canvas tests
# ---------------------------------------------------------------------------


class TestSpecCheckpointReasons:
    """Tests for spec checkpoint REASONS canvas (FE5-14)."""

    def test_include_reasons_field_default(self) -> None:
        policy = SpecCheckpointPolicy()
        assert policy.include_reasons is True

    def test_include_reasons_disabled(self) -> None:
        policy = SpecCheckpointPolicy(include_reasons=False)
        assert policy.include_reasons is False

    def test_render_includes_reasons_section(self) -> None:
        policy = SpecCheckpointPolicy(include_reasons=True, max_summary_chars=50_000)
        task = TaskEnvelope(
            task_id="t-1",
            session_id="s-1",
            trace_id="r-1",
            objective="Test objective",
            verification=VerificationPlan(
                checklist=("non_empty_response",),
                acceptance_criteria=("criterion a",),
            ),
            spec=TaskSpec(
                summary="Test task for FE5-14",
                acceptance_criteria=("criterion a",),
            ),
        )
        rendered = render_spec_checkpoint(
            task=task,
            role_id="crafter",
            sequence=1,
            reason="tool_calls>=12",
            policy=policy,
            tool_calls_since_checkpoint=15,
            messages_since_checkpoint=30,
            tokens_since_checkpoint=5000,
        )
        assert "### REASONS" in rendered
        assert "timestamp:" in rendered
        assert "trigger: tool_calls>=12" in rendered
        assert "changed_fields:" in rendered
        assert "reason: Automatic spec checkpoint" in rendered

    def test_render_no_reasons_when_disabled(self) -> None:
        policy = SpecCheckpointPolicy(include_reasons=False)
        task = TaskEnvelope(
            task_id="t-1",
            session_id="s-1",
            trace_id="r-1",
            objective="Test objective",
            verification=VerificationPlan(
                checklist=("non_empty_response",),
                acceptance_criteria=("criterion a",),
            ),
            spec=TaskSpec(summary="Test task"),
        )
        rendered = render_spec_checkpoint(
            task=task,
            role_id="crafter",
            sequence=1,
            reason="tool_calls>=12",
            policy=policy,
            tool_calls_since_checkpoint=15,
            messages_since_checkpoint=30,
            tokens_since_checkpoint=5000,
        )
        assert "### REASONS" not in rendered

    def test_render_no_reasons_when_trigger_empty(self) -> None:
        policy = SpecCheckpointPolicy(include_reasons=True)
        task = TaskEnvelope(
            task_id="t-1",
            session_id="s-1",
            trace_id="r-1",
            objective="Test objective",
            verification=VerificationPlan(
                checklist=("non_empty_response",),
            ),
            spec=TaskSpec(summary="Test task"),
        )
        rendered = render_spec_checkpoint(
            task=task,
            role_id="crafter",
            sequence=1,
            reason="",
            policy=policy,
            tool_calls_since_checkpoint=0,
            messages_since_checkpoint=0,
            tokens_since_checkpoint=0,
        )
        assert "### REASONS" not in rendered
