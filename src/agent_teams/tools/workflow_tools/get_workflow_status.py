from __future__ import annotations


from pydantic_ai import Agent

from agent_teams.shared_types.json_types import JsonObject

from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool
from agent_teams.workflow.runtime_graph import load_graph


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def get_workflow_status(ctx: ToolContext, workflow_id: str) -> JsonObject:
        def _action() -> dict[str, object]:
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

            def _get_task_info(task_id: str) -> JsonObject:
                task = records.get(task_id)
                if task is None:
                    return {"status": "missing"}
                info: JsonObject = {"status": task.status.value}
                if task.status.value == "completed" and task.result:
                    info["result"] = task.result
                elif task.status.value in ("failed", "timeout") and task.error_message:
                    info["error"] = task.error_message
                return info

            task_status: dict[str, JsonObject] = {}
            for task_name, task_info in tasks.items():
                task_id = task_info.get("task_id", "")
                role_id = task_info.get("role_id", "")
                status_info = _get_task_info(task_id)
                row: JsonObject = {"status": status_info["status"], "role_id": role_id}
                if "result" in status_info:
                    row["result"] = status_info["result"]
                if "error" in status_info:
                    row["error"] = status_info["error"]
                task_status[task_name] = row

            return {
                "ok": True,
                "workflow_id": workflow_id,
                "workflow_type": graph.get("workflow_type"),
                "objective": graph.get("objective"),
                "task_status": task_status,
            }

        return await execute_tool(
            ctx,
            tool_name="get_workflow_status",
            args_summary={"workflow_id": workflow_id},
            action=_action,
        )
