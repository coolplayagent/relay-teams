from __future__ import annotations

import json

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.tool_helpers import execute_tool
from agent_teams.workflow.runtime_graph import load_graph


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def get_workflow_status(ctx: ToolContext, workflow_id: str) -> str:
        """
        Get the current status of a workflow.

        Args:
            workflow_id: The ID of the workflow to query.

        Returns:
            Workflow status including all task statuses and their assigned roles.
        """

        def _action() -> str:
            graph = load_graph(ctx.deps.shared_store, task_id=ctx.deps.task_id)
            if graph is None:
                raise KeyError(
                    "workflow_graph not found, call create_workflow_graph first"
                )
            if graph.get("workflow_id") != workflow_id:
                raise ValueError(
                    f"workflow_id mismatch: expected {graph.get('workflow_id')}, got {workflow_id}"
                )

            records = {
                record.envelope.task_id: record
                for record in ctx.deps.task_repo.list_by_trace(ctx.deps.trace_id)
            }
            tasks = graph.get("tasks", {})
            if not isinstance(tasks, dict):
                raise ValueError("invalid workflow graph tasks")

            def _status(task_id: str) -> str:
                task = records.get(task_id)
                if task is None:
                    return "missing"
                return task.status.value

            task_status = {}
            for task_name, task_info in tasks.items():
                task_id = task_info.get("task_id", "")
                role_id = task_info.get("role_id", "")
                task_status[task_name] = {
                    "status": _status(task_id),
                    "role_id": role_id,
                }

            return json.dumps(
                {
                    "ok": True,
                    "workflow_id": workflow_id,
                    "workflow_type": graph.get("workflow_type"),
                    "objective": graph.get("objective"),
                    "task_status": task_status,
                },
                ensure_ascii=False,
            )

        return execute_tool(
            ctx,
            tool_name="get_workflow_status",
            args_summary={"workflow_id": workflow_id},
            action=_action,
        )
