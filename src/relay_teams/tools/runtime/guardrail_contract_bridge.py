# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.roles.role_contracts import (
    RoleContractInvariantType,
    RoleContractPostconditionType,
)
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.tools.runtime.guardrails import (
    RuntimeGuardrailAction,
    RuntimeGuardrailLayer,
    RuntimeGuardrailRule,
    RuntimeGuardrailRuleType,
    RuntimeGuardrailPolicy,
)

_CONTRACT_RULE_PREFIX = "contract:"

_EXCERPT_CHARS = 200


def derive_guardrail_rules_from_contract(
    role: RoleDefinition,
) -> list[RuntimeGuardrailRule]:
    """Derive guardrail rules from a role's contract constraints.

    Maps contract invariants and postconditions to runtime guardrail
    rules that enforce the declared constraints during execution.
    """
    rules: list[RuntimeGuardrailRule] = []
    contract = role.contract
    role_id = role.role_id

    for invariant in contract.invariants:
        if invariant.invariant == RoleContractInvariantType.MUST_NOT_HAVE_TOOLS:
            for tool_name in invariant.tools:
                rules.append(
                    RuntimeGuardrailRule(
                        rule_id=f"{_CONTRACT_RULE_PREFIX}deny_tool:{role_id}:{tool_name}",
                        layer=RuntimeGuardrailLayer.PRE_EXECUTION,
                        rule_type=RuntimeGuardrailRuleType.TOOL_DENYLIST,
                        action=RuntimeGuardrailAction.DENY,
                        description=(
                            f"Contract invariant forbids tool '{tool_name}' "
                            f"for role '{role_id}'."
                        ),
                        tool_names=(tool_name,),
                    )
                )
        elif invariant.invariant == RoleContractInvariantType.MUST_HAVE_TOOLS:
            for tool_name in invariant.tools:
                rules.append(
                    RuntimeGuardrailRule(
                        rule_id=f"{_CONTRACT_RULE_PREFIX}warn_missing_tool:{role_id}:{tool_name}",
                        layer=RuntimeGuardrailLayer.PRE_EXECUTION,
                        rule_type=RuntimeGuardrailRuleType.CALL_FREQUENCY,
                        action=RuntimeGuardrailAction.WARN,
                        description=(
                            f"Contract invariant requires tool '{tool_name}' "
                            f"for role '{role_id}'."
                        ),
                        tool_names=(tool_name,),
                    )
                )

    for postcondition in contract.postconditions:
        if postcondition.guarantee == (
            RoleContractPostconditionType.RESULT_MENTIONS_ACCEPTANCE_CRITERIA
        ):
            rules.append(
                RuntimeGuardrailRule(
                    rule_id=(
                        f"{_CONTRACT_RULE_PREFIX}output_mentions_criteria:{role_id}"
                    ),
                    layer=RuntimeGuardrailLayer.IN_EXECUTION,
                    rule_type=RuntimeGuardrailRuleType.OUTPUT_SIZE,
                    action=RuntimeGuardrailAction.WARN,
                    description=(
                        "Postcondition requires output to mention acceptance criteria."
                    ),
                )
            )
        elif postcondition.guarantee == (
            RoleContractPostconditionType.RESULT_MENTIONS_EVIDENCE_EXPECTATIONS
        ):
            rules.append(
                RuntimeGuardrailRule(
                    rule_id=(
                        f"{_CONTRACT_RULE_PREFIX}output_mentions_evidence:{role_id}"
                    ),
                    layer=RuntimeGuardrailLayer.IN_EXECUTION,
                    rule_type=RuntimeGuardrailRuleType.OUTPUT_SIZE,
                    action=RuntimeGuardrailAction.WARN,
                    description=(
                        "Postcondition requires output to mention "
                        "evidence expectations."
                    ),
                )
            )

    return rules


def register_contract_rules(
    policy: RuntimeGuardrailPolicy,
    role: RoleDefinition,
) -> RuntimeGuardrailPolicy:
    """Register contract-derived guardrail rules, deduplicating by rule_id.

    Returns a new policy with the contract-derived rules appended
    (excluding duplicates identified by the ``contract:`` prefix).
    """
    new_rules = derive_guardrail_rules_from_contract(role)
    existing_ids = {
        rule.rule_id
        for rule in policy.rules
        if rule.rule_id.startswith(_CONTRACT_RULE_PREFIX)
    }
    unique_new = tuple(rule for rule in new_rules if rule.rule_id not in existing_ids)
    if not unique_new:
        return policy
    return policy.model_copy(update={"rules": policy.rules + unique_new})
