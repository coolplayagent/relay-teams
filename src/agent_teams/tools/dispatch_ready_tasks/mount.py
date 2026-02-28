from __future__ import annotations

import json

from pydantic_ai import Agent

from agent_teams.core.enums import InstanceStatus, TaskStatus
from agent_teams.core.models import TaskRecord
from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.tool_helpers import execute_tool
from agent_teams.workflow.runtime_graph import get_ready_tasks, load_graph


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def dispatch_ready_tasks(
        ctx: ToolContext, workflow_id: str, max_dispatch: int = 4
    ) -> str:
        """
        Dispatch and execute ready tasks in a workflow.

        Args:
            workflow_id: The ID of the workflow to dispatch tasks for.
            max_dispatch: Maximum number of tasks to dispatch (default 4, max 8).

        Returns:
            Dispatched tasks, execution results, progress status, and next action recommendation.
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

            tasks = graph.get("tasks", {})
            if not isinstance(tasks, dict):
                raise ValueError("invalid workflow graph tasks")

            records = {
                record.envelope.task_id: record
                for record in ctx.deps.task_repo.list_by_trace(ctx.deps.trace_id)
            }

            bounded_dispatch = max(1, min(int(max_dispatch), 8))

            dispatched: list[dict[str, str]] = []
            executed: list[dict[str, str]] = []
            failed: list[dict[str, str]] = []

            def _refresh_records() -> dict[str, TaskRecord]:
                return {
                    record.envelope.task_id: record
                    for record in ctx.deps.task_repo.list_by_trace(ctx.deps.trace_id)
                }

            def _ensure_and_execute(task_id: str, task_name: str, role_id: str) -> bool:
                nonlocal records
                if len(dispatched) >= bounded_dispatch:
                    return False
                record = records.get(task_id)
                if record is None:
                    return False
                if record.status != TaskStatus.CREATED:
                    return False
                instance = ctx.deps.instance_pool.create_subagent(role_id)
                ctx.deps.agent_repo.upsert_instance(
                    run_id=ctx.deps.run_id,
                    trace_id=ctx.deps.trace_id,
                    session_id=ctx.deps.session_id,
                    instance_id=instance.instance_id,
                    role_id=instance.role_id,
                    status=InstanceStatus.IDLE,
                )
                ctx.deps.task_repo.update_status(
                    task_id=task_id,
                    status=TaskStatus.ASSIGNED,
                    assigned_instance_id=instance.instance_id,
                )
                dispatched.append(
                    {
                        "task_id": task_id,
                        "task_name": task_name,
                        "role_id": role_id,
                        "instance_id": instance.instance_id,
                    }
                )
                try:
                    ctx.deps.task_execution_service.execute(
                        instance_id=instance.instance_id,
                        role_id=instance.role_id,
                        task=record.envelope,
                    )
                    executed.append(
                        {
                            "task_id": task_id,
                            "task_name": task_name,
                            "status": "completed",
                        }
                    )
                except Exception as exc:
                    failed.append(
                        {
                            "task_id": task_id,
                            "task_name": task_name,
                            "status": "failed",
                            "error": str(exc),
                        }
                    )
                records = _refresh_records()
                return True

            ready = get_ready_tasks(graph, records)
            for task_name, task_info in ready:
                task_id = task_info.get("task_id", "")
                role_id = task_info.get("role_id", "")
                if not task_id or not role_id:
                    continue
                _ensure_and_execute(task_id, task_name, role_id)

            all_tasks = list(tasks.keys())
            completed_tasks = [
                name
                for name in all_tasks
                if records.get(tasks[name].get("task_id", ""))
                and records[tasks[name].get("task_id", "")].status
                == TaskStatus.COMPLETED
            ]
            total_tasks = len(all_tasks)
            completed_count = len(completed_tasks)

            if failed:
                converged_stage = "failed"
            elif completed_count == total_tasks:
                converged_stage = "all_completed"
            elif completed_count > 0:
                converged_stage = f"progress_{completed_count}_{total_tasks}"
            else:
                converged_stage = "no_progress"

            return json.dumps(
                {
                    "ok": True,
                    "workflow_id": workflow_id,
                    "dispatched": dispatched,
                    "executed": executed,
                    "failed": failed,
                    "converged_stage": converged_stage,
                    "next_action": _next_action(
                        converged_stage, failed, completed_count, total_tasks
                    ),
                    "remaining_budget": max(0, bounded_dispatch - len(dispatched)),
                    "progress": {"completed": completed_count, "total": total_tasks},
                },
                ensure_ascii=False,
            )

        return execute_tool(
            ctx,
            tool_name="dispatch_ready_tasks",
            args_summary={"workflow_id": workflow_id, "max_dispatch": max_dispatch},
            action=_action,
        )


def _next_action(
    converged_stage: str, failed: list[dict[str, str]], completed: int, total: int
) -> str:
    if failed:
        return "review_failures"
    if converged_stage == "all_completed":
        return "finalize"
    if converged_stage == "no_progress":
        return "check_blocked_tasks"
    if converged_stage.startswith("progress_"):
        return "dispatch_again"
    return "inspect_status"
