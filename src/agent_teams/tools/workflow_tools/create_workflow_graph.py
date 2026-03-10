# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.shared_types.json_types import JsonObject
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool
from agent_teams.workflow.constants import CUSTOM_WORKFLOW_ID
from agent_teams.workflow.spec import WorkflowTaskSpec


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def create_workflow_graph(
        ctx: ToolContext,
        objective: str,
        workflow_id: str = CUSTOM_WORKFLOW_ID,
        tasks: list[WorkflowTaskSpec] | None = None,
    ) -> JsonObject:
        def _action() -> dict[str, object]:
            existing_records = ctx.deps.workflow_graph_repo.get_by_run(
                ctx.deps.trace_id
            )
            existing = existing_records[-1].graph if existing_records else None
            if existing is not None:
                return {
                    "ok": True,
                    "created": False,
                    "message": (
                        "A workflow already exists for this task. Use "
                        "dispatch_tasks to continue, or start a new run "
                        "for a fresh workflow."
                    ),
                    "workflow_id": existing.get("workflow_id"),
                    "workflow_name": existing.get("workflow_name"),
                    "existing_tasks": _format_tasks_for_response(existing),
                }

            result = ctx.deps.workflow_service.create_workflow_graph(
                run_id=ctx.deps.trace_id,
                objective=objective,
                workflow_id=workflow_id,
                tasks=tasks,
            )
            ctx.deps.run_runtime_repo.ensure(
                run_id=ctx.deps.run_id,
                session_id=ctx.deps.session_id,
                root_task_id=ctx.deps.task_id,
            )
            ctx.deps.run_runtime_repo.update(
                ctx.deps.run_id,
                active_workflow_id=str(result.get("workflow_id", "")),
            )
            task_list = result.get("tasks", [])
            task_count = len(task_list) if isinstance(task_list, list) else 0
            return {
                **result,
                "message": (
                    f"Workflow created successfully with {task_count} tasks. "
                    f'Use workflow_id="{result.get("workflow_id", "")}" in dispatch_tasks to execute.'
                ),
                "next_action": (
                    'Call dispatch_tasks(action="next") with this workflow_id to start executing tasks.'
                ),
            }

        return await execute_tool(
            ctx,
            tool_name="create_workflow_graph",
            args_summary={
                "workflow_id": workflow_id,
                "objective_len": len(objective),
                "has_tasks": tasks is not None,
                "task_count": len(tasks) if tasks else 0,
            },
            action=_action,
        )


def _format_tasks_for_response(graph: dict[str, object]) -> dict[str, dict[str, str]]:
    tasks = graph.get("tasks", {})
    if not isinstance(tasks, dict):
        return {}
    return {
        name: {
            "task_id": str(info.get("task_id", "")),
            "role_id": str(info.get("role_id", "")),
        }
        for name, info in tasks.items()
        if isinstance(info, dict)
    }
