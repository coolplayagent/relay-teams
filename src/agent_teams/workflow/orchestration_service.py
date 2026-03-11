# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import uuid4


from agent_teams.shared_types.json_types import JsonObject
from agent_teams.agents.enums import InstanceStatus
from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.coordination.task_execution_service import TaskExecutionService
from agent_teams.roles.registry import RoleRegistry
from agent_teams.runs.injection_queue import RunInjectionManager
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.shared_state_repo import SharedStateRepository
from agent_teams.state.task_repo import TaskRepository
from agent_teams.state.workflow_graph_repo import WorkflowGraphRepository
from agent_teams.workflow.enums import TaskStatus
from agent_teams.workflow.models import TaskEnvelope, TaskRecord, VerificationPlan
from agent_teams.workflow.registry import WorkflowRegistry
from agent_teams.workflow.constants import CUSTOM_WORKFLOW_ID
from agent_teams.workflow.runtime_graph import get_ready_tasks
from agent_teams.workflow.spec import WorkflowTaskSpec
from agent_teams.workflow.status_snapshot import (
    build_task_status_row,
    build_task_status_snapshot,
)

DispatchAction = str
WorkflowTaskSpecInput = WorkflowTaskSpec


class WorkflowOrchestrationService:
    def __init__(
        self,
        *,
        task_repo: TaskRepository,
        shared_store: SharedStateRepository,
        workflow_graph_repo: WorkflowGraphRepository,
        role_registry: RoleRegistry,
        workflow_registry: WorkflowRegistry,
        instance_pool: InstancePool,
        agent_repo: AgentInstanceRepository,
        task_execution_service: TaskExecutionService,
        injection_manager: RunInjectionManager,
        message_repo: MessageRepository,
    ) -> None:
        self._task_repo: TaskRepository = task_repo
        self._shared_store: SharedStateRepository = shared_store
        self._workflow_graph_repo: WorkflowGraphRepository = workflow_graph_repo
        self._role_registry: RoleRegistry = role_registry
        self._workflow_registry: WorkflowRegistry = workflow_registry
        self._instance_pool: InstancePool = instance_pool
        self._agent_repo: AgentInstanceRepository = agent_repo
        self._task_execution_service: TaskExecutionService = task_execution_service
        self._injection_manager: RunInjectionManager = injection_manager
        self._message_repo: MessageRepository = message_repo

    def create_workflow_graph(
        self,
        *,
        run_id: str,
        objective: str,
        workflow_id: str = CUSTOM_WORKFLOW_ID,
        tasks: list[WorkflowTaskSpecInput] | None = None,
    ) -> dict[str, object]:
        root = self._get_root_task(run_id=run_id)
        existing_records = self._workflow_graph_repo.get_by_run(run_id)
        existing = existing_records[-1].graph if existing_records else None
        if existing is not None:
            return {
                "ok": True,
                "created": False,
                "message": (
                    "A workflow already exists for this run. Use dispatch_tasks to continue, "
                    "or start a new run for a fresh workflow."
                ),
                "workflow_id": existing.get("workflow_id"),
                "workflow_name": existing.get("workflow_name"),
            }

        workflow_name = "Custom Workflow"
        parsed_tasks = list(tasks or [])
        if workflow_id == CUSTOM_WORKFLOW_ID:
            if not parsed_tasks:
                raise ValueError(
                    "tasks is required for custom workflow. "
                    + 'Example: [{"task_name": "code", "objective": "Write hello.py", '
                    + '"role_id": "spec_coder", "depends_on": []}]'
                )
        else:
            definition = self._workflow_registry.get(workflow_id)
            workflow_name = definition.name
            parsed_tasks = list(definition.instantiate(objective=objective))

        _validate_roles_exist(self._role_registry, parsed_tasks)
        _validate_task_dependencies(parsed_tasks)
        _detect_cycle(parsed_tasks)
        workflow_run_id = f"workflow_{uuid4().hex[:8]}"

        name_to_task_id: dict[str, str] = {}
        for spec in parsed_tasks:
            name_to_task_id[spec.task_name] = f"task_{uuid4().hex[:12]}"

        for spec in parsed_tasks:
            _ = self._task_repo.create(
                TaskEnvelope(
                    task_id=name_to_task_id[spec.task_name],
                    session_id=root.envelope.session_id,
                    trace_id=root.envelope.trace_id,
                    parent_task_id=root.envelope.task_id,
                    objective=spec.objective,
                    verification=VerificationPlan(checklist=("non_empty_response",)),
                )
            )

        graph: dict[str, object] = {
            "workflow_id": workflow_run_id,
            "workflow_template_id": workflow_id,
            "workflow_name": workflow_name,
            "objective": objective,
            "trace_id": root.envelope.trace_id,
            "session_id": root.envelope.session_id,
            "tasks": {
                spec.task_name: {
                    "task_id": name_to_task_id[spec.task_name],
                    "role_id": spec.role_id,
                    "depends_on": list(spec.depends_on),
                }
                for spec in parsed_tasks
            },
        }
        self._workflow_graph_repo.upsert(
            workflow_id=workflow_run_id,
            run_id=run_id,
            session_id=root.envelope.session_id,
            root_task_id=root.envelope.task_id,
            graph=graph,
        )

        return {
            "ok": True,
            "created": True,
            "workflow_id": workflow_run_id,
            "workflow_template_id": workflow_id,
            "workflow_name": workflow_name,
            "tasks": [
                {
                    "task_name": spec.task_name,
                    "task_id": name_to_task_id[spec.task_name],
                    "role_id": spec.role_id,
                    "depends_on": list(spec.depends_on),
                }
                for spec in parsed_tasks
            ],
        }

    def get_workflow_status(
        self, *, run_id: str, workflow_id: str
    ) -> dict[str, object]:
        graph_record = self._workflow_graph_repo.get(workflow_id)
        graph = graph_record.graph if graph_record is not None else None
        if graph is None:
            raise KeyError("workflow_graph not found, call create_workflow_graph first")
        if graph.get("workflow_id") != workflow_id:
            raise ValueError(
                f"workflow_id mismatch: expected {graph.get('workflow_id')}, got {workflow_id}"
            )

        records = {
            record.envelope.task_id: record
            for record in self._task_repo.list_by_trace(run_id)
        }
        tasks = graph.get("tasks", {})
        if not isinstance(tasks, dict):
            raise ValueError("invalid workflow graph tasks")

        task_status = build_task_status_snapshot(tasks=tasks, records=records)

        return {
            "ok": True,
            "workflow_id": workflow_id,
            "workflow_template_id": graph.get("workflow_template_id"),
            "workflow_name": graph.get("workflow_name"),
            "objective": graph.get("objective"),
            "task_status": task_status,
        }

    async def dispatch_tasks(
        self,
        *,
        run_id: str,
        workflow_id: str,
        action: DispatchAction,
        feedback: str = "",
        max_dispatch: int = 1,
    ) -> dict[str, object]:
        graph_record = self._workflow_graph_repo.get(workflow_id)
        graph = graph_record.graph if graph_record is not None else None
        if graph is None:
            raise KeyError("workflow_graph not found, call create_workflow_graph first")
        if graph.get("workflow_id") != workflow_id:
            raise ValueError(
                f"workflow_id mismatch: expected {graph.get('workflow_id')}, got {workflow_id}"
            )
        if action == "next":
            return await self._dispatch_next(
                run_id=run_id,
                workflow_id=workflow_id,
                graph=graph,
                feedback=feedback,
                max_dispatch=max_dispatch,
            )
        return await self._dispatch_revise(
            run_id=run_id,
            workflow_id=workflow_id,
            graph=graph,
            feedback=feedback,
        )

    def _get_root_task(self, *, run_id: str) -> TaskRecord:
        records = self._task_repo.list_by_trace(run_id)
        for record in records:
            if record.envelope.parent_task_id is None:
                return record
        raise KeyError(f"No root task found for run_id={run_id}")

    async def _dispatch_next(
        self,
        *,
        run_id: str,
        workflow_id: str,
        graph: dict[str, object],
        feedback: str,
        max_dispatch: int,
    ) -> dict[str, object]:
        tasks = graph.get("tasks", {})
        if not isinstance(tasks, dict):
            raise ValueError("invalid workflow graph tasks")

        records = _records_by_task_id(self._task_repo.list_by_trace(run_id))
        bounded_dispatch = max(1, min(int(max_dispatch), 8))
        dispatched: list[dict[str, str]] = []
        executed: list[JsonObject] = []
        failed: list[JsonObject] = []

        async def _ensure_and_execute(
            task_id: str, task_name: str, role_id: str
        ) -> None:
            nonlocal records
            if len(dispatched) >= bounded_dispatch:
                return
            record = records.get(task_id)
            if record is None or record.status != TaskStatus.CREATED:
                return
            instance = self._instance_pool.create_subagent(
                role_id,
                session_id=record.envelope.session_id,
            )
            self._agent_repo.upsert_instance(
                run_id=run_id,
                trace_id=run_id,
                session_id=record.envelope.session_id,
                instance_id=instance.instance_id,
                role_id=instance.role_id,
                workspace_id=instance.workspace_id,
                conversation_id=instance.conversation_id,
                status=InstanceStatus.IDLE,
            )
            self._task_repo.update_status(
                task_id=task_id,
                status=TaskStatus.ASSIGNED,
                assigned_instance_id=instance.instance_id,
            )
            if feedback.strip():
                self._persist_followup_message(
                    session_id=record.envelope.session_id,
                    instance_id=instance.instance_id,
                    role_id=instance.role_id,
                    workspace_id=instance.workspace_id,
                    conversation_id=instance.conversation_id,
                    task_id=task_id,
                    trace_id=run_id,
                    content=feedback,
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
                _ = await self._task_execution_service.execute(
                    instance_id=instance.instance_id,
                    role_id=instance.role_id,
                    task=record.envelope,
                )
                records = _records_by_task_id(self._task_repo.list_by_trace(run_id))
                executed.append(
                    build_task_status_row(
                        task_name=task_name,
                        task_id=task_id,
                        role_id=role_id,
                        record=records.get(task_id),
                    )
                )
            except Exception as exc:
                records = _records_by_task_id(self._task_repo.list_by_trace(run_id))
                failed_entry = build_task_status_row(
                    task_name=task_name,
                    task_id=task_id,
                    role_id=role_id,
                    record=records.get(task_id),
                )
                failed_entry["error"] = str(exc)
                failed.append(failed_entry)

        for task_name, task_info in get_ready_tasks(graph, records):
            task_id = str(task_info.get("task_id", ""))
            role_id = str(task_info.get("role_id", ""))
            if not task_id or not role_id:
                continue
            await _ensure_and_execute(task_id, task_name, role_id)
            records = _records_by_task_id(self._task_repo.list_by_trace(run_id))

        progress = _progress(tasks=tasks, records=records)
        converged_stage = _converged_stage(progress=progress, failed=failed)
        return {
            "ok": True,
            "workflow_id": workflow_id,
            "action": "next",
            "dispatched": dispatched,
            "executed": executed,
            "failed": failed,
            "task_status": build_task_status_snapshot(tasks=tasks, records=records),
            "converged_stage": converged_stage,
            "next_action": _next_action(converged_stage, failed),
            "remaining_budget": max(0, bounded_dispatch - len(dispatched)),
            "progress": progress,
        }

    async def _dispatch_revise(
        self,
        *,
        run_id: str,
        workflow_id: str,
        graph: dict[str, object],
        feedback: str,
    ) -> dict[str, object]:
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
        records = _records_by_task_id(self._task_repo.list_by_trace(run_id))
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
        instance_id = record.assigned_instance_id
        if instance_id is None:
            return {
                "ok": False,
                "workflow_id": workflow_id,
                "action": "revise",
                "task_name": task_name,
                "task_id": task_id,
                "message": "Task has no assigned instance.",
            }
        instance = self._instance_pool.get(instance_id)
        self._persist_followup_message(
            session_id=record.envelope.session_id,
            instance_id=instance_id,
            role_id=instance.role_id,
            workspace_id=instance.workspace_id,
            conversation_id=instance.conversation_id,
            task_id=task_id,
            trace_id=run_id,
            content=feedback,
        )
        try:
            _ = await self._task_execution_service.execute(
                instance_id=instance.instance_id,
                role_id=instance.role_id,
                task=record.envelope,
            )
        except Exception as exc:
            refreshed_records = _records_by_task_id(
                self._task_repo.list_by_trace(run_id)
            )
            return {
                "ok": False,
                "workflow_id": workflow_id,
                "action": "revise",
                "task_name": task_name,
                "task_id": task_id,
                "instance_id": instance.instance_id,
                "role_id": instance.role_id,
                "task": build_task_status_row(
                    task_name=task_name,
                    task_id=task_id,
                    role_id=instance.role_id,
                    record=refreshed_records.get(task_id),
                ),
                "task_status": build_task_status_snapshot(
                    tasks=tasks,
                    records=refreshed_records,
                ),
                "error": str(exc),
            }
        refreshed_records = _records_by_task_id(self._task_repo.list_by_trace(run_id))
        progress = _progress(tasks=tasks, records=refreshed_records)
        converged_stage = _converged_stage(progress=progress, failed=[])
        return {
            "ok": True,
            "workflow_id": workflow_id,
            "action": "revise",
            "task_name": task_name,
            "task_id": task_id,
            "instance_id": instance.instance_id,
            "role_id": instance.role_id,
            "task": build_task_status_row(
                task_name=task_name,
                task_id=task_id,
                role_id=instance.role_id,
                record=refreshed_records.get(task_id),
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

    def _persist_followup_message(
        self,
        *,
        session_id: str,
        instance_id: str,
        role_id: str,
        workspace_id: str,
        conversation_id: str,
        task_id: str,
        trace_id: str,
        content: str,
    ) -> None:
        self._message_repo.append_user_prompt_if_missing(
            session_id=session_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_role_id=role_id,
            instance_id=instance_id,
            task_id=task_id,
            trace_id=trace_id,
            content=content,
        )


def _records_by_task_id(records: tuple[TaskRecord, ...]) -> dict[str, TaskRecord]:
    return {record.envelope.task_id: record for record in records}


def _validate_roles_exist(
    role_registry: RoleRegistry, tasks: list[WorkflowTaskSpecInput]
) -> None:
    available_roles = {role.role_id for role in role_registry.list_roles()}
    for task in tasks:
        if task.role_id not in available_roles:
            raise ValueError(
                f"Invalid role_id '{task.role_id}'. Available roles: {sorted(available_roles)}"
            )


def _validate_task_dependencies(tasks: list[WorkflowTaskSpecInput]) -> None:
    task_names: set[str] = set()
    for task in tasks:
        if task.task_name in task_names:
            raise ValueError(f"Duplicate task_name detected: {task.task_name}")
        task_names.add(task.task_name)

    for task in tasks:
        for dependency in task.depends_on:
            if dependency not in task_names:
                raise ValueError(
                    f"Task '{task.task_name}' depends on missing task '{dependency}'."
                )


def _detect_cycle(tasks: list[WorkflowTaskSpecInput]) -> None:
    graph: dict[str, list[str]] = {
        task.task_name: list(task.depends_on) for task in tasks
    }
    visited: set[str] = set()
    rec_stack: set[str] = set()

    def dfs(node: str) -> bool:
        visited.add(node)
        rec_stack.add(node)
        for dep in graph.get(node, []):
            if dep not in visited:
                if dfs(dep):
                    return True
            elif dep in rec_stack:
                return True
        rec_stack.remove(node)
        return False

    for task in tasks:
        if task.task_name not in visited and dfs(task.task_name):
            raise ValueError("Circular dependency detected in tasks")


def _latest_revisable_task(
    *,
    tasks: dict[str, dict[str, object]],
    records: dict[str, TaskRecord],
) -> tuple[str, str] | None:
    revisable_statuses = {
        TaskStatus.COMPLETED,
        TaskStatus.RUNNING,
        TaskStatus.ASSIGNED,
        TaskStatus.STOPPED,
    }
    ordered_task_names = list(tasks.keys())
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
    *, tasks: dict[str, dict[str, object]], records: dict[str, TaskRecord]
) -> dict[str, int]:
    all_tasks = list(tasks.keys())
    completed_tasks = [
        name
        for name in all_tasks
        if records.get(str(tasks[name].get("task_id", "")))
        and records[str(tasks[name].get("task_id", ""))].status == TaskStatus.COMPLETED
    ]
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
