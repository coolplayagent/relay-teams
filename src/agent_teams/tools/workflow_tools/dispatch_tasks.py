# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic_ai import Agent

from agent_teams.shared_types.json_types import JsonObject
from agent_teams.agents.enums import InstanceStatus
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool
from agent_teams.workflow.runtime_graph import get_ready_tasks
from agent_teams.workflow.enums import TaskStatus
from agent_teams.workflow.models import TaskRecord
from agent_teams.workflow.status_snapshot import (
    build_task_status_row,
    build_task_status_snapshot,
)

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
            graph_record = ctx.deps.workflow_graph_repo.get(workflow_id)
            if graph_record is None or graph_record.run_id != ctx.deps.trace_id:
                raise KeyError(
                    "workflow_graph not found, call create_workflow_graph first"
                )
            graph = graph_record.graph
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
    executed: list[JsonObject] = []
    failed: list[JsonObject] = []
    selected_tasks = _select_tasks_for_dispatch(
        graph=graph,
        records=records,
        max_dispatch=bounded_dispatch,
        feedback_recorded=False,
    )

    def _refresh_records() -> dict[str, TaskRecord]:
        return _records_by_task_id(ctx=ctx)

    async def _ensure_and_execute(
        task_id: str,
        task_name: str,
        role_id: str,
        planned_instance_id: str,
        feedback_recorded: bool,
    ) -> dict[str, object]:
        nonlocal records
        record = records.get(task_id)
        if record is None:
            failed_entry: JsonObject = {
                "task_name": task_name,
                "task_id": task_id,
                "role_id": role_id,
                "instance_id": planned_instance_id,
                "status": "missing",
                "error": "Task not found during dispatch resume.",
            }
            failed.append(failed_entry)
            return {
                "instance_id": planned_instance_id,
                "feedback_recorded": feedback_recorded,
            }

        instance_id = planned_instance_id or record.assigned_instance_id or ""
        if record.status == TaskStatus.CREATED:
            if not instance_id:
                instance = ctx.deps.instance_pool.create_subagent(
                    role_id,
                    session_id=ctx.deps.session_id,
                )
                instance_id = instance.instance_id
                ctx.deps.agent_repo.upsert_instance(
                    run_id=ctx.deps.run_id,
                    trace_id=ctx.deps.trace_id,
                    session_id=ctx.deps.session_id,
                    instance_id=instance.instance_id,
                    role_id=instance.role_id,
                    workspace_id=instance.workspace_id,
                    conversation_id=instance.conversation_id,
                    status=InstanceStatus.IDLE,
                )
            ctx.deps.task_repo.update_status(
                task_id=task_id,
                status=TaskStatus.ASSIGNED,
                assigned_instance_id=instance_id,
            )
            records = _refresh_records()
            record = records.get(task_id)
        if record is None:
            return {
                "instance_id": instance_id,
                "feedback_recorded": feedback_recorded,
            }
        if not instance_id:
            instance_id = record.assigned_instance_id or ""
        if not instance_id:
            failed_entry = build_task_status_row(
                task_name=task_name,
                task_id=task_id,
                role_id=role_id,
                record=record,
            )
            failed_entry["error"] = "Task has no assigned instance."
            failed.append(failed_entry)
            return {
                "instance_id": "",
                "feedback_recorded": feedback_recorded,
            }
        if feedback.strip() and not feedback_recorded:
            _persist_followup_message(
                ctx=ctx,
                instance_id=instance_id,
                task_id=task_id,
                content=feedback,
            )
            feedback_recorded = True
        if record.status == TaskStatus.COMPLETED:
            executed.append(
                build_task_status_row(
                    task_name=task_name,
                    task_id=task_id,
                    role_id=role_id,
                    record=record,
                )
            )
            return {
                "instance_id": instance_id,
                "feedback_recorded": feedback_recorded,
            }
        if record.status in {TaskStatus.FAILED, TaskStatus.TIMEOUT}:
            failed.append(
                build_task_status_row(
                    task_name=task_name,
                    task_id=task_id,
                    role_id=role_id,
                    record=record,
                )
            )
            return {
                "instance_id": instance_id,
                "feedback_recorded": feedback_recorded,
            }
        dispatched.append(
            {
                "task_id": task_id,
                "task_name": task_name,
                "role_id": role_id,
                "instance_id": instance_id,
            }
        )
        try:
            await ctx.deps.task_execution_service.execute(
                instance_id=instance_id,
                role_id=role_id,
                task=record.envelope,
            )
            records = _refresh_records()
            executed.append(
                build_task_status_row(
                    task_name=task_name,
                    task_id=task_id,
                    role_id=role_id,
                    record=records.get(task_id),
                )
            )
        except Exception as exc:
            records = _refresh_records()
            failed_entry = build_task_status_row(
                task_name=task_name,
                task_id=task_id,
                role_id=role_id,
                record=records.get(task_id),
            )
            failed_entry["error"] = str(exc)
            failed.append(failed_entry)
        return {
            "instance_id": instance_id,
            "feedback_recorded": feedback_recorded,
        }

    for planned in selected_tasks:
        if not isinstance(planned, dict):
            continue
        task_id = str(planned.get("task_id") or "")
        task_name = str(planned.get("task_name") or "")
        role_id = str(planned.get("role_id") or "")
        if not task_id or not task_name or not role_id:
            continue
        await _ensure_and_execute(
            task_id,
            task_name,
            role_id,
            str(planned.get("instance_id") or ""),
            bool(planned.get("feedback_recorded") or False),
        )
    progress = _progress(tasks=tasks, records=records)
    converged_stage = _converged_stage(progress=progress, failed=failed)
    task_status = build_task_status_snapshot(tasks=tasks, records=records)
    return {
        "ok": True,
        "workflow_id": workflow_id,
        "action": "next",
        "dispatched": dispatched,
        "executed": executed,
        "failed": failed,
        "task_status": task_status,
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
    if not str(feedback or "").strip():
        return {
            "ok": False,
            "workflow_id": workflow_id,
            "action": "revise",
            "message": "feedback is required for revise.",
        }
    latest = _latest_revisable_task(tasks=tasks, records=records)
    if latest is None:
        return {
            "ok": False,
            "workflow_id": workflow_id,
            "action": "revise",
            "message": "No completed task available to revise.",
        }
    task_name, task_id = latest
    record = records[task_id]
    task_info = tasks.get(task_name, {})
    instance_id = str(record.assigned_instance_id or "")
    role_id = str(task_info.get("role_id") if isinstance(task_info, dict) else "")
    if not task_name or not task_id or not instance_id:
        return {
            "ok": False,
            "workflow_id": workflow_id,
            "action": "revise",
            "message": "Revision target is incomplete.",
        }
    record = records.get(task_id)
    if record is None:
        return {
            "ok": False,
            "workflow_id": workflow_id,
            "action": "revise",
            "message": "Revision target task not found.",
            "task_id": task_id,
        }
    if not role_id:
        role_id = str(tasks.get(task_name, {}).get("role_id", ""))
    _persist_followup_message(
        ctx=ctx,
        instance_id=instance_id,
        task_id=task_id,
        content=feedback,
    )
    try:
        await ctx.deps.task_execution_service.execute(
            instance_id=instance_id,
            role_id=role_id,
            task=record.envelope,
        )
    except Exception as exc:
        return {
            "ok": False,
            "workflow_id": workflow_id,
            "action": "revise",
            "task_name": task_name,
            "task_id": task_id,
            "instance_id": instance_id,
            "error": str(exc),
        }

    refreshed_records = _records_by_task_id(ctx=ctx)
    progress = _progress(tasks=tasks, records=refreshed_records)
    converged_stage = _converged_stage(progress=progress, failed=[])
    refreshed_record = refreshed_records.get(task_id)
    return {
        "ok": True,
        "workflow_id": workflow_id,
        "action": "revise",
        "task_name": task_name,
        "task_id": task_id,
        "instance_id": instance_id,
        "role_id": role_id,
        "task": build_task_status_row(
            task_name=task_name,
            task_id=task_id,
            role_id=role_id,
            record=refreshed_record,
        ),
        "task_status": build_task_status_snapshot(
            tasks=tasks,
            records=refreshed_records,
        ),
        "message": "Revision completed successfully.",
        "converged_stage": converged_stage,
        "next_action": _next_action(converged_stage, []),
        "progress": progress,
    }


def _select_tasks_for_dispatch(
    *,
    graph: dict[str, object],
    records: dict[str, TaskRecord],
    max_dispatch: int,
    feedback_recorded: bool,
) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    for task_name, task_info in get_ready_tasks(graph, records):
        task_id = task_info.get("task_id")
        role_id = task_info.get("role_id")
        if not isinstance(task_id, str) or not task_id:
            continue
        if not isinstance(role_id, str) or not role_id:
            continue
        if len(selected) >= max_dispatch:
            break
        selected.append(
            {
                "task_id": task_id,
                "task_name": task_name,
                "role_id": role_id,
                "instance_id": "",
                "feedback_recorded": feedback_recorded,
            }
        )
    return selected


def _persist_followup_message(
    *,
    ctx: ToolContext,
    instance_id: str,
    task_id: str,
    content: str,
) -> None:
    record = ctx.deps.agent_repo.get_instance(instance_id)
    ctx.deps.message_repo.append_user_prompt_if_missing(
        session_id=ctx.deps.session_id,
        workspace_id=record.workspace_id,
        conversation_id=record.conversation_id,
        agent_role_id=record.role_id,
        instance_id=instance_id,
        task_id=task_id,
        trace_id=ctx.deps.trace_id,
        content=content,
    )


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


def _latest_revisable_task(
    *,
    tasks: Mapping[str, Mapping[str, object]],
    records: dict[str, TaskRecord],
) -> tuple[str, str] | None:
    ordered_task_names = list(tasks.keys())
    revisable_statuses = {
        TaskStatus.COMPLETED,
        TaskStatus.RUNNING,
        TaskStatus.ASSIGNED,
        TaskStatus.STOPPED,
    }
    for task_name in reversed(ordered_task_names):
        task_info = tasks.get(task_name, {})
        task_id = task_info.get("task_id", "")
        if not isinstance(task_id, str) or not task_id:
            continue
        record = records.get(task_id)
        if record is None:
            continue
        if record.status in revisable_statuses:
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


def _converged_stage(
    *, progress: dict[str, int], failed: Sequence[Mapping[str, object]]
) -> str:
    if failed:
        return "failed"
    completed_count = progress["completed"]
    total_tasks = progress["total"]
    if completed_count == total_tasks:
        return "all_completed"
    if completed_count > 0:
        return f"progress_{completed_count}_{total_tasks}"
    return "no_progress"


def _next_action(converged_stage: str, failed: Sequence[Mapping[str, object]]) -> str:
    if failed:
        return "revise"
    if converged_stage == "all_completed":
        return "finalize"
    if converged_stage == "no_progress":
        return "check_blocked_tasks"
    if converged_stage.startswith("progress_"):
        return "next"
    return "inspect_status"
