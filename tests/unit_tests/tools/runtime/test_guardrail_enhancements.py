# -*- coding: utf-8 -*-
from __future__ import annotations


from relay_teams.agents.tasks.enums import TaskSpecStrictness
from relay_teams.tools.runtime.guardrails import (
    RuntimeGuardrailAction,
    RuntimeGuardrailRule,
    RuntimeGuardrailRuleType,
    guardrail_rules_from_contract,
    merge_contract_rules,
    strictness_augmented_rules,
)


class TestStrictnessAugmentedRules:
    def test_low_returns_base(self) -> None:
        base: tuple[RuntimeGuardrailRule, ...] = ()
        result = strictness_augmented_rules(base, TaskSpecStrictness.LOW)
        assert result == ()

    def test_medium_adds_call_frequency(self) -> None:
        base: tuple[RuntimeGuardrailRule, ...] = ()
        result = strictness_augmented_rules(base, TaskSpecStrictness.MEDIUM)
        rule_ids = [r.rule_id for r in result]
        assert "strictness_call_frequency_medium" in rule_ids

    def test_high_adds_all(self) -> None:
        base: tuple[RuntimeGuardrailRule, ...] = ()
        result = strictness_augmented_rules(base, TaskSpecStrictness.HIGH)
        rule_ids = [r.rule_id for r in result]
        assert "strictness_call_frequency_medium" in rule_ids
        assert "strictness_output_size_high" in rule_ids
        assert "strictness_shell_expanded_high" in rule_ids

    def test_high_output_size_limit(self) -> None:
        result = strictness_augmented_rules((), TaskSpecStrictness.HIGH)
        output_rule = next(
            r for r in result if r.rule_id == "strictness_output_size_high"
        )
        assert output_rule.max_bytes == 256 * 1024

    def test_high_shell_patterns_expanded(self) -> None:
        result = strictness_augmented_rules((), TaskSpecStrictness.HIGH)
        shell_rule = next(
            r for r in result if r.rule_id == "strictness_shell_expanded_high"
        )
        assert len(shell_rule.blocked_patterns) >= 4

    def test_base_rules_preserved(self) -> None:
        base_rule = RuntimeGuardrailRule(
            rule_id="test_base",
            layer="pre_execution",  # type: ignore[arg-type]
            rule_type=RuntimeGuardrailRuleType.TOOL_DENYLIST,
        )
        result = strictness_augmented_rules((base_rule,), TaskSpecStrictness.HIGH)
        ids = [r.rule_id for r in result]
        assert "test_base" in ids


class TestGuardrailRulesFromContract:
    def test_empty_contract(self) -> None:
        rules = guardrail_rules_from_contract("role_1", ())
        assert rules == ()

    def test_denied_tools_produces_denylist(self) -> None:
        invariants = ({"denied_tools": ("shell", "write")},)
        rules = guardrail_rules_from_contract("my_role", invariants)
        assert len(rules) == 1
        assert rules[0].rule_type == RuntimeGuardrailRuleType.TOOL_DENYLIST
        assert rules[0].action == RuntimeGuardrailAction.DENY
        assert rules[0].role_ids == ("my_role",)
        assert set(rules[0].tool_names) == {"shell", "write"}

    def test_allowed_tools_produces_allowlist(self) -> None:
        invariants = ({"allowed_tools": ("read", "grep")},)
        rules = guardrail_rules_from_contract("my_role", invariants)
        assert len(rules) == 1
        assert rules[0].rule_type == RuntimeGuardrailRuleType.TOOL_ALLOWLIST

    def test_mixed_invariants(self) -> None:
        invariants = (
            {"denied_tools": ("shell",)},
            {"allowed_tools": ("read",)},
            {"irrelevant_key": "value"},
        )
        rules = guardrail_rules_from_contract("my_role", invariants)
        assert len(rules) == 2


class TestMergeContractRules:
    def test_deduplication(self) -> None:
        base = (
            RuntimeGuardrailRule(
                rule_id="r1",
                layer="pre_execution",  # type: ignore[arg-type]
                rule_type=RuntimeGuardrailRuleType.TOOL_DENYLIST,
            ),
        )
        contract = (
            RuntimeGuardrailRule(
                rule_id="r1",
                layer="pre_execution",  # type: ignore[arg-type]
                rule_type=RuntimeGuardrailRuleType.TOOL_ALLOWLIST,
                description="Override",
            ),
        )
        result = merge_contract_rules(base, contract)
        assert len(result) == 1
        assert result[0].description == "Override"

    def test_no_overlap(self) -> None:
        base = (
            RuntimeGuardrailRule(
                rule_id="r1",
                layer="pre_execution",  # type: ignore[arg-type]
                rule_type=RuntimeGuardrailRuleType.TOOL_DENYLIST,
            ),
        )
        contract = (
            RuntimeGuardrailRule(
                rule_id="r2",
                layer="pre_execution",  # type: ignore[arg-type]
                rule_type=RuntimeGuardrailRuleType.TOOL_ALLOWLIST,
            ),
        )
        result = merge_contract_rules(base, contract)
        assert len(result) == 2
