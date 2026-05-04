# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import MagicMock

from relay_teams.agents.orchestration.llm_security_evaluator import (
    LLMSecurityEvaluator,
)
from relay_teams.agents.tasks.enums import VerificationLayer
from relay_teams.providers.provider_contracts import LLMProvider
from relay_teams.tools.runtime.guardrails import (
    RuntimeGuardrailReport,
    RuntimeGuardrailStatus,
)


def _make_report(
    *,
    blocked: int = 0,
    warnings: int = 0,
) -> RuntimeGuardrailReport:
    return RuntimeGuardrailReport(
        task_id="task-1",
        status=RuntimeGuardrailStatus.BLOCKED
        if blocked > 0
        else RuntimeGuardrailStatus.PASSED,
        blocked_count=blocked,
        warning_count=warnings,
    )


def test_evaluate_security_no_provider():
    provider = MagicMock(spec=LLMProvider)
    provider.generate.side_effect = RuntimeError("down")
    evaluator = LLMSecurityEvaluator(provider=provider, max_retries=0)
    report = _make_report(blocked=0)
    result = evaluator.evaluate_security(
        task_id="task-1",
        result="hello",
        tool_calls=(),
        guardrail_report=report,
    )
    assert len(result) >= 1
    assert result[0].layer == VerificationLayer.SECURITY
    assert result[0].passed is True


def test_evaluate_security_fallback_with_blocks():
    provider = MagicMock(spec=LLMProvider)
    provider.generate.side_effect = RuntimeError("down")
    evaluator = LLMSecurityEvaluator(provider=provider, max_retries=0)
    report = _make_report(blocked=2)
    result = evaluator.evaluate_security(
        task_id="task-1",
        result="hello",
        tool_calls=(),
        guardrail_report=report,
    )
    assert len(result) == 1
    assert result[0].layer == VerificationLayer.SECURITY
    assert result[0].passed is False


def test_evaluate_security_parse_high_severity():
    provider = MagicMock(spec=LLMProvider)
    provider.generate.return_value = (
        '{"findings": [{"issue":"injection","severity":"high"}]}'
    )
    evaluator = LLMSecurityEvaluator(provider=provider, max_retries=0)
    result = evaluator.evaluate_security(
        task_id="task-1",
        result="some result",
        tool_calls=(),
        guardrail_report=None,
    )
    assert len(result) == 1
    assert result[0].passed is False


def test_evaluate_security_parse_no_high_severity():
    provider = MagicMock(spec=LLMProvider)
    provider.generate.return_value = (
        '{"findings": [{"issue":"minor","severity":"low"}]}'
    )
    evaluator = LLMSecurityEvaluator(provider=provider, max_retries=0)
    result = evaluator.evaluate_security(
        task_id="task-1",
        result="clean result",
        tool_calls=(),
        guardrail_report=None,
    )
    assert len(result) == 1
    assert result[0].passed is True


def test_evaluate_security_parse_malformed():
    provider = MagicMock(spec=LLMProvider)
    provider.generate.return_value = "not json"
    evaluator = LLMSecurityEvaluator(provider=provider, max_retries=0)
    result = evaluator.evaluate_security(
        task_id="task-1",
        result="result",
        tool_calls=(),
        guardrail_report=None,
    )
    assert len(result) == 1
    assert result[0].passed is True
    assert "parse failed" in result[0].details.lower()


def test_evaluate_security_no_guardrail_report():
    provider = MagicMock(spec=LLMProvider)
    provider.generate.side_effect = RuntimeError("down")
    evaluator = LLMSecurityEvaluator(provider=provider, max_retries=0)
    result = evaluator.evaluate_security(
        task_id="task-1",
        result="result",
        tool_calls=(),
        guardrail_report=None,
    )
    assert result == ()


class TestLLMSecurityEvaluatorCoverage:
    """Cover _build_prompt branches."""

    def test_build_prompt_with_tool_calls(self) -> None:
        from relay_teams.agents.orchestration.llm_security_evaluator import (
            LLMSecurityEvaluator,
        )

        prompt = LLMSecurityEvaluator._build_prompt(
            task_id="t-1",
            result="some output",
            tool_calls=({"tool_name": "shell"}, {"tool_name": "read"}),
            guardrail_report=None,
        )
        assert "Tool calls:" in prompt
        assert "- shell" in prompt
        assert "- read" in prompt

    def test_build_prompt_with_guardrail_report(self) -> None:
        from relay_teams.agents.orchestration.llm_security_evaluator import (
            LLMSecurityEvaluator,
        )
        from relay_teams.tools.runtime.guardrails import (
            RuntimeGuardrailReport,
            RuntimeGuardrailStatus,
        )

        report = RuntimeGuardrailReport(
            task_id="t-1",
            status=RuntimeGuardrailStatus.WARNING,
            blocked_count=1,
            warning_count=3,
        )
        prompt = LLMSecurityEvaluator._build_prompt(
            task_id="t-1",
            result="some output",
            tool_calls=(),
            guardrail_report=report,
        )
        assert "warning" in prompt
