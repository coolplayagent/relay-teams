# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.workflow.registry import WorkflowRegistry
from agent_teams.workflow.spec import WorkflowDefinition, WorkflowRecommendation


class WorkflowSelectionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(default="")
    recommendation: WorkflowRecommendation | None = None


class WorkflowRecommendationService:
    def __init__(self, workflow_registry: WorkflowRegistry) -> None:
        self._workflow_registry = workflow_registry

    def recommend(self, objective: str) -> WorkflowSelectionDecision:
        workflow = self._workflow_registry.recommend(objective)
        if workflow is None:
            return WorkflowSelectionDecision(objective=objective)

        return WorkflowSelectionDecision(
            objective=objective,
            recommendation=WorkflowRecommendation(
                workflow_id=workflow.workflow_id,
                workflow_name=workflow.name,
                reason=self._build_reason(workflow=workflow, objective=objective),
                matched_hints=self._matched_hints(
                    workflow=workflow,
                    objective=objective,
                ),
                guidance=workflow.guidance,
                is_default=workflow.is_default,
            ),
        )

    def _matched_hints(
        self,
        *,
        workflow: WorkflowDefinition,
        objective: str,
    ) -> tuple[str, ...]:
        normalized_objective = objective.strip().lower()
        if not normalized_objective:
            return ()
        return tuple(
            hint
            for hint in workflow.selection_hints
            if hint.strip() and hint.strip().lower() in normalized_objective
        )

    def _build_reason(
        self,
        *,
        workflow: WorkflowDefinition,
        objective: str,
    ) -> str:
        if not objective.strip():
            return f"No objective provided, defaulting to workflow '{workflow.workflow_id}'."

        matched_hints = self._matched_hints(workflow=workflow, objective=objective)
        if matched_hints:
            hints = ", ".join(matched_hints)
            return f"Intent matched workflow '{workflow.workflow_id}' via selection hints: {hints}."

        if workflow.is_default:
            return f"No workflow-specific hint matched, so default workflow '{workflow.workflow_id}' is recommended."

        return f"Workflow '{workflow.workflow_id}' is the best registered match for the intent."
