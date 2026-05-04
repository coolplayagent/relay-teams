# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.roles.tool_diet_policy import (
    ToolDietFinding,
    ToolDietPolicy,
    ToolDietReport,
    ToolDietSeverity,
)


def validate_tool_diet(
    *,
    policy: ToolDietPolicy,
    tool_count: int,
    objective: str,
    verification_acceptance_criteria_count: int = 0,
    verification_evidence_expectations_count: int = 0,
    role_id: str = "",
) -> ToolDietReport:
    findings: list[ToolDietFinding] = []
    role_label = role_id or "(unknown)"

    if tool_count > policy.max_tools_per_role:
        findings.append(
            ToolDietFinding(
                code="tool_count_exceeded",
                severity=ToolDietSeverity.WARNING,
                message=(
                    f"Role '{role_label}' has {tool_count} tools, exceeding the "
                    f"maximum of {policy.max_tools_per_role}. Consider splitting "
                    f"into specialized roles."
                ),
                detail={
                    "tool_count": tool_count,
                    "max_tools": policy.max_tools_per_role,
                },
            )
        )
    elif tool_count >= policy.max_tools_warning_threshold:
        findings.append(
            ToolDietFinding(
                code="tool_count_warning",
                severity=ToolDietSeverity.WARNING,
                message=(
                    f"Role '{role_label}' has {tool_count} tools, approaching the "
                    f"maximum of {policy.max_tools_per_role}. Consider reviewing "
                    f"the tool set."
                ),
                detail={
                    "tool_count": tool_count,
                    "max_tools": policy.max_tools_per_role,
                },
            )
        )

    objective_lower = objective.lower()
    for keyword in policy.broad_objective_keywords:
        if keyword in objective_lower:
            findings.append(
                ToolDietFinding(
                    code="objective_too_broad",
                    severity=ToolDietSeverity.WARNING,
                    message=(
                        f"Objective for '{role_label}' may be too broad. "
                        f"Consider splitting into smaller, focused tasks."
                    ),
                    detail={"matched_keyword": keyword},
                )
            )
            break

    objective_len = len(objective)
    if objective_len < policy.min_objective_length:
        findings.append(
            ToolDietFinding(
                code="objective_too_short",
                severity=ToolDietSeverity.WARNING,
                message=(
                    f"Objective for '{role_label}' is very short "
                    f"({objective_len} chars). Consider providing more "
                    f"specific goals."
                ),
                detail={
                    "length": objective_len,
                    "min_length": policy.min_objective_length,
                },
            )
        )

    if objective_len > policy.max_objective_length:
        findings.append(
            ToolDietFinding(
                code="objective_too_long",
                severity=ToolDietSeverity.WARNING,
                message=(
                    f"Objective for '{role_label}' is very long "
                    f"({objective_len} chars). Consider splitting the scope."
                ),
                detail={
                    "length": objective_len,
                    "max_length": policy.max_objective_length,
                },
            )
        )

    verification_count = (
        verification_acceptance_criteria_count
        + verification_evidence_expectations_count
    )
    if verification_count < policy.min_verification_fields:
        findings.append(
            ToolDietFinding(
                code="insufficient_verification",
                severity=ToolDietSeverity.WARNING,
                message=(
                    f"Task has only {verification_count} verification field(s), "
                    f"below the recommended minimum of "
                    f"{policy.min_verification_fields}."
                ),
                detail={
                    "count": verification_count,
                    "min_count": policy.min_verification_fields,
                },
            )
        )

    return ToolDietReport(
        findings=tuple(findings),
        tool_count=tool_count,
        max_tools=policy.max_tools_per_role,
        objective_length=objective_len,
    )


def should_reject(report: ToolDietReport) -> bool:
    return any(f.severity == ToolDietSeverity.ERROR for f in report.findings)


def has_warnings(report: ToolDietReport) -> bool:
    return any(f.severity == ToolDietSeverity.WARNING for f in report.findings)
