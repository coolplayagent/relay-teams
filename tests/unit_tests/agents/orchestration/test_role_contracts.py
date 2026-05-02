# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.orchestration.role_contracts import (
    role_contract_precondition_failures,
    role_contract_verification_checks,
)
from relay_teams.agents.tasks.enums import TaskStatus, VerificationLayer
from relay_teams.agents.tasks.models import (
    TaskEnvelope,
    TaskHandoff,
    TaskRecord,
    TaskSpec,
    VerificationCheckResult,
    VerificationCommand,
    VerificationPlan,
)
from relay_teams.roles.role_contracts import (
    RoleContract,
    RoleContractInvariant,
    RoleContractInvariantType,
    RoleContractPostcondition,
    RoleContractPostconditionType,
    RoleContractPrecondition,
    RoleContractPreconditionType,
)
from relay_teams.roles.role_models import RoleDefinition


def test_role_contract_preconditions_report_missing_task_and_dependency_state() -> None:
    role = _role(
        contract=RoleContract(
            preconditions=(
                RoleContractPrecondition(
                    condition=RoleContractPreconditionType.TASK_HAS_SPEC
                ),
                RoleContractPrecondition(
                    condition=(
                        RoleContractPreconditionType.TASK_HAS_ACCEPTANCE_CRITERIA
                    )
                ),
                RoleContractPrecondition(
                    condition=RoleContractPreconditionType.DEPENDENCIES_COMPLETED
                ),
                RoleContractPrecondition(
                    condition=RoleContractPreconditionType.DEPENDENCY_ROLE_COMPLETED,
                    role_ids=("Crafter", "Designer"),
                ),
            ),
            invariants=(
                RoleContractInvariant(
                    invariant=RoleContractInvariantType.MUST_HAVE_TOOLS,
                    tools=("edit",),
                ),
            ),
        ),
        tools=("read",),
    )
    task = _task(depends_on_task_ids=("missing", "craft", "review"))
    records_by_id = {
        "craft": TaskRecord(
            envelope=_task(task_id="craft", role_id="Crafter"),
            status=TaskStatus.ASSIGNED,
        ),
        "review": TaskRecord(
            envelope=_task(task_id="review", role_id="Reviewer"),
            status=TaskStatus.COMPLETED,
        ),
    }

    failures = role_contract_precondition_failures(
        role=role,
        task=task,
        records_by_id=records_by_id,
    )

    assert failures == (
        "must_have_tools: missing tools 'edit'",
        "task_has_spec: task has no TaskSpec",
        "task_has_acceptance_criteria: task has no acceptance criteria",
        "dependencies_completed: dependency task not found: missing",
        "dependencies_completed: dependency task craft is assigned",
        "dependency_role_completed: dependency role Crafter is not complete "
        "(craft:assigned)",
        "dependency_role_completed: no dependency task from role Designer",
    )


def test_role_contract_preconditions_without_role_filter_use_dependencies() -> None:
    role = _role(
        contract=RoleContract(
            preconditions=(
                RoleContractPrecondition(
                    condition=RoleContractPreconditionType.DEPENDENCY_ROLE_COMPLETED
                ),
            )
        )
    )
    task = _task(depends_on_task_ids=("draft",))
    records_by_id = {
        "draft": TaskRecord(
            envelope=_task(task_id="draft", role_id="Designer"),
            status=TaskStatus.CREATED,
        )
    }

    assert role_contract_precondition_failures(
        role=role,
        task=task,
        records_by_id=records_by_id,
    ) == ("dependencies_completed: dependency task draft is created",)
    assert (
        role_contract_precondition_failures(
            role=_role(contract=RoleContract()),
            task=task,
            records_by_id=records_by_id,
        )
        == ()
    )


