# -*- coding: utf-8 -*-
from __future__ import annotations


from relay_teams.roles.role_contracts import (
    RoleContract,
    RoleContractInvariant,
    RoleContractInvariantType,
    RoleContractPostcondition,
    RoleContractPostconditionType,
)
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.tools.runtime.guardrail_contract_bridge import (
    derive_guardrail_rules_from_contract,
    register_contract_rules,
)
from relay_teams.tools.runtime.guardrails import (
    RuntimeGuardrailAction,
    RuntimeGuardrailLayer,
    RuntimeGuardrailPolicy,
    RuntimeGuardrailRuleType,
)


def _make_role(
    *,
    invariant_tools: tuple[str, ...] = (),
    invariant_type: RoleContractInvariantType = (
        RoleContractInvariantType.MUST_NOT_HAVE_TOOLS
    ),
    postcondition: RoleContractPostconditionType | None = None,
) -> RoleDefinition:
    invariants: list[RoleContractInvariant] = []
    if invariant_tools:
        invariants.append(
            RoleContractInvariant(
                invariant=invariant_type,
                tools=invariant_tools,
            )
        )
    postconditions: list[RoleContractPostcondition] = []
    if postcondition is not None:
        postconditions.append(RoleContractPostcondition(guarantee=postcondition))
    contract = RoleContract(
        invariants=tuple(invariants),
        postconditions=tuple(postconditions),
    )
    return RoleDefinition(
        role_id="test-role",
        name="Test Role",
        description="Test role for unit tests",
        version="1.0",
        system_prompt="You are a test role.",
        contract=contract,
    )


def test_derive_no_constraints():
    role = _make_role()
    rules = derive_guardrail_rules_from_contract(role)
    assert rules == []


def test_derive_deny_tools():
    role = _make_role(
        invariant_tools=("dangerous_tool",),
        invariant_type=RoleContractInvariantType.MUST_NOT_HAVE_TOOLS,
    )
    rules = derive_guardrail_rules_from_contract(role)
    assert len(rules) == 1
    assert rules[0].action == RuntimeGuardrailAction.DENY
    assert rules[0].rule_type == RuntimeGuardrailRuleType.TOOL_DENYLIST
    assert rules[0].layer == RuntimeGuardrailLayer.PRE_EXECUTION
    assert "dangerous_tool" in rules[0].tool_names


def test_derive_required_tools():
    role = _make_role(
        invariant_tools=("required_tool",),
        invariant_type=RoleContractInvariantType.MUST_HAVE_TOOLS,
    )
    rules = derive_guardrail_rules_from_contract(role)
    assert len(rules) == 1
    assert rules[0].action == RuntimeGuardrailAction.WARN


def test_derive_postcondition_criteria():
    role = _make_role(
        postcondition=(
            RoleContractPostconditionType.RESULT_MENTIONS_ACCEPTANCE_CRITERIA
        ),
    )
    rules = derive_guardrail_rules_from_contract(role)
    assert len(rules) == 1
    assert rules[0].layer == RuntimeGuardrailLayer.IN_EXECUTION


def test_derive_postcondition_evidence():
    role = _make_role(
        postcondition=(
            RoleContractPostconditionType.RESULT_MENTIONS_EVIDENCE_EXPECTATIONS
        ),
    )
    rules = derive_guardrail_rules_from_contract(role)
    assert len(rules) == 1
    assert rules[0].layer == RuntimeGuardrailLayer.IN_EXECUTION


def test_register_contract_rules_dedup():
    policy = RuntimeGuardrailPolicy(rules=())
    role = _make_role(
        invariant_tools=("tool_a",),
        invariant_type=RoleContractInvariantType.MUST_NOT_HAVE_TOOLS,
    )
    updated = register_contract_rules(policy, role)
    assert len(updated.rules) == 1

    duplicate = register_contract_rules(updated, role)
    assert len(duplicate.rules) == 1


def test_register_contract_rules_append():
    policy = RuntimeGuardrailPolicy(rules=())
    role1 = _make_role(
        invariant_tools=("tool_a",),
        invariant_type=RoleContractInvariantType.MUST_NOT_HAVE_TOOLS,
    )
    role2 = RoleDefinition(
        role_id="test-role-2",
        name="Test Role 2",
        description="Test role 2 for unit tests",
        version="1.0",
        system_prompt="You are a test role.",
        contract=RoleContract(
            invariants=(
                RoleContractInvariant(
                    invariant=RoleContractInvariantType.MUST_NOT_HAVE_TOOLS,
                    tools=("tool_b",),
                ),
            ),
            postconditions=(),
        ),
    )
    updated = register_contract_rules(policy, role1)
    updated = register_contract_rules(updated, role2)
    assert len(updated.rules) == 2


def test_register_no_rules_for_empty_contract():
    policy = RuntimeGuardrailPolicy(rules=())
    role = _make_role()
    updated = register_contract_rules(policy, role)
    assert len(updated.rules) == 0
