# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.shared_types.json_types import JsonObject
from agent_teams.agents.enums import InstanceStatus
from agent_teams.agents.models import create_subagent_instance
from agent_teams.coordination.task_execution_service import TaskExecutionService
from agent_teams.roles.registry import RoleRegistry
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.task_repo import TaskRepository
from agent_teams.workflow.enums import TaskStatus
from agent_teams.workflow.ids import new_task_id
from agent_teams.workflow.models import TaskEnvelope, TaskRecord, VerificationPlan


class TaskDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    title: str | None = None


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str | None = None
    objective: str | None = None
    title: str | None = None


class TaskOrchestrationService:
    def __init__(
        self,
        *,
        task_repo: TaskRepository,
        role_registry: RoleRegistry,
        agent_repo: AgentInstanceRepository,
        task_execution_service: TaskExecutionService,
        message_repo: MessageRepository,
    ) -> None:
        self._task_repo = task_repo
        self._role_registry = role_registry
        self._agent_repo = agent_repo
        self._task_execution_service = task_execution_service
        self._message_repo = message_repo

    async def create_tasks(
        self,
        *,
        run_id: str,
        tasks: list[TaskDraft],
        auto_dispatch: bool = False,
    ) -> JsonObject:
        if not tasks:
            raise ValueError("tasks must contain at least one task")
        if auto_dispatch and len(tasks) != 1:
            raise ValueError("auto_dispatch only supports a single task")

        root = self._get_root_task(run_id)
        created_records: list[TaskRecord] = []
        for draft in tasks:
            self._role_registry.get(draft.role_id)
            created_records.append(
                self._task_repo.create(
                    TaskEnvelope(
                        task_id=new_task_id().value,
                        session_id=root.envelope.session_id,
                        parent_task_id=root.envelope.task_id,
                        trace_id=root.envelope.trace_id,
                        role_id=draft.role_id,
                        title=_resolved_title(draft.title, draft.objective),
                        objective=draft.objective,
                        verification=VerificationPlan(
                            checklist=("non_empty_response",)
                        ),
                    )
                )
            )

        response: JsonObject = {
            "ok": True,
            "created_count": len(created_records),
            "tasks": [_task_projection(record) for record in created_records],
        }
        if auto_dispatch:
            dispatched = await self.dispatch_task(
                run_id=run_id,
                task_id=created_records[0].envelope.task_id,
            )
            response["dispatched_task"] = dispatched
        return response

    def update_task(
        self,
        *,
        run_id: str | None,
        task_id: str,
        update: TaskUpdate,
    ) -> JsonObject:
        record = self.get_task(task_id=task_id, run_id=run_id)
        if record.envelope.parent_task_id is None:
            raise ValueError("root coordinator task cannot be updated via task APIs")
        if record.status != TaskStatus.CREATED:
            raise ValueError("only created tasks can be updated")

        current = record.envelope
        next_role_id = (
            str(update.role_id).strip()
            if update.role_id is not None
            else current.role_id
        )
        if not next_role_id:
            raise ValueError("role_id must not be empty")
        self._role_registry.get(next_role_id)

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
                    "role_id": next_role_id,
                    "objective": next_objective,
                    "title": next_title,
                }
            ),
        )
        return {"ok": True, "task": _task_projection(updated)}

    def list_run_tasks(
        self,
        *,
        run_id: str,
        include_root: bool = False,
    ) -> JsonObject:
        records = [
            record
            for record in self._task_repo.list_by_trace(run_id)
            if include_root or record.envelope.parent_task_id is not None
        ]
        return {
            "ok": True,
            "tasks": [_task_projection(record) for record in records],
        }

    async def dispatch_task(
        self,
        *,
        run_id: str | None,
        task_id: str,
        feedback: str = "",
    ) -> JsonObject:
        record = self.get_task(task_id=task_id, run_id=run_id)
        resolved_run_id = run_id or record.envelope.trace_id
        if record.envelope.parent_task_id is None:
            raise ValueError("root coordinator task cannot be dispatched via task APIs")
        if record.status == TaskStatus.RUNNING:
            raise ValueError("task is already running")

        normalized_feedback = feedback.strip()
        role_id = record.envelope.role_id
        instance_id = record.assigned_instance_id or ""

        if record.status == TaskStatus.CREATED:
            bound_instance_id = self._ensure_role_instance(
                session_id=record.envelope.session_id,
                run_id=resolved_run_id,
                role_id=role_id,
            )
            instance_id = bound_instance_id
            self._task_repo.update_status(
                task_id=task_id,
                status=TaskStatus.ASSIGNED,
                assigned_instance_id=instance_id,
            )
            record = self._task_repo.get(task_id)
        elif record.status == TaskStatus.COMPLETED and not normalized_feedback:
            raise ValueError("feedback is required to re-dispatch a completed task")
        elif record.status in {TaskStatus.FAILED, TaskStatus.TIMEOUT}:
            raise ValueError("failed or timed out tasks must be recreated")

        if not instance_id:
            instance_id = record.assigned_instance_id or ""
        if not instance_id:
            raise ValueError("task has no bound instance to dispatch")

        self._assert_instance_available(task=record, instance_id=instance_id)

        if normalized_feedback:
            self._append_followup_prompt(
                task=record,
                instance_id=instance_id,
                content=normalized_feedback,
            )

        await self._task_execution_service.execute(
            instance_id=instance_id,
            role_id=role_id,
            task=record.envelope,
        )
        refreshed = self._task_repo.get(task_id)
        return {
            "ok": True,
            "task": _task_projection(refreshed),
        }

    def _append_followup_prompt(
        self,
        *,
        task: TaskRecord,
        instance_id: str,
        content: str,
    ) -> None:
        agent = self._agent_repo.get_instance(instance_id)
        self._message_repo.append_user_prompt_if_missing(
            session_id=task.envelope.session_id,
            workspace_id=agent.workspace_id,
            conversation_id=agent.conversation_id,
            agent_role_id=agent.role_id,
            instance_id=instance_id,
            task_id=task.envelope.task_id,
            trace_id=task.envelope.trace_id,
            content=content,
        )

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

        instance = create_subagent_instance(role_id, session_id=session_id)
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
                "role instance is busy; finish or resume the existing task before dispatching another task for this role"
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


def _task_projection(record: TaskRecord) -> JsonObject:
    row: JsonObject = {
        "task_id": record.envelope.task_id,
        "title": record.envelope.title
        or _resolved_title(None, record.envelope.objective),
        "role_id": record.envelope.role_id,
        "objective": record.envelope.objective,
        "status": record.status.value,
        "instance_id": record.assigned_instance_id or "",
        "parent_task_id": record.envelope.parent_task_id,
    }
    if record.result:
        row["result"] = record.result
    if record.error_message:
        row["error"] = record.error_message
    return row
