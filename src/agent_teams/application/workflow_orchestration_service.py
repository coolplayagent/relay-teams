from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.coordination.task_execution_service import TaskExecutionService
from agent_teams.core.enums import InjectionSource, InstanceStatus, TaskStatus
from agent_teams.core.models import TaskEnvelope, TaskRecord, VerificationPlan
from agent_teams.roles.registry import RoleRegistry
from agent_teams.runtime.injection_manager import RunInjectionManager
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository
from agent_teams.workflow.runtime_graph import get_ready_tasks, load_graph, save_graph

WorkflowType = Literal["spec_flow", "custom"]
DispatchAction = Literal["next", "revise"]


class WorkflowTaskSpecInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_name: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)


class WorkflowOrchestrationService:
    def __init__(
        self,
        *,
        task_repo: TaskRepository,
        shared_store: SharedStore,
        role_registry: RoleRegistry,
        instance_pool: InstancePool,
        agent_repo: AgentInstanceRepository,
        task_execution_service: TaskExecutionService,
        injection_manager: RunInjectionManager,
    ) -> None:
        self._task_repo: TaskRepository = task_repo
        self._shared_store: SharedStore = shared_store
        self._role_registry: RoleRegistry = role_registry
        self._instance_pool: InstancePool = instance_pool
        self._agent_repo: AgentInstanceRepository = agent_repo
        self._task_execution_service: TaskExecutionService = task_execution_service
        self._injection_manager: RunInjectionManager = injection_manager

    def create_workflow_graph(
        self,
        *,
        run_id: str,
        objective: str,
        workflow_type: WorkflowType = "custom",
        tasks: list[WorkflowTaskSpecInput] | None = None,
    ) -> dict[str, object]:
        root = self._get_root_task(run_id=run_id)
        existing = load_graph(self._shared_store, task_id=root.envelope.task_id)
        if existing is not None:
            return {
                "ok": True,
                "created": False,
                "message": (
                    "A workflow already exists for this run. Use dispatch_tasks to continue, "
                    "or start a new run for a fresh workflow."
                ),
                "workflow_id": existing.get("workflow_id"),
                "workflow_type": existing.get("workflow_type"),
            }

        parsed_tasks = tasks
        if workflow_type == "spec_flow":
            parsed_tasks = _create_spec_flow_template(objective=objective)
        elif not parsed_tasks:
            raise ValueError(
                "tasks is required for custom workflow. "
                + 'Example: [{"task_name": "code", "objective": "Write hello.py", '
                + '"role_id": "spec_coder", "depends_on": []}]'
            )

        _validate_role_depends(self._role_registry, parsed_tasks)
        _detect_cycle(parsed_tasks)
        workflow_id = f"workflow_{uuid4().hex[:8]}"

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
            "workflow_id": workflow_id,
            "workflow_type": workflow_type,
            "objective": objective,
            "trace_id": root.envelope.trace_id,
            "session_id": root.envelope.session_id,
            "tasks": {
                spec.task_name: {
                    "task_id": name_to_task_id[spec.task_name],
                    "role_id": spec.role_id,
                    "depends_on": spec.depends_on,
                }
                for spec in parsed_tasks
            },
        }
        save_graph(self._shared_store, task_id=root.envelope.task_id, graph=graph)

        return {
            "ok": True,
            "created": True,
            "workflow_id": workflow_id,
            "workflow_type": workflow_type,
            "tasks": [
                {
                    "task_name": spec.task_name,
                    "task_id": name_to_task_id[spec.task_name],
                    "role_id": spec.role_id,
                    "depends_on": spec.depends_on,
                }
                for spec in parsed_tasks
            ],
        }

    def get_workflow_status(
        self, *, run_id: str, workflow_id: str
    ) -> dict[str, object]:
        root = self._get_root_task(run_id=run_id)
        graph = load_graph(self._shared_store, task_id=root.envelope.task_id)
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

        task_status: dict[str, dict[str, object]] = {}
        for task_name, task_info in tasks.items():
            task_id = str(task_info.get("task_id", ""))
            role_id = str(task_info.get("role_id", ""))
            record = records.get(task_id)
            if record is None:
                task_status[task_name] = {"status": "missing", "role_id": role_id}
                continue
            row: dict[str, object] = {"status": record.status.value, "role_id": role_id}
            if record.result:
                row["result"] = record.result
            if record.error_message:
                row["error"] = record.error_message
            task_status[task_name] = row

        return {
            "ok": True,
            "workflow_id": workflow_id,
            "workflow_type": graph.get("workflow_type"),
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
        root = self._get_root_task(run_id=run_id)
        graph = load_graph(self._shared_store, task_id=root.envelope.task_id)
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
        executed: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []

        async def _ensure_and_execute(
            task_id: str, task_name: str, role_id: str
        ) -> None:
            if len(dispatched) >= bounded_dispatch:
                return
            record = records.get(task_id)
            if record is None or record.status != TaskStatus.CREATED:
                return
            instance = self._instance_pool.create_subagent(role_id)
            self._agent_repo.upsert_instance(
                run_id=run_id,
                trace_id=run_id,
                session_id=record.envelope.session_id,
                instance_id=instance.instance_id,
                role_id=instance.role_id,
                status=InstanceStatus.IDLE,
            )
            self._task_repo.update_status(
                task_id=task_id,
                status=TaskStatus.ASSIGNED,
                assigned_instance_id=instance.instance_id,
            )
            if feedback.strip():
                _ = self._injection_manager.enqueue(
                    run_id=run_id,
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
                _ = await self._task_execution_service.execute(
                    instance_id=instance.instance_id,
                    role_id=instance.role_id,
                    task=record.envelope,
                )
                executed.append(
                    {"task_id": task_id, "task_name": task_name, "status": "completed"}
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
        records = _records_by_task_id(self._task_repo.list_by_trace(run_id))
        latest = _latest_completed_task(tasks=tasks, records=records)
        if latest is None:
            return {
                "ok": False,
                "workflow_id": workflow_id,
                "action": "revise",
                "message": "No completed task to revise.",
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
        if feedback.strip():
            _ = self._injection_manager.enqueue(
                run_id=run_id,
                recipient_instance_id=instance_id,
                source=InjectionSource.USER,
                content=f"Please revise your previous output based on this feedback: {feedback}",
            )
        try:
            _ = await self._task_execution_service.execute(
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
                "error": str(exc),
            }
        return {
            "ok": True,
            "workflow_id": workflow_id,
            "action": "revise",
            "task_name": task_name,
            "task_id": task_id,
            "message": "Revision completed successfully.",
        }


def _records_by_task_id(records: tuple[TaskRecord, ...]) -> dict[str, TaskRecord]:
    return {record.envelope.task_id: record for record in records}


def _create_spec_flow_template(objective: str) -> list[WorkflowTaskSpecInput]:
    return [
        WorkflowTaskSpecInput(
            task_name="spec",
            objective=(
                f'Input: user requirement "{objective}". '
                "Output: a structured requirement specification with clear goals, scope, and acceptance criteria."
            ),
            role_id="spec_spec",
            depends_on=[],
        ),
        WorkflowTaskSpecInput(
            task_name="design",
            objective=(
                f'Input: spec.md from previous stage for "{objective}". '
                "Output: an implementation-ready technical design describing architecture, interfaces, and testing."
            ),
            role_id="spec_design",
            depends_on=["spec"],
        ),
        WorkflowTaskSpecInput(
            task_name="code",
            objective=(
                f'Input: design.md from previous stage for "{objective}". '
                "Output: code changes and tests that implement the approved design."
            ),
            role_id="spec_coder",
            depends_on=["design"],
        ),
        WorkflowTaskSpecInput(
            task_name="verify",
            objective=(
                f'Input: implementation output and design artifacts for "{objective}". '
                "Output: a verification verdict (PASS/FAIL) with concrete findings and coverage gaps."
            ),
            role_id="spec_verify",
            depends_on=["code"],
        ),
    ]


def _validate_role_depends(
    role_registry: RoleRegistry, tasks: list[WorkflowTaskSpecInput]
) -> None:
    available_roles = {r.role_id for r in role_registry.list_roles()}
    role_to_tasks: dict[str, list[str]] = {}
    for task in tasks:
        role_to_tasks.setdefault(task.role_id, []).append(task.task_name)

    for task in tasks:
        if task.role_id not in available_roles:
            raise ValueError(
                f"Invalid role_id '{task.role_id}'. Available roles: {sorted(available_roles)}"
            )
        role_def = role_registry.get(task.role_id)
        for required_role in list(role_def.depends_on) or []:
            if required_role not in role_to_tasks:
                raise ValueError(
                    f"Role '{task.role_id}' depends on '{required_role}', but '{required_role}' is not in the task list."
                )


def _detect_cycle(tasks: list[WorkflowTaskSpecInput]) -> None:
    graph: dict[str, list[str]] = {task.task_name: task.depends_on for task in tasks}
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


def _latest_completed_task(
    *,
    tasks: dict[str, dict[str, object]],
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
