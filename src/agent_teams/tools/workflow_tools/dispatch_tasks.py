from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic_ai import Agent

from agent_teams.shared_types.json_types import JsonObject
from agent_teams.agents.enums import InstanceStatus
from agent_teams.runs.enums import InjectionSource
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool
from agent_teams.workflow.runtime_graph import get_ready_tasks, load_graph
from agent_teams.workflow.enums import TaskStatus
from agent_teams.workflow.models import TaskRecord

DispatchAction = Literal["next", "revise"]


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def dispatch_tasks(
        ctx: ToolContext,
        workflow_id: str,
        action: DispatchAction,
        feedback: str = "",
        max_dispatch: int = 1,
    ) -> JsonObject:
        async def _action() -> dict[str, object]:
            graph = load_graph(ctx.deps.shared_store, task_id=ctx.deps.task_id)
            if graph is None:
                raise KeyError(
                    "workflow_graph not found, call create_workflow_graph first"
                )
            if graph.get("workflow_id") != workflow_id:
                raise ValueError(
                    f"workflow_id mismatch: expected {graph.get('workflow_id')}, got {workflow_id}"
                )
            if action == "next":
                return await _dispatch_next(
                    ctx=ctx,
                    workflow_id=workflow_id,
                    graph=graph,
                    feedback=feedback,
                    max_dispatch=max_dispatch,
                )
            return await _dispatch_revise(
                ctx=ctx,
                workflow_id=workflow_id,
                graph=graph,
                feedback=feedback,
            )

        return await execute_tool(
            ctx,
            tool_name="dispatch_tasks",
            args_summary={
                "workflow_id": workflow_id,
                "action": action,
                "feedback_len": len(feedback),
                "max_dispatch": max_dispatch,
            },
            action=_action,
        )


