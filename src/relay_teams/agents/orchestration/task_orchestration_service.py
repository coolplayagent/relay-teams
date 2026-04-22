# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.models import create_subagent_instance
from relay_teams.agents.orchestration.task_execution_service import (
    TaskExecutionService,
)
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.ids import new_task_id
from relay_teams.agents.tasks.models import (
    TaskEnvelope,
    TaskRecord,
    VerificationPlan,
)
from relay_teams.hooks import HookEventName, HookService, TaskCreatedInput
from relay_teams.sessions.runs.event_stream import RunEventHub


class TaskDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1)
    title: str | None = None


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str | None = None
    title: str | None = None

    @model_validator(mode="after")
    def validate_non_empty_patch(self) -> TaskUpdate:
        if self.objective is None and self.title is None:
            raise ValueError("update must include at least one field")
        return self


class TaskOrchestrationService:
    def __init__(
        self,
        *,
        task_repo: TaskRepository,
        role_registry: RoleRegistry,
        agent_repo: AgentInstanceRepository,
        task_execution_service: TaskExecutionService,
        message_repo: MessageRepository,
        session_repo: SessionRepository | None = None,
        runtime_role_resolver: RuntimeRoleResolver | None = None,
        hook_service: HookService | None = None,
        run_event_hub: RunEventHub | None = None,
    ) -> None:
        self._task_repo = task_repo
        self._role_registry = role_registry
        self._agent_repo = agent_repo
        self._task_execution_service = task_execution_service
        self._message_repo = message_repo
        self._session_repo = session_repo
        self._runtime_role_resolver = runtime_role_resolver
        self._hook_service = hook_service
        self._run_event_hub = run_event_hub

    async def create_tasks(
        self,
        *,
        run_id: str,
        tasks: list[TaskDraft],
    ) -> dict[str, JsonValue]:
        if not tasks:
            raise ValueError("tasks must contain at least one task")

        root = self._get_root_task(run_id)
        created_records: list[TaskRecord] = []
        for draft in tasks:
            record = self._task_repo.create(
                TaskEnvelope(
                    task_id=new_task_id().value,
                    session_id=root.envelope.session_id,
                    parent_task_id=root.envelope.task_id,
                    trace_id=root.envelope.trace_id,
                    role_id=None,
                    title=_resolved_title(draft.title, draft.objective),
                    objective=draft.objective,
                    verification=VerificationPlan(checklist=("non_empty_response",)),
                )
            )
            created_records.append(record)
            await self._execute_task_created_hooks(record=record)

        response: dict[str, JsonValue] = {
            "created_count": len(created_records),
            "tasks": [_task_projection(record) for record in created_records],
        }
        return response

    async def _execute_task_created_hooks(self, *, record: TaskRecord) -> None:
        if self._hook_service is None:
            return
        envelope = record.envelope
        _ = await self._hook_service.execute(
            event_input=TaskCreatedInput(
                event_name=HookEventName.TASK_CREATED,
                session_id=envelope.session_id,
                run_id=envelope.trace_id,
                trace_id=envelope.trace_id,
                task_id=envelope.task_id,
                role_id=envelope.role_id,
                created_task_id=envelope.task_id,
                parent_task_id=envelope.parent_task_id,
                title=envelope.title or "",
                objective=envelope.objective,
            ),
            run_event_hub=self._run_event_hub,
        )

    def update_task(
        self,
        *,
        run_id: str | None,
        task_id: str,
        update: TaskUpdate,
    ) -> dict[str, JsonValue]:
        record = self.get_task(task_id=task_id, run_id=run_id)
        if record.envelope.parent_task_id is None:
            raise ValueError("root coordinator task cannot be updated via task APIs")
        if record.status != TaskStatus.CREATED:
            raise ValueError("only created tasks can be updated")

        current = record.envelope
        next_objective = (
            str(update.objective).strip()
            if update.objective is not None
            else current.objective
        )
        if not next_objective:
            raise ValueError("objective must not be empty")

        next_title = (
            _resolved_title(update.title, next_objective)
            if update.title is not None
            else current.title or _resolved_title(None, next_objective)
        )
        updated = self._task_repo.update_envelope(
            task_id,
            current.model_copy(
                update={
                    "objective": next_objective,
                    "title": next_title,
                }
            ),
        )
        return {"task": _task_projection(updated)}

    def list_delegated_tasks(
        self,
        *,
        run_id: str,
        include_root: bool = False,
    ) -> dict[str, JsonValue]:
        records = [
            record
            for record in self._task_repo.list_by_trace(run_id)
            if include_root or record.envelope.parent_task_id is not None
        ]
        return {
            "tasks": [_task_projection(record) for record in records],
        }

    def list_run_tasks(
        self,
        *,
        run_id: str,
        include_root: bool = False,
    ) -> dict[str, JsonValue]:
        return self.list_delegated_tasks(run_id=run_id, include_root=include_root)

    async def dispatch_task(
        self,
        *,
        run_id: str | None,
        task_id: str,
        role_id: str,
        prompt: str = "",
    ) -> dict[str, JsonValue]:
        record = self.get_task(task_id=task_id, run_id=run_id)
        resolved_run_id = run_id or record.envelope.trace_id
        if record.envelope.parent_task_id is None:
            raise ValueError("root coordinator task cannot be dispatched via task APIs")
        if record.status == TaskStatus.RUNNING:
            raise ValueError("task is already running")

        normalized_role_id = str(role_id).strip()
        if not normalized_role_id:
            raise ValueError("role_id must not be empty")
        if self._runtime_role_resolver is not None:
            self._runtime_role_resolver.get_effective_role(
                run_id=resolved_run_id,
                role_id=normalized_role_id,
            )
        else:
            self._role_registry.get(normalized_role_id)

        normalized_prompt = prompt.strip()
        bound_role_id = str(record.envelope.role_id or "").strip()
        instance_id = record.assigned_instance_id or ""

        if record.status == TaskStatus.CREATED:
            if bound_role_id and bound_role_id != normalized_role_id:
                raise ValueError(
                    f"Task is already bound to role {bound_role_id}; create a replacement task to change roles."
                )
            if not bound_role_id:
                record = self._task_repo.update_envelope(
                    task_id,
                    record.envelope.model_copy(update={"role_id": normalized_role_id}),
                )
                bound_role_id = normalized_role_id
            bound_instance_id = self._ensure_role_instance(
                session_id=record.envelope.session_id,
                run_id=resolved_run_id,
                role_id=bound_role_id,
            )
            instance_id = bound_instance_id
            self._task_repo.update_status(
                task_id=task_id,
                status=TaskStatus.ASSIGNED,
                assigned_instance_id=instance_id,
            )
            record = self._task_repo.get(task_id)
        else:
            if not bound_role_id:
                raise ValueError(
                    "task must be bound to a role before it can be re-dispatched"
                )
            if bound_role_id != normalized_role_id:
                raise ValueError(
                    f"Task is already bound to role {bound_role_id}; create a replacement task to change roles."
                )
        if record.status == TaskStatus.COMPLETED:
            raise ValueError(
                f"Task '{record.envelope.title}' (role={bound_role_id}) "
                "is completed. Create a replacement task instead of re-dispatching this one."
            )
        elif record.status in {TaskStatus.FAILED, TaskStatus.TIMEOUT}:
            raise ValueError(
                f"Task '{record.envelope.title}' (role={bound_role_id}) "
                f"is {record.status.value}: "
                f"{record.error_message or 'unknown error'}. "
                "Create a replacement task instead of re-dispatching this one."
            )

        if not instance_id:
            instance_id = record.assigned_instance_id or ""
        if not instance_id:
            raise ValueError("task has no bound instance to dispatch")

        self._assert_instance_available(task=record, instance_id=instance_id)

        effective_prompt = (
            normalized_prompt
            or "Execute this task contract and return the requested result."
        )

        await self._task_execution_service.execute(
            instance_id=instance_id,
            role_id=bound_role_id,
            task=record.envelope,
            user_prompt_override=effective_prompt,
        )
        refreshed = self._task_repo.get(task_id)
        return {
            "task": _task_projection(refreshed),
        }

    def _ensure_role_instance(
        self,
        *,
        session_id: str,
        run_id: str,
        role_id: str,
    ) -> str:
        existing = self._agent_repo.get_session_role_instance(session_id, role_id)
        if existing is not None:
            self._agent_repo.upsert_instance(
                run_id=run_id,
                trace_id=run_id,
                session_id=session_id,
                instance_id=existing.instance_id,
                role_id=existing.role_id,
                workspace_id=existing.workspace_id,
                conversation_id=existing.conversation_id,
                status=existing.status,
            )
            return existing.instance_id

        session = self._session_repo.get(session_id) if self._session_repo else None
        if session is None:
            raise RuntimeError(
                "TaskOrchestrationService requires session_repo to resolve workspace"
            )
        instance = create_subagent_instance(
            role_id,
            session_id=session_id,
            workspace_id=session.workspace_id,
        )
        self._agent_repo.upsert_instance(
            run_id=run_id,
            trace_id=run_id,
            session_id=session_id,
            instance_id=instance.instance_id,
            role_id=instance.role_id,
            workspace_id=instance.workspace_id,
            conversation_id=instance.conversation_id,
            status=InstanceStatus.IDLE,
        )
        return instance.instance_id

    def _assert_instance_available(self, *, task: TaskRecord, instance_id: str) -> None:
        blocking_statuses = {
            TaskStatus.ASSIGNED,
            TaskStatus.RUNNING,
            TaskStatus.STOPPED,
        }
        for candidate in self._task_repo.list_by_session(task.envelope.session_id):
            if candidate.envelope.task_id == task.envelope.task_id:
                continue
            if candidate.assigned_instance_id != instance_id:
                continue
            if candidate.status not in blocking_statuses:
                continue
            raise ValueError(
                f"Role {candidate.envelope.role_id or 'unassigned'} is busy with task "
                f"'{candidate.envelope.title}' (status={candidate.status.value}). "
                f"Wait for it to complete or use a different role."
            )

    def _get_root_task(self, run_id: str) -> TaskRecord:
        for record in self._task_repo.list_by_trace(run_id):
            if record.envelope.parent_task_id is None:
                return record
        raise KeyError(f"No root task found for run_id={run_id}")

    def get_task(self, *, task_id: str, run_id: str | None = None) -> TaskRecord:
        record = self._task_repo.get(task_id)
        if run_id is not None and record.envelope.trace_id != run_id:
            raise KeyError(f"Task {task_id} does not belong to run {run_id}")
        return record


def _resolved_title(title: str | None, objective: str) -> str:
    normalized = str(title or "").strip()
    if normalized:
        return normalized
    summary = " ".join(objective.strip().split())
    if not summary:
        raise ValueError("objective must not be empty")
    return summary[:80]


def _task_projection(record: TaskRecord) -> dict[str, JsonValue]:
    assigned_role_id = record.envelope.role_id
    assigned_instance_id = record.assigned_instance_id
    row: dict[str, JsonValue] = {
        "task_id": record.envelope.task_id,
        "title": record.envelope.title
        or _resolved_title(None, record.envelope.objective),
        "objective": record.envelope.objective,
        "status": record.status.value,
        "assigned_role_id": assigned_role_id,
        "assigned_instance_id": assigned_instance_id,
        "role_id": assigned_role_id,
        "instance_id": assigned_instance_id,
        "parent_task_id": record.envelope.parent_task_id,
    }
    if record.result:
        row["result"] = record.result
    if record.error_message:
        row["error"] = record.error_message
    return row
