# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.tasks.enums import TaskSpecStrictness
from relay_teams.tools.runtime.guardrails import (
    RuntimeGuardrailAction,
    RuntimeGuardrailEvaluation,
    RuntimeGuardrailFinding,
    RuntimeGuardrailLayer,
    RuntimeGuardrailRuleType,
    adjust_evaluation_for_strictness,
    adjust_finding_for_strictness,
)


def _make_finding(
    action: RuntimeGuardrailAction = RuntimeGuardrailAction.WARN,
) -> RuntimeGuardrailFinding:
    return RuntimeGuardrailFinding(
        rule_id="test-rule",
        layer=RuntimeGuardrailLayer.PRE_EXECUTION,
        rule_type=RuntimeGuardrailRuleType.TOOL_DENYLIST,
        action=action,
        message="Test finding",
        details={},
    )


def test_adjust_finding_high_warn_to_deny():
    finding = _make_finding(RuntimeGuardrailAction.WARN)
    adjusted = adjust_finding_for_strictness(finding, TaskSpecStrictness.HIGH)
    assert adjusted.action == RuntimeGuardrailAction.DENY


def test_adjust_finding_high_deny_stays_deny():
    finding = _make_finding(RuntimeGuardrailAction.DENY)
    adjusted = adjust_finding_for_strictness(finding, TaskSpecStrictness.HIGH)
    assert adjusted.action == RuntimeGuardrailAction.DENY


def test_adjust_finding_high_allow_stays_allow():
    finding = _make_finding(RuntimeGuardrailAction.ALLOW)
    adjusted = adjust_finding_for_strictness(finding, TaskSpecStrictness.HIGH)
    assert adjusted.action == RuntimeGuardrailAction.ALLOW


def test_adjust_finding_low_deny_to_warn():
    finding = _make_finding(RuntimeGuardrailAction.DENY)
    adjusted = adjust_finding_for_strictness(finding, TaskSpecStrictness.LOW)
    assert adjusted.action == RuntimeGuardrailAction.WARN


def test_adjust_finding_low_warn_stays_warn():
    finding = _make_finding(RuntimeGuardrailAction.WARN)
    adjusted = adjust_finding_for_strictness(finding, TaskSpecStrictness.LOW)
    assert adjusted.action == RuntimeGuardrailAction.WARN


def test_adjust_finding_medium_unchanged():
    finding = _make_finding(RuntimeGuardrailAction.WARN)
    adjusted = adjust_finding_for_strictness(finding, TaskSpecStrictness.MEDIUM)
    assert adjusted.action == RuntimeGuardrailAction.WARN

    finding_deny = _make_finding(RuntimeGuardrailAction.DENY)
    adjusted_deny = adjust_finding_for_strictness(
        finding_deny, TaskSpecStrictness.MEDIUM
    )
    assert adjusted_deny.action == RuntimeGuardrailAction.DENY


def test_adjust_evaluation_for_strictness():
    evaluation = RuntimeGuardrailEvaluation(
        findings=(
            _make_finding(RuntimeGuardrailAction.WARN),
            _make_finding(RuntimeGuardrailAction.ALLOW),
        )
    )
    adjusted = adjust_evaluation_for_strictness(evaluation, TaskSpecStrictness.HIGH)
    assert len(adjusted.findings) == 2
    assert adjusted.findings[0].action == RuntimeGuardrailAction.DENY
    assert adjusted.findings[1].action == RuntimeGuardrailAction.ALLOW


def test_adjust_evaluation_empty():
    evaluation = RuntimeGuardrailEvaluation(findings=())
    adjusted = adjust_evaluation_for_strictness(evaluation, TaskSpecStrictness.HIGH)
    assert len(adjusted.findings) == 0