async def _dispatch_next(
    *,
    ctx: ToolContext,
    workflow_id: str,
    graph: dict[str, object],
    feedback: str,
    max_dispatch: int,
) -> dict[str, object]:
    tasks = graph.get("tasks", {})
    if not isinstance(tasks, dict):
        raise ValueError("invalid workflow graph tasks")

    records = _records_by_task_id(ctx=ctx)
    bounded_dispatch = max(1, min(int(max_dispatch), 8))
    dispatched: list[dict[str, str]] = []
    executed: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []

    def _refresh_records() -> dict[str, TaskRecord]:
        return _records_by_task_id(ctx=ctx)

    async def _ensure_and_execute(task_id: str, task_name: str, role_id: str) -> bool:
        nonlocal records
        if len(dispatched) >= bounded_dispatch:
            return False
        record = records.get(task_id)
        if record is None or record.status != TaskStatus.CREATED:
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
        if feedback.strip():
            ctx.deps.injection_manager.enqueue(
                run_id=ctx.deps.run_id,
                recipient_instance_id=instance.instance_id,
                source=InjectionSource.SYSTEM,
                content=f"Coordinator note for this stage: {feedback}",
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
            await ctx.deps.task_execution_service.execute(
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
        task_id = task_info.get("task_id")
        role_id = task_info.get("role_id")
        if not isinstance(task_id, str) or not task_id:
            continue
        if not isinstance(role_id, str) or not role_id:
            continue
        await _ensure_and_execute(task_id, task_name, role_id)

    progress = _progress(tasks=tasks, records=records)
    converged_stage = _converged_stage(progress=progress, failed=failed)
    return {
        "ok": True,
        "workflow_id": workflow_id,
        "action": "next",
        "dispatched": dispatched,
        "executed": executed,
        "failed": failed,
        "converged_stage": converged_stage,
        "next_action": _next_action(converged_stage, failed),
        "remaining_budget": max(0, bounded_dispatch - len(dispatched)),
        "progress": progress,
    }


async def _dispatch_revise(
    *,
    ctx: ToolContext,
    workflow_id: str,
    graph: dict[str, object],
    feedback: str,
) -> dict[str, object]:
    records = _records_by_task_id(ctx=ctx)
    tasks = graph.get("tasks", {})
    if not isinstance(tasks, dict):
        raise ValueError("invalid workflow graph tasks")

    latest = _latest_completed_task(tasks=tasks, records=records)
    if latest is None:
        return {
            "ok": False,
            "workflow_id": workflow_id,
            "action": "revise",
            "message": "No completed task available to revise.",
        }

    task_name, task_id = latest
    record = records[task_id]
    instance_id = record.assigned_instance_id
    if instance_id is None:
        return {
            "ok": False,
            "workflow_id": workflow_id,
            "action": "revise",
            "message": f"Task {task_name} has no assigned instance to revise.",
            "task_id": task_id,
        }

    instance = ctx.deps.instance_pool.get(instance_id)
    if feedback.strip():
        ctx.deps.injection_manager.enqueue(
            run_id=ctx.deps.run_id,
            recipient_instance_id=instance_id,
            source=InjectionSource.USER,
            content=f"Please revise your previous output based on this feedback: {feedback}",
        )
    try:
        await ctx.deps.task_execution_service.execute(
            instance_id=instance.instance_id,
            role_id=instance.role_id,
            task=record.envelope,
        )
    except Exception as exc:
        return {
            "ok": False,
            "workflow_id": workflow_id,
            "action": "revise",
            "task_name": task_name,
            "task_id": task_id,
            "instance_id": instance.instance_id,
            "error": str(exc),
        }

    refreshed_records = _records_by_task_id(ctx=ctx)
    progress = _progress(tasks=tasks, records=refreshed_records)
    return {
        "ok": True,
        "workflow_id": workflow_id,
        "action": "revise",
        "task_name": task_name,
        "task_id": task_id,
        "instance_id": instance.instance_id,
        "message": "Revision completed successfully.",
        "progress": progress,
    }


def _records_by_task_id(ctx: ToolContext) -> dict[str, TaskRecord]:
    return {
        record.envelope.task_id: record
        for record in ctx.deps.task_repo.list_by_trace(ctx.deps.trace_id)
    }


def _latest_completed_task(
    *,
    tasks: Mapping[str, Mapping[str, object]],
    records: dict[str, TaskRecord],
) -> tuple[str, str] | None:
    ordered_task_names = list(tasks.keys())
    for task_name in reversed(ordered_task_names):
        task_info = tasks.get(task_name, {})
        task_id = task_info.get("task_id", "")
        if not isinstance(task_id, str) or not task_id:
            continue
        record = records.get(task_id)
        if record is None:
            continue
        if record.status == TaskStatus.COMPLETED:
            return task_name, task_id
    return None


def _progress(
    *,
    tasks: Mapping[str, Mapping[str, object]],
    records: dict[str, TaskRecord],
) -> dict[str, int]:
    all_tasks = list(tasks.keys())
    completed_tasks: list[str] = []
    for name in all_tasks:
        task_info = tasks.get(name)
        if task_info is None:
            continue
        task_id = task_info.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            continue
        record = records.get(task_id)
        if record is not None and record.status == TaskStatus.COMPLETED:
            completed_tasks.append(name)
    return {"completed": len(completed_tasks), "total": len(all_tasks)}


def _converged_stage(*, progress: dict[str, int], failed: list[dict[str, str]]) -> str:
    if failed:
        return "failed"
    completed_count = progress["completed"]
    total_tasks = progress["total"]
    if completed_count == total_tasks:
        return "all_completed"
    if completed_count > 0:
        return f"progress_{completed_count}_{total_tasks}"
    return "no_progress"


def _next_action(converged_stage: str, failed: list[dict[str, str]]) -> str:
    if failed:
        return "revise"
    if converged_stage == "all_completed":
        return "finalize"
    if converged_stage == "no_progress":
        return "check_blocked_tasks"
    if converged_stage.startswith("progress_"):
        return "next"
    return "inspect_status"
