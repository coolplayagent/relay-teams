# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from relay_teams.roles.tool_diet_policy import ToolDietPolicy, ToolDietReport
from relay_teams.roles.tool_diet_validation import suggest_auto_split
from relay_teams.roles.role_models import RoleDefinition


class RoleSplitCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suggested_objective: str
    suggested_tools: tuple[str, ...]
    rationale: str


class TaskSplitSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    original_role_id: str
    original_tool_count: int
    suggested_splits: tuple[RoleSplitCandidate, ...]
    reason: str


_READ_LABEL = "Read-only investigation and information gathering"
_WRITE_LABEL = "File modification and code generation"
_ORCHESTRATION_LABEL = "Workflow orchestration and coordination"
_OTHER_LABEL = "Specialized tool operations"


def suggest_task_split(
    *,
    role: RoleDefinition,
    report: ToolDietReport,
    policy_max_tools: int = 10,
) -> TaskSplitSuggestion | None:
    """Return an advisory split suggestion when the tool diet report has findings.

    Returns ``None`` when the report has no warnings or errors.  Otherwise
    partitions the role tools by category (read/write/orchestration/other)
    and returns one ``RoleSplitCandidate`` per non-empty group.
    """
    if not report.findings:
        return None

    tool_names = tuple(role.tools) if role.tools else ()
    groups = suggest_auto_split(
        policy=ToolDietPolicy(max_tools_per_role=policy_max_tools),
        tool_names=tool_names,
        role_id=role.role_id,
        role_name=role.name,
    )
    if not groups:
        return None

    labels = [_READ_LABEL, _WRITE_LABEL, _ORCHESTRATION_LABEL, _OTHER_LABEL]
    candidates: list[RoleSplitCandidate] = []
    for idx, group in enumerate(groups):
        label = labels[idx] if idx < len(labels) else f"Split group {idx + 1}"
        objective = f"{role.name or role.role_id} -- {label}"
        candidates.append(
            RoleSplitCandidate(
                suggested_objective=objective,
                suggested_tools=tuple(group),
                rationale=f"Contains {len(group)} tools: {label.lower()}",
            )
        )

    warning_codes = [f.code for f in report.findings]
    return TaskSplitSuggestion(
        original_role_id=role.role_id,
        original_tool_count=report.tool_count,
        suggested_splits=tuple(candidates),
        reason=f"Tool diet findings: {', '.join(warning_codes)}",
    )
