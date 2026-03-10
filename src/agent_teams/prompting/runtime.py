# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agent_teams.roles.models import RoleDefinition
from agent_teams.workflow.models import TaskEnvelope
from agent_teams.workflow.spec import WorkflowRecommendation

COORDINATOR_ROLE_ID = "coordinator_agent"


class RuntimePromptBuildInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: RoleDefinition
    task: TaskEnvelope
    shared_state_snapshot: tuple[tuple[str, str], ...]
    workflow_recommendation: WorkflowRecommendation | None = None


def build_runtime_system_prompt(data: RuntimePromptBuildInput) -> str:
    sections: list[str] = [f"## Role\n{data.role.system_prompt}"]
    if data.role.role_id == COORDINATOR_ROLE_ID:
        sections.append(
            "## Runtime Contract\n"
            "- A coordinator turn can call tools many times, but delegated tasks run after the turn ends.\n"
            "- Use dispatch_tasks return payloads as the source of truth for progress, task status, and stage outputs.\n"
            "- Prefer workflow tools over raw task-by-task creation."
        )
        recommendation = data.workflow_recommendation
        if recommendation is not None:
            sections.append(_build_workflow_recommendation_section(recommendation))
    sections.append(f"## Task Context\n- TaskRef: {data.task.task_id}")
    shared_state_lines = (
        "\n".join(f"- {key}: {value}" for key, value in data.shared_state_snapshot)
        if data.shared_state_snapshot
        else "- none"
    )
    sections.append(f"## Shared State\n{shared_state_lines}")
    return "\n\n".join(sections)


def _build_workflow_recommendation_section(
    recommendation: WorkflowRecommendation,
) -> str:
    lines = [
        f"- Recommended workflow: {recommendation.workflow_id} ({recommendation.workflow_name})",
        f"- Reason: {recommendation.reason}",
    ]
    if recommendation.matched_hints:
        lines.append(
            "- Matched intent hints: " + ", ".join(recommendation.matched_hints)
        )
    if recommendation.guidance:
        lines.append(f"- Workflow guidance: {recommendation.guidance}")
    lines.append("- Validate the final workflow choice with workflow tools.")
    lines.append("- Do not derive task order from role metadata.")
    return "## Workflow Recommendation\n" + "\n".join(lines)
