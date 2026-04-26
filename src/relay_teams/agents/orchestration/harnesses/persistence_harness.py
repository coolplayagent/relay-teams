# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, JsonValue

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.orchestration.task_contracts import TaskExecutionResult
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.events import EventEnvelope, EventType
from relay_teams.agents.tasks.models import TaskEnvelope, TaskRecord
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.hooks import HookEventName, HookService, TaskCompletedInput
from relay_teams.logger import get_logger, log_event
from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.sessions.runs.assistant_errors import (
    RunCompletionReason,
    build_assistant_error_response,
)
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)

LOGGER = get_logger(__name__)
TASK_MEMORY_RESULT_EXCERPT_CHARS = 2000


class TaskPersistenceHarness(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    task_repo: TaskRepository | None = None
    shared_store: SharedStateRepository | None = None
    event_bus: EventLog | None = None
    agent_repo: AgentInstanceRepository | None = None
    message_repo: MessageRepository | None = None
    run_runtime_repo: RunRuntimeRepository | None = None
    run_event_hub: RunEventHub | None = None
    run_control_manager: RunControlManager | None = None
    hook_service: HookService | None = None

    async def execute_task_completed_hooks(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        output_text: str,
    ) -> None:
        if self.hook_service is None or task.parent_task_id is None:
            return
        _ = await self.hook_service.execute(
            event_input=TaskCompletedInput(
                event_name=HookEventName.TASK_COMPLETED,
                session_id=task.session_id,
                run_id=task.trace_id,
                trace_id=task.trace_id,
                task_id=task.task_id,
                instance_id=instance_id,
                role_id=role_id,
                completed_task_id=task.task_id,
                title=task.title or "",
                objective=task.objective,
                output_text=output_text,
                completion_reason=TaskStatus.COMPLETED.value,
            ),
            run_event_hub=self.run_event_hub,
        )

    def complete_with_assistant_error(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        conversation_id: str,
        workspace_id: str,
        assistant_message: str,
        error_code: str,
        error_message: str,
        append_message: bool = True,
    ) -> TaskExecutionResult:
        message_repo = self.message_repo
        task_repo = self.task_repo
        agent_repo = self.agent_repo
        event_bus = self.event_bus
        assert message_repo is not None
        assert task_repo is not None
        assert agent_repo is not None
        assert event_bus is not None
        if append_message:
            message_repo.prune_conversation_history_to_safe_boundary(conversation_id)
            message_repo.append(
                session_id=task.session_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_role_id=role_id,
                instance_id=instance_id,
                task_id=task.task_id,
                trace_id=task.trace_id,
                messages=[build_assistant_error_response(assistant_message)],
            )
        task_repo.update_status(
            task.task_id,
            TaskStatus.FAILED,
            assigned_instance_id=instance_id,
            result=assistant_message,
            error_message=error_message or assistant_message,
        )
        agent_repo.mark_status(instance_id, InstanceStatus.FAILED)
        self.mark_runtime_after_terminal_task_update(
            run_id=task.trace_id,
            terminal_task_id=task.task_id,
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.IDLE,
            active_instance_id=None,
            active_task_id=None,
            active_role_id=None,
            active_subagent_instance_id=None,
            last_error=error_message or assistant_message,
        )
        event_bus.emit(
            EventEnvelope(
                event_type=EventType.TASK_FAILED,
                trace_id=task.trace_id,
                session_id=task.session_id,
                task_id=task.task_id,
                instance_id=instance_id,
                payload_json="{}",
            )
        )
        log_event(
            LOGGER,
            logging.WARNING,
            event="task.execution.failed_with_assistant_error",
            message="Task execution failed after assistant error message was persisted",
            payload={
                "task_id": task.task_id,
                "instance_id": instance_id,
                "role_id": role_id,
                "error_code": error_code,
            },
        )
        return TaskExecutionResult(
            output=assistant_message,
            completion_reason=RunCompletionReason.ASSISTANT_ERROR,
            error_code=error_code,
            error_message=error_message or assistant_message,
        )

    async def complete_with_assistant_error_async(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        conversation_id: str,
        workspace_id: str,
        assistant_message: str,
        error_code: str,
        error_message: str,
        append_message: bool = True,
    ) -> TaskExecutionResult:
        message_repo = self.message_repo
        task_repo = self.task_repo
        agent_repo = self.agent_repo
        event_bus = self.event_bus
        assert message_repo is not None
        assert task_repo is not None
        assert agent_repo is not None
        assert event_bus is not None
        if append_message:
            await message_repo.prune_conversation_history_to_safe_boundary_async(
                conversation_id
            )
            await message_repo.append_async(
                session_id=task.session_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_role_id=role_id,
                instance_id=instance_id,
                task_id=task.task_id,
                trace_id=task.trace_id,
                messages=[build_assistant_error_response(assistant_message)],
            )
        await task_repo.update_status_async(
            task.task_id,
            TaskStatus.FAILED,
            assigned_instance_id=instance_id,
            result=assistant_message,
            error_message=error_message or assistant_message,
        )
        await agent_repo.mark_status_async(instance_id, InstanceStatus.FAILED)
        await self.mark_runtime_after_terminal_task_update_async(
            run_id=task.trace_id,
            terminal_task_id=task.task_id,
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.IDLE,
            active_instance_id=None,
            active_task_id=None,
            active_role_id=None,
            active_subagent_instance_id=None,
            last_error=error_message or assistant_message,
        )
        await event_bus.emit_async(
            EventEnvelope(
                event_type=EventType.TASK_FAILED,
                trace_id=task.trace_id,
                session_id=task.session_id,
                task_id=task.task_id,
                instance_id=instance_id,
                payload_json="{}",
            )
        )
        log_event(
            LOGGER,
            logging.WARNING,
            event="task.execution.failed_with_assistant_error",
            message="Task execution failed after assistant error message was persisted",
            payload={
                "task_id": task.task_id,
                "instance_id": instance_id,
                "role_id": role_id,
                "error_code": error_code,
            },
        )
        return TaskExecutionResult(
            output=assistant_message,
            completion_reason=RunCompletionReason.ASSISTANT_ERROR,
            error_code=error_code,
            error_message=error_message or assistant_message,
        )

    def record_memory_if_needed(
        self,
        *,
        role_id: str,
        workspace_id: str,
        task: TaskEnvelope,
        conversation_id: str,
        instance_id: str,
        lifecycle: str,
        result: str,
    ) -> None:
        shared_store = self.shared_store
        assert shared_store is not None
        payload: dict[str, JsonValue] = {
            "task_id": task.task_id,
            "title": task.title or "",
            "objective": task.objective[:500],
            "role_id": role_id,
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "instance_id": instance_id,
            "lifecycle": lifecycle,
            "result_excerpt": truncate_task_memory_result(result),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        try:
            shared_store.manage_state(
                StateMutation(
                    scope=ScopeRef(
                        scope_type=ScopeType.ROLE,
                        scope_id=f"{task.session_id}:{role_id}",
                    ),
                    key=f"task_result:{task.task_id}",
                    value_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                )
            )
        except (AttributeError, RuntimeError, sqlite3.Error):
            LOGGER.warning(
                "Failed to persist completed task memory",
                extra={
                    "task_id": task.task_id,
                    "role_id": role_id,
                    "instance_id": instance_id,
                },
                exc_info=True,
            )

    async def record_memory_if_needed_async(
        self,
        *,
        role_id: str,
        workspace_id: str,
        task: TaskEnvelope,
        conversation_id: str,
        instance_id: str,
        lifecycle: str,
        result: str,
    ) -> None:
        shared_store = self.shared_store
        assert shared_store is not None
        payload: dict[str, JsonValue] = {
            "task_id": task.task_id,
            "title": task.title or "",
            "objective": task.objective[:500],
            "role_id": role_id,
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "instance_id": instance_id,
            "lifecycle": lifecycle,
            "result_excerpt": truncate_task_memory_result(result),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        try:
            await shared_store.manage_state_async(
                StateMutation(
                    scope=ScopeRef(
                        scope_type=ScopeType.ROLE,
                        scope_id=f"{task.session_id}:{role_id}",
                    ),
                    key=f"task_result:{task.task_id}",
                    value_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                )
            )
        except (AttributeError, RuntimeError, sqlite3.Error):
            LOGGER.warning(
                "Failed to persist completed task memory",
                extra={
                    "task_id": task.task_id,
                    "role_id": role_id,
                    "instance_id": instance_id,
                },
                exc_info=True,
            )

    def mark_runtime_idle_after_success(
        self,
        *,
        run_id: str,
        completed_task_id: str,
    ) -> None:
        self.mark_runtime_after_terminal_task_update(
            run_id=run_id,
            terminal_task_id=completed_task_id,
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.IDLE,
            active_instance_id=None,
            active_task_id=None,
            active_role_id=None,
            active_subagent_instance_id=None,
            last_error=None,
        )

    async def mark_runtime_idle_after_success_async(
        self,
        *,
        run_id: str,
        completed_task_id: str,
    ) -> None:
        await self.mark_runtime_after_terminal_task_update_async(
            run_id=run_id,
            terminal_task_id=completed_task_id,
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.IDLE,
            active_instance_id=None,
            active_task_id=None,
            active_role_id=None,
            active_subagent_instance_id=None,
            last_error=None,
        )

    def mark_runtime_after_terminal_task_update(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        status: RunRuntimeStatus,
        phase: RunRuntimePhase,
        active_instance_id: str | None,
        active_task_id: str | None,
        active_role_id: str | None,
        active_subagent_instance_id: str | None,
        last_error: str | None,
    ) -> None:
        run_runtime_repo = self.run_runtime_repo
        assert run_runtime_repo is not None
        current = run_runtime_repo.get(run_id)
        if current is not None and current.active_task_id not in {
            None,
            terminal_task_id,
        }:
            if last_error is not None:
                run_runtime_repo.update(run_id, last_error=last_error)
            return
        if self.promote_running_runtime_lane(
            run_id=run_id,
            terminal_task_id=terminal_task_id,
            last_error=last_error,
        ):
            return
        if self.promote_paused_runtime_lane(
            run_id=run_id,
            terminal_task_id=terminal_task_id,
            last_error=last_error,
        ):
            return
        run_runtime_repo.update(
            run_id,
            status=status,
            phase=phase,
            active_instance_id=active_instance_id,
            active_task_id=active_task_id,
            active_role_id=active_role_id,
            active_subagent_instance_id=active_subagent_instance_id,
            last_error=last_error,
        )

    async def mark_runtime_after_terminal_task_update_async(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        status: RunRuntimeStatus,
        phase: RunRuntimePhase,
        active_instance_id: str | None,
        active_task_id: str | None,
        active_role_id: str | None,
        active_subagent_instance_id: str | None,
        last_error: str | None,
    ) -> None:
        run_runtime_repo = self.run_runtime_repo
        assert run_runtime_repo is not None
        current = await run_runtime_repo.get_async(run_id)
        if current is not None and current.active_task_id not in {
            None,
            terminal_task_id,
        }:
            if last_error is not None:
                await run_runtime_repo.update_async(run_id, last_error=last_error)
            return
        if await self.promote_running_runtime_lane_async(
            run_id=run_id,
            terminal_task_id=terminal_task_id,
            last_error=last_error,
        ):
            return
        if await self.promote_paused_runtime_lane_async(
            run_id=run_id,
            terminal_task_id=terminal_task_id,
            last_error=last_error,
        ):
            return
        await run_runtime_repo.update_async(
            run_id,
            status=status,
            phase=phase,
            active_instance_id=active_instance_id,
            active_task_id=active_task_id,
            active_role_id=active_role_id,
            active_subagent_instance_id=active_subagent_instance_id,
            last_error=last_error,
        )

    def promote_running_runtime_lane(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        last_error: str | None,
    ) -> bool:
        task_repo = self.task_repo
        run_runtime_repo = self.run_runtime_repo
        assert task_repo is not None
        assert run_runtime_repo is not None
        coordinator_record: TaskRecord | None = None
        promoted_record: TaskRecord | None = None
        for record in task_repo.list_by_trace(run_id):
            task = record.envelope
            if task.task_id == terminal_task_id:
                continue
            if record.status != TaskStatus.RUNNING:
                continue
            if not record.assigned_instance_id:
                continue
            if task.parent_task_id is not None:
                promoted_record = record
                break
            if coordinator_record is None:
                coordinator_record = record
        if promoted_record is None:
            promoted_record = coordinator_record
        if promoted_record is None:
            return False
        task = promoted_record.envelope
        instance_id = promoted_record.assigned_instance_id
        if not instance_id:
            return False
        is_coordinator = task.parent_task_id is None
        run_runtime_repo.update(
            run_id,
            status=RunRuntimeStatus.RUNNING,
            phase=(
                RunRuntimePhase.COORDINATOR_RUNNING
                if is_coordinator
                else RunRuntimePhase.SUBAGENT_RUNNING
            ),
            active_instance_id=instance_id,
            active_task_id=task.task_id,
            active_role_id=task.role_id,
            active_subagent_instance_id=(None if is_coordinator else instance_id),
            last_error=last_error,
        )
        return True

    async def promote_running_runtime_lane_async(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        last_error: str | None,
    ) -> bool:
        task_repo = self.task_repo
        run_runtime_repo = self.run_runtime_repo
        assert task_repo is not None
        assert run_runtime_repo is not None
        coordinator_record: TaskRecord | None = None
        promoted_record: TaskRecord | None = None
        for record in await task_repo.list_by_trace_async(run_id):
            task = record.envelope
            if task.task_id == terminal_task_id:
                continue
            if record.status != TaskStatus.RUNNING:
                continue
            if not record.assigned_instance_id:
                continue
            if task.parent_task_id is not None:
                promoted_record = record
                break
            if coordinator_record is None:
                coordinator_record = record
        if promoted_record is None:
            promoted_record = coordinator_record
        if promoted_record is None:
            return False
        task = promoted_record.envelope
        instance_id = promoted_record.assigned_instance_id
        if not instance_id:
            return False
        is_coordinator = task.parent_task_id is None
        await run_runtime_repo.update_async(
            run_id,
            status=RunRuntimeStatus.RUNNING,
            phase=(
                RunRuntimePhase.COORDINATOR_RUNNING
                if is_coordinator
                else RunRuntimePhase.SUBAGENT_RUNNING
            ),
            active_instance_id=instance_id,
            active_task_id=task.task_id,
            active_role_id=task.role_id,
            active_subagent_instance_id=(None if is_coordinator else instance_id),
            last_error=last_error,
        )
        return True

    def promote_paused_runtime_lane(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        last_error: str | None,
    ) -> bool:
        task_repo = self.task_repo
        run_runtime_repo = self.run_runtime_repo
        assert task_repo is not None
        assert run_runtime_repo is not None
        if self.run_control_manager is None:
            return False
        if self.run_control_manager.is_run_stop_requested(run_id):
            return False
        for record in task_repo.list_by_trace(run_id):
            task = record.envelope
            instance_id = record.assigned_instance_id
            if task.task_id == terminal_task_id:
                continue
            if task.parent_task_id is None:
                continue
            if record.status != TaskStatus.STOPPED:
                continue
            if not instance_id:
                continue
            if not (
                self.run_control_manager.is_subagent_stop_requested(
                    run_id=run_id,
                    instance_id=instance_id,
                )
                or self.run_control_manager.is_subagent_paused(
                    session_id=task.session_id,
                    instance_id=instance_id,
                )
            ):
                continue
            run_runtime_repo.update(
                run_id,
                status=RunRuntimeStatus.STOPPED,
                phase=RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP,
                active_instance_id=None,
                active_task_id=task.task_id,
                active_role_id=task.role_id,
                active_subagent_instance_id=instance_id,
                last_error=last_error or record.error_message or "Task stopped by user",
            )
            return True
        return False

    async def promote_paused_runtime_lane_async(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        last_error: str | None,
    ) -> bool:
        task_repo = self.task_repo
        run_runtime_repo = self.run_runtime_repo
        assert task_repo is not None
        assert run_runtime_repo is not None
        if self.run_control_manager is None:
            return False
        if self.run_control_manager.is_run_stop_requested(run_id):
            return False
        for record in await task_repo.list_by_trace_async(run_id):
            task = record.envelope
            instance_id = record.assigned_instance_id
            if task.task_id == terminal_task_id:
                continue
            if task.parent_task_id is None:
                continue
            if record.status != TaskStatus.STOPPED:
                continue
            if not instance_id:
                continue
            if not (
                self.run_control_manager.is_subagent_stop_requested(
                    run_id=run_id,
                    instance_id=instance_id,
                )
                or self.run_control_manager.is_subagent_paused(
                    session_id=task.session_id,
                    instance_id=instance_id,
                )
            ):
                continue
            await run_runtime_repo.update_async(
                run_id,
                status=RunRuntimeStatus.STOPPED,
                phase=RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP,
                active_instance_id=None,
                active_task_id=task.task_id,
                active_role_id=task.role_id,
                active_subagent_instance_id=instance_id,
                last_error=last_error or record.error_message or "Task stopped by user",
            )
            return True
        return False


def truncate_task_memory_result(result: str) -> str:
    normalized = " ".join(result.strip().split())
    if len(normalized) <= TASK_MEMORY_RESULT_EXCERPT_CHARS:
        return normalized
    return normalized[:TASK_MEMORY_RESULT_EXCERPT_CHARS].rstrip() + "..."


_truncate_task_memory_result = truncate_task_memory_result
