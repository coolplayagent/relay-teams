# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.shared_types.json_types import JsonObject
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool
from agent_teams.workflow.constants import CUSTOM_WORKFLOW_ID
from agent_teams.workflow.recommendation_service import WorkflowRecommendationService


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def list_available_workflows(
        ctx: ToolContext,
        objective: str = "",
    ) -> JsonObject:
        def _action() -> JsonObject:
            selector = WorkflowRecommendationService(ctx.deps.workflow_registry)
            decision = selector.recommend(objective)
            recommended = decision.recommendation
            workflows = ctx.deps.workflow_registry.list_workflows()
            return {
                "ok": True,
                "recommended_workflow_id": (
                    recommended.workflow_id
                    if recommended is not None
                    else CUSTOM_WORKFLOW_ID
                ),
                "recommended_reason": (
                    recommended.reason
                    if recommended is not None
                    else _custom_reason(objective)
                ),
                "supports_custom": True,
                "workflows": [
                    {
                        "workflow_id": workflow.workflow_id,
                        "name": workflow.name,
                        "description": workflow.description,
                        "selection_hints": list(workflow.selection_hints),
                        "is_default": workflow.is_default,
                        "tasks": [
                            {
                                "task_name": task.task_name,
                                "role_id": task.role_id,
                                "depends_on": list(task.depends_on),
                            }
                            for task in workflow.tasks
                        ],
                    }
                    for workflow in workflows
                ],
            }

        return await execute_tool(
            ctx,
            tool_name="list_available_workflows",
            args_summary={"objective_len": len(objective)},
            action=_action,
        )


def _custom_reason(objective: str) -> str:
    if not objective.strip():
        return f"No objective provided, defaulting to '{CUSTOM_WORKFLOW_ID}'."
    return (
        "No registered workflow strongly matched the intent, so custom is recommended."
    )
