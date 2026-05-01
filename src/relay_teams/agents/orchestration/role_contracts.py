# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.tasks.enums import TaskStatus, VerificationLayer
from relay_teams.agents.tasks.models import TaskEnvelope, TaskRecord
from relay_teams.agents.tasks.models import VerificationCheckResult
from relay_teams.roles.role_contracts import (
    RoleContractPostconditionType,
    RoleContractPreconditionType,
    is_empty_role_contract,
    role_contract_invariant_failures,
)
from relay_teams.roles.role_models import RoleDefinition


def role_contract_precondition_failures(
    *,
    role: RoleDefinition,
    task: TaskEnvelope,
    records_by_id: dict[str, TaskRecord],
) -> tuple[str, ...]:
    contract = role.contract
    if is_empty_role_contract(contract):
        return ()

    failures: list[str] = list(
        role_contract_invariant_failures(
            contract=contract,
            tools=role.tools,
            mcp_servers=role.mcp_servers,
            skills=role.skills,
        )
    )
    for precondition in contract.preconditions:
        if precondition.condition == RoleContractPreconditionType.TASK_HAS_SPEC:
            if task.spec is None:
                failures.append("task_has_spec: task has no TaskSpec")
        elif (
            precondition.condition
            == RoleContractPreconditionType.TASK_HAS_ACCEPTANCE_CRITERIA
        ):
            if not task.verification.acceptance_criteria:
                failures.append(
                    "task_has_acceptance_criteria: task has no acceptance criteria"
                )
        elif (
            precondition.condition
            == RoleContractPreconditionType.DEPENDENCIES_COMPLETED
        ):
            failures.extend(_dependency_completion_failures(task, records_by_id))
        elif (
            precondition.condition
            == RoleContractPreconditionType.DEPENDENCY_ROLE_COMPLETED
        ):
            failures.extend(
                _dependency_role_completion_failures(
                    task=task,
                    records_by_id=records_by_id,
                    role_ids=precondition.role_ids,
                )
            )
    return tuple(failures)


def role_contract_verification_checks(
    *,
    role: RoleDefinition,
    task: TaskRecord,
    result: str,
) -> tuple[VerificationCheckResult, ...]:
    contract = role.contract
    if is_empty_role_contract(contract):
        return ()

    checks: list[VerificationCheckResult] = []
    checks.extend(_role_contract_invariant_checks(role))
    normalized_result = result.lower()
    for postcondition in contract.postconditions:
        if (
            postcondition.guarantee
            == RoleContractPostconditionType.VERIFICATION_COMMANDS_CONFIGURED
        ):
            passed = bool(task.envelope.verification.command_checks)
            checks.append(
                _contract_check(
                    name="contract_postcondition:verification_commands_configured",
                    passed=passed,
                    details=(
                        "Verification commands are configured."
                        if passed
                        else "Role contract requires verification commands."
                    ),
                )
            )
        elif (
            postcondition.guarantee
            == RoleContractPostconditionType.RESULT_MENTIONS_ACCEPTANCE_CRITERIA
        ):
            checks.extend(
                _result_mentions_checks(
                    label="acceptance",
                    items=task.envelope.verification.acceptance_criteria,
                    normalized_result=normalized_result,
                )
            )
        elif (
            postcondition.guarantee
            == RoleContractPostconditionType.RESULT_MENTIONS_EVIDENCE_EXPECTATIONS
        ):
            checks.extend(
                _result_mentions_checks(
                    label="evidence",
                    items=task.envelope.verification.evidence_expectations,
                    normalized_result=normalized_result,
                )
            )
        elif postcondition.guarantee == RoleContractPostconditionType.HANDOFF_PRESENT:
            passed = task.envelope.handoff is not None
            checks.append(
                _contract_check(
                    name="contract_postcondition:handoff_present",
                    passed=passed,
                    details=(
                        "Task handoff is present."
                        if passed
                        else "Role contract requires a task handoff."
                    ),
                )
            )
    return tuple(checks)


def _role_contract_invariant_checks(
    role: RoleDefinition,
) -> tuple[VerificationCheckResult, ...]:
    failures = role_contract_invariant_failures(
        contract=role.contract,
        tools=role.tools,
        mcp_servers=role.mcp_servers,
        skills=role.skills,
    )
    if not failures:
        return (
            _contract_check(
                name="contract_invariant:role_capabilities",
                passed=True,
                details="Role capability invariants are satisfied.",
            ),
        )
    return tuple(
        _contract_check(
            name=f"contract_invariant:{failure}",
            passed=False,
            details=failure,
        )
        for failure in failures
    )


def _dependency_completion_failures(
    task: TaskEnvelope,
    records_by_id: dict[str, TaskRecord],
) -> tuple[str, ...]:
    failures: list[str] = []
    for dependency_task_id in task.depends_on_task_ids:
        dependency = records_by_id.get(dependency_task_id)
        if dependency is None:
            failures.append(
                f"dependencies_completed: dependency task not found: {dependency_task_id}"
            )
        elif dependency.status != TaskStatus.COMPLETED:
            failures.append(
                "dependencies_completed: dependency task "
                f"{dependency_task_id} is {dependency.status.value}"
            )
    return tuple(failures)


def _dependency_role_completion_failures(
    *,
    task: TaskEnvelope,
    records_by_id: dict[str, TaskRecord],
    role_ids: tuple[str, ...],
) -> tuple[str, ...]:
    if not role_ids:
        return _dependency_completion_failures(task, records_by_id)

    dependencies = tuple(
        records_by_id[dependency_task_id]
        for dependency_task_id in task.depends_on_task_ids
        if dependency_task_id in records_by_id
    )
    failures: list[str] = []
    for role_id in role_ids:
        matching_dependencies = tuple(
            dependency
            for dependency in dependencies
            if dependency.envelope.role_id == role_id
        )
        if not matching_dependencies:
            failures.append(
                f"dependency_role_completed: no dependency task from role {role_id}"
            )
            continue
        incomplete = tuple(
            dependency
            for dependency in matching_dependencies
            if dependency.status != TaskStatus.COMPLETED
        )
        if incomplete:
            statuses = ", ".join(
                f"{dependency.envelope.task_id}:{dependency.status.value}"
                for dependency in incomplete
            )
            failures.append(
                "dependency_role_completed: dependency role "
                f"{role_id} is not complete ({statuses})"
            )
    return tuple(failures)


def _result_mentions_checks(
    *,
    label: str,
    items: tuple[str, ...],
    normalized_result: str,
) -> tuple[VerificationCheckResult, ...]:
    if not items:
        return (
            _contract_check(
                name=f"contract_postcondition:result_mentions_{label}:none",
                passed=True,
                details=f"No {label} items are configured.",
            ),
        )
    return tuple(
        _contract_check(
            name=f"contract_postcondition:result_mentions_{label}:{item}",
            passed=item.lower() in normalized_result,
            details=(
                f"{label.title()} item was cited in the result."
                if item.lower() in normalized_result
                else f"{label.title()} item was not cited in the result."
            ),
        )
        for item in items
    )


def _contract_check(
    *,
    name: str,
    passed: bool,
    details: str,
) -> VerificationCheckResult:
    return VerificationCheckResult(
        layer=VerificationLayer.CONTRACT,
        name=name,
        passed=passed,
        details=details,
    )