def test_role_contract_verification_checks_cover_postconditions_and_invariants() -> (
    None
):
    role = _role(
        contract=RoleContract(
            postconditions=(
                RoleContractPostcondition(
                    guarantee=(
                        RoleContractPostconditionType.VERIFICATION_COMMANDS_CONFIGURED
                    )
                ),
                RoleContractPostcondition(
                    guarantee=(
                        RoleContractPostconditionType.RESULT_MENTIONS_ACCEPTANCE_CRITERIA
                    )
                ),
                RoleContractPostcondition(
                    guarantee=(
                        RoleContractPostconditionType.RESULT_MENTIONS_EVIDENCE_EXPECTATIONS
                    )
                ),
                RoleContractPostcondition(
                    guarantee=RoleContractPostconditionType.HANDOFF_PRESENT
                ),
            ),
            invariants=(
                RoleContractInvariant(
                    invariant=RoleContractInvariantType.MUST_HAVE_TOOLS,
                    tools=("read",),
                ),
            ),
        ),
        tools=("read",),
    )
    record = TaskRecord(
        envelope=_task(
            verification=VerificationPlan(
                acceptance_criteria=("all tests pass",),
                evidence_expectations=("pytest output",),
            )
        ),
        status=TaskStatus.COMPLETED,
    )

    checks = role_contract_verification_checks(
        role=role,
        task=record,
        result="All tests pass.",
    )

    assert {check.layer for check in checks} == {VerificationLayer.CONTRACT}
    assert _check(checks, "contract_invariant:role_capabilities").passed is True
    assert (
        _check(checks, "contract_postcondition:verification_commands_configured").passed
        is False
    )
    assert (
        _check(
            checks,
            "contract_postcondition:result_mentions_acceptance:all tests pass",
        ).passed
        is True
    )
    assert (
        _check(
            checks,
            "contract_postcondition:result_mentions_evidence:pytest output",
        ).passed
        is False
    )
    assert _check(checks, "contract_postcondition:handoff_present").passed is False


def test_role_contract_verification_checks_cover_empty_lists_and_failures() -> None:
    role = _role(
        contract=RoleContract(
            postconditions=(
                RoleContractPostcondition(
                    guarantee=(
                        RoleContractPostconditionType.VERIFICATION_COMMANDS_CONFIGURED
                    )
                ),
                RoleContractPostcondition(
                    guarantee=(
                        RoleContractPostconditionType.RESULT_MENTIONS_ACCEPTANCE_CRITERIA
                    )
                ),
                RoleContractPostcondition(
                    guarantee=RoleContractPostconditionType.HANDOFF_PRESENT
                ),
            ),
            invariants=(
                RoleContractInvariant(
                    invariant=RoleContractInvariantType.MUST_NOT_HAVE_SKILLS,
                    skills=("network",),
                ),
            ),
        ),
        skills=("network",),
    )
    record = TaskRecord(
        envelope=_task(
            verification=VerificationPlan(
                command_checks=(VerificationCommand(command=("pytest",)),)
            ),
            handoff=TaskHandoff(completed=("done",)),
        ),
        status=TaskStatus.COMPLETED,
    )

    checks = role_contract_verification_checks(role=role, task=record, result="")

    assert (
        _check(
            checks,
            "contract_invariant:must_not_have_skills: forbidden skills "
            "'network' is present",
        ).passed
        is False
    )
    assert (
        _check(checks, "contract_postcondition:verification_commands_configured").passed
        is True
    )
    assert (
        _check(checks, "contract_postcondition:result_mentions_acceptance:none").passed
        is True
    )
    assert _check(checks, "contract_postcondition:handoff_present").passed is True
    assert (
        role_contract_verification_checks(
            role=_role(contract=RoleContract()),
            task=record,
            result="",
        )
        == ()
    )


def test_result_mentions_evidence_semantic_pattern_issue_url() -> None:
    """Evidence expectations containing 'issue' should match GitHub issue URLs."""
    role = _role(
        contract=RoleContract(
            postconditions=(
                RoleContractPostcondition(
                    guarantee=(
                        RoleContractPostconditionType.RESULT_MENTIONS_EVIDENCE_EXPECTATIONS
                    )
                ),
            ),
        ),
    )
    record = TaskRecord(
        envelope=_task(
            verification=VerificationPlan(
                evidence_expectations=("issue number",),
            )
        ),
        status=TaskStatus.COMPLETED,
    )
    checks = role_contract_verification_checks(
        role=role,
        task=record,
        result="Fixed in https://github.com/org/repo/issues/656",
    )
    assert _check(
        checks, "contract_postcondition:result_mentions_evidence:issue number"
    ).passed


def test_result_mentions_evidence_semantic_pattern_issue_hash() -> None:
    """Evidence expectations containing 'issue' should match #123 references."""
    role = _role(
        contract=RoleContract(
            postconditions=(
                RoleContractPostcondition(
                    guarantee=(
                        RoleContractPostconditionType.RESULT_MENTIONS_EVIDENCE_EXPECTATIONS
                    )
                ),
            ),
        ),
    )
    record = TaskRecord(
        envelope=_task(
            verification=VerificationPlan(
                evidence_expectations=("issue number",),
            )
        ),
        status=TaskStatus.COMPLETED,
    )
    checks = role_contract_verification_checks(
        role=role,
        task=record,
        result="Fixed issue, see #123",
    )
    assert _check(
        checks, "contract_postcondition:result_mentions_evidence:issue number"
    ).passed


