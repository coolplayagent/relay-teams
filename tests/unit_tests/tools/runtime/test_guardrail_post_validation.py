# -*- coding: utf-8 -*-
"""Coverage gap tests for guardrails post-validation function (lines 345-368)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from relay_teams.agents.tasks.enums import TaskSpecStrictness
from relay_teams.tools.runtime.guardrails import (
    RuntimeGuardrailAction,
    RuntimeGuardrailContext,
    RuntimeGuardrailEvaluation,
    RuntimeGuardrailFinding,
    RuntimeGuardrailLayer,
    RuntimeGuardrailRuleType,
    evaluate_post_validation_guardrails,
)


def _make_context() -> RuntimeGuardrailContext:
    return RuntimeGuardrailContext(
        run_id="run_cov",
        role_id="role_cov",
        session_id="sess_cov",
        task_id="task_cov",
        instance_id="inst_cov",
        tool_name="test_tool",
        tool_call_id="tc_cov",
    )


def _make_finding(
    action: RuntimeGuardrailAction = RuntimeGuardrailAction.WARN,
) -> RuntimeGuardrailFinding:
    return RuntimeGuardrailFinding(
        rule_id="test_rule",
        rule_type=RuntimeGuardrailRuleType.TOOL_DENYLIST,
        layer=RuntimeGuardrailLayer.PRE_EXECUTION,
        action=action,
        message="test finding",
    )


class TestEvaluatePostValidationGuardrails:
    """Cover lines 345-368."""

    def test_no_matching_rules_returns_empty(self) -> None:
        context = _make_context()
        mock_policy = MagicMock()
        mock_policy.matching_rules.return_value = ()
        result = evaluate_post_validation_guardrails(
            policy=mock_policy,
            context=context,
            result_envelope=MagicMock(),
            tool_input={"arg": "value"},
            strictness=TaskSpecStrictness.MEDIUM,
        )
        assert isinstance(result, RuntimeGuardrailEvaluation)
        assert len(result.findings) == 0

    def test_with_matching_rules_returns_findings(self) -> None:
        context = _make_context()
        finding = _make_finding()
        mock_rule = MagicMock()
        mock_policy = MagicMock()
        mock_policy.matching_rules.return_value = (mock_rule,)

        with patch(
            "relay_teams.tools.runtime.guardrails._evaluate_in_execution_rule",
            return_value=finding,
        ):
            result = evaluate_post_validation_guardrails(
                policy=mock_policy,
                context=context,
                result_envelope=MagicMock(),
                tool_input={"arg": "value"},
                strictness=TaskSpecStrictness.MEDIUM,
            )
            assert isinstance(result, RuntimeGuardrailEvaluation)
            assert len(result.findings) >= 1

    def test_high_strictness_escalates_warn_to_deny(self) -> None:
        context = _make_context()
        finding = _make_finding(RuntimeGuardrailAction.WARN)
        mock_rule = MagicMock()
        mock_policy = MagicMock()
        mock_policy.matching_rules.return_value = (mock_rule,)

        with patch(
            "relay_teams.tools.runtime.guardrails._evaluate_in_execution_rule",
            return_value=finding,
        ):
            result = evaluate_post_validation_guardrails(
                policy=mock_policy,
                context=context,
                result_envelope=MagicMock(),
                tool_input={"arg": "value"},
                strictness=TaskSpecStrictness.HIGH,
            )
            assert len(result.findings) >= 1
            assert all(f.action == RuntimeGuardrailAction.DENY for f in result.findings)

    def test_low_strictness_downgrades_deny_to_warn(self) -> None:
        context = _make_context()
        finding = _make_finding(RuntimeGuardrailAction.DENY)
        mock_rule = MagicMock()
        mock_policy = MagicMock()
        mock_policy.matching_rules.return_value = (mock_rule,)

        with patch(
            "relay_teams.tools.runtime.guardrails._evaluate_in_execution_rule",
            return_value=finding,
        ):
            result = evaluate_post_validation_guardrails(
                policy=mock_policy,
                context=context,
                result_envelope=MagicMock(),
                tool_input={"arg": "value"},
                strictness=TaskSpecStrictness.LOW,
            )
            assert len(result.findings) >= 1
            assert all(f.action == RuntimeGuardrailAction.WARN for f in result.findings)

    def test_none_finding_skipped(self) -> None:
        context = _make_context()
        mock_rule = MagicMock()
        mock_policy = MagicMock()
        mock_policy.matching_rules.return_value = (mock_rule,)

        with patch(
            "relay_teams.tools.runtime.guardrails._evaluate_in_execution_rule",
            return_value=None,
        ):
            result = evaluate_post_validation_guardrails(
                policy=mock_policy,
                context=context,
                result_envelope=MagicMock(),
                tool_input={"arg": "value"},
                strictness=TaskSpecStrictness.MEDIUM,
            )
            assert len(result.findings) == 0
