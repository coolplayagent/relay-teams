# -*- coding: utf-8 -*-
from __future__ import annotations


from relay_teams.agents.orchestration.llm_evaluator import (
    _fallback_semantic_result,
)
from relay_teams.agents.tasks.models import (
    SemanticEvaluationRequest,
)


def test_fallback_semantic_result() -> None:
    request = SemanticEvaluationRequest(
        task_id="t1", criterion="test", result_excerpt="output"
    )
    result = _fallback_semantic_result(request)
    assert result.passed is False
    assert result.evaluator == "rule"
    assert result.criterion == "test"