def test_result_mentions_evidence_semantic_pattern_pr_url() -> None:
    """Evidence expectations containing 'pr' should match GitHub PR URLs."""
    role = _role(
        contract=RoleContract(
            postconditions=(
                RoleContractPostcondition(
                    guarantee=(
                        RoleContractPostconditionType.RESULT_MENTIONS_EVIDENCE_EXPECTATIONS
                    )
                ),
            ),
        ),
    )
    record = TaskRecord(
        envelope=_task(
            verification=VerificationPlan(
                evidence_expectations=("PR URL",),
            )
        ),
        status=TaskStatus.COMPLETED,
    )
    checks = role_contract_verification_checks(
        role=role,
        task=record,
        result="Created https://github.com/org/repo/pull/658",
    )
    assert _check(
        checks, "contract_postcondition:result_mentions_evidence:PR URL"
    ).passed


def test_result_mentions_evidence_semantic_pattern_url() -> None:
    """Evidence expectations containing 'url' should match any HTTP URL."""
    role = _role(
        contract=RoleContract(
            postconditions=(
                RoleContractPostcondition(
                    guarantee=(
                        RoleContractPostconditionType.RESULT_MENTIONS_EVIDENCE_EXPECTATIONS
                    )
                ),
            ),
        ),
    )
    record = TaskRecord(
        envelope=_task(
            verification=VerificationPlan(
                evidence_expectations=("download url",),
            )
        ),
        status=TaskStatus.COMPLETED,
    )
    checks = role_contract_verification_checks(
        role=role,
        task=record,
        result="Available at https://example.com/file.zip",
    )
    assert _check(
        checks, "contract_postcondition:result_mentions_evidence:download url"
    ).passed


def test_result_mentions_evidence_semantic_pattern_file_path() -> None:
    """Evidence expectations containing 'file' should match path patterns."""
    role = _role(
        contract=RoleContract(
            postconditions=(
                RoleContractPostcondition(
                    guarantee=(
                        RoleContractPostconditionType.RESULT_MENTIONS_EVIDENCE_EXPECTATIONS
                    )
                ),
            ),
        ),
    )
    record = TaskRecord(
        envelope=_task(
            verification=VerificationPlan(
                evidence_expectations=("file list",),
            )
        ),
        status=TaskStatus.COMPLETED,
    )
    checks = role_contract_verification_checks(
        role=role,
        task=record,
        result="Modified src/relay_teams/main.py",
    )
    assert _check(
        checks, "contract_postcondition:result_mentions_evidence:file list"
    ).passed


def test_result_mentions_evidence_semantic_fallback_still_literal() -> None:
    """Unrecognized expectations still use literal matching (backwards compat)."""
    role = _role(
        contract=RoleContract(
            postconditions=(
                RoleContractPostcondition(
                    guarantee=(
                        RoleContractPostconditionType.RESULT_MENTIONS_EVIDENCE_EXPECTATIONS
                    )
                ),
            ),
        ),
    )
    record = TaskRecord(
        envelope=_task(
            verification=VerificationPlan(
                evidence_expectations=("pytest output",),
            )
        ),
        status=TaskStatus.COMPLETED,
    )
    checks = role_contract_verification_checks(
        role=role,
        task=record,
        result="All tests pass.",
    )
    assert not _check(
        checks, "contract_postcondition:result_mentions_evidence:pytest output"
    ).passed


def _task(
    *,
    task_id: str = "task-1",
    role_id: str = "Crafter",
    verification: VerificationPlan | None = None,
    spec: TaskSpec | None = None,
    handoff: TaskHandoff | None = None,
    depends_on_task_ids: tuple[str, ...] = (),
) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=task_id,
        session_id="session-1",
        trace_id="run-1",
        role_id=role_id,
        objective="Do the work.",
        verification=verification or VerificationPlan(),
        spec=spec,
        handoff=handoff,
        depends_on_task_ids=depends_on_task_ids,
    )


def _role(
    *,
    contract: RoleContract,
    tools: tuple[str, ...] = (),
    mcp_servers: tuple[str, ...] = (),
    skills: tuple[str, ...] = (),
) -> RoleDefinition:
    return RoleDefinition(
        role_id="Crafter",
        name="Crafter",
        description="Crafts work.",
        version="1.0.0",
        tools=tools,
        mcp_servers=mcp_servers,
        skills=skills,
        contract=contract,
        system_prompt="Craft carefully.",
    )


def _check(
    checks: tuple[VerificationCheckResult, ...],
    name: str,
) -> VerificationCheckResult:
    return next(check for check in checks if check.name == name)
