# -*- coding: utf-8 -*-
from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict

from relay_teams.agents.execution.subagent_runner import SubAgentRunner
from relay_teams.agents.orchestration.harnesses.persistence_harness import (
    TaskPersistenceHarness,
)
from relay_teams.agents.orchestration.task_contracts import TaskExecutionResult
from relay_teams.agents.tasks.models import TaskEnvelope
from relay_teams.logger import get_logger, log_event
from relay_teams.reminders import (
    CompletionAttemptObservation,
    IncompleteTodoItem,
    ReminderDecision,
    SystemReminderService,
)
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.sessions.runs.todo_models import TodoItem, TodoStatus
from relay_teams.sessions.runs.todo_service import TodoService
from relay_teams.sessions.runs.assistant_errors import build_assistant_error_message
from relay_teams.workspace import WorkspaceHandle

LOGGER = get_logger(__name__)


class TaskLlmHarness(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    run_intent_repo: RunIntentRepository | None = None
    todo_service: TodoService | None = None
    reminder_service: SystemReminderService | None = None
    persistence_harness: TaskPersistenceHarness

    async def run_with_completion_guard(
        self,
        *,
        runner: SubAgentRunner,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        workspace: WorkspaceHandle,
        conversation_id: str,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        system_prompt_override: str,
    ) -> str | TaskExecutionResult:
        result = await self.run_agent_once(
            runner=runner,
            task=task,
            instance_id=instance_id,
            workspace=workspace,
            conversation_id=conversation_id,
            shared_state_snapshot=shared_state_snapshot,
            system_prompt_override=system_prompt_override,
        )
        while True:
            decision = await self.evaluate_completion_guard_async(
                task=task,
                instance_id=instance_id,
                role_id=role_id,
                workspace=workspace,
                conversation_id=conversation_id,
                output_text=result,
            )
            if not decision.issue:
                return result
            if decision.retry_completion:
                log_event(
                    LOGGER,
                    logging.INFO,
                    event="task.execution.completion_reminder_retry",
                    message="Retrying task after system reminder blocked completion",
                    payload={
                        "task_id": task.task_id,
                        "instance_id": instance_id,
                        "role_id": role_id,
                        "reason": decision.reason,
                    },
                )
                result = await self.run_agent_once(
                    runner=runner,
                    task=task,
                    instance_id=instance_id,
                    workspace=workspace,
                    conversation_id=conversation_id,
                    shared_state_snapshot=shared_state_snapshot,
                    system_prompt_override=system_prompt_override,
                )
                continue
            if decision.fail_completion:
                assistant_message = build_assistant_error_message(
                    error_code="incomplete_todos",
                    error_message=decision.content,
                )
                return (
                    await self.persistence_harness.complete_with_assistant_error_async(
                        task=task,
                        instance_id=instance_id,
                        role_id=role_id,
                        conversation_id=conversation_id,
                        workspace_id=workspace.ref.workspace_id,
                        assistant_message=assistant_message,
                        error_code="incomplete_todos",
                        error_message=decision.content,
                    )
                )
            return result

    async def run_agent_once(
        self,
        *,
        runner: SubAgentRunner,
        task: TaskEnvelope,
        instance_id: str,
        workspace: WorkspaceHandle,
        conversation_id: str,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        system_prompt_override: str,
    ) -> str:
        return await runner.run(
            task=task,
            instance_id=instance_id,
            workspace_id=workspace.ref.workspace_id,
            working_directory=workspace.resolve_workdir(),
            conversation_id=conversation_id,
            shared_state_snapshot=shared_state_snapshot,
            thinking=await self.thinking_for_run_async(task.trace_id),
            system_prompt_override=system_prompt_override,
            user_prompt=None,
        )

    async def evaluate_completion_guard_async(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        workspace: WorkspaceHandle,
        conversation_id: str,
        output_text: str,
    ) -> ReminderDecision:
        if task.parent_task_id is not None:
            if self.todo_service is not None:
                await self._finalize_subtask_todos_async(task, instance_id)
            return ReminderDecision()
        if self.reminder_service is None or self.todo_service is None:
            return ReminderDecision()
        snapshot = await self.todo_service.get_for_run_async(
            run_id=task.trace_id,
            session_id=task.session_id,
        )
        incomplete = tuple(
            IncompleteTodoItem(content=item.content, status=item.status.value)
            for item in snapshot.items
            if item.status != TodoStatus.COMPLETED
        )
        return await self.reminder_service.evaluate_completion_attempt_async(
            CompletionAttemptObservation(
                session_id=task.session_id,
                run_id=task.trace_id,
                trace_id=task.trace_id,
                task_id=task.task_id,
                instance_id=instance_id,
                role_id=role_id,
                workspace_id=workspace.ref.workspace_id,
                conversation_id=conversation_id,
                output_text=output_text,
                incomplete_todos=incomplete,
            )
        )

    async def thinking_for_run_async(self, run_id: str) -> RunThinkingConfig:
        if self.run_intent_repo is None:
            return RunThinkingConfig()
        try:
            return (await self.run_intent_repo.get_async(run_id)).thinking
        except KeyError:
            return RunThinkingConfig()

    def evaluate_completion_guard(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        workspace: WorkspaceHandle,
        conversation_id: str,
        output_text: str,
    ) -> ReminderDecision:
        if task.parent_task_id is not None:
            if self.todo_service is not None:
                self._finalize_subtask_todos(task, instance_id)
            return ReminderDecision()
        if self.reminder_service is None or self.todo_service is None:
            return ReminderDecision()
        snapshot = self.todo_service.get_for_run(
            run_id=task.trace_id,
            session_id=task.session_id,
        )
        incomplete = tuple(
            IncompleteTodoItem(content=item.content, status=item.status.value)
            for item in snapshot.items
            if item.status != TodoStatus.COMPLETED
        )
        return self.reminder_service.evaluate_completion_attempt(
            CompletionAttemptObservation(
                session_id=task.session_id,
                run_id=task.trace_id,
                trace_id=task.trace_id,
                task_id=task.task_id,
                instance_id=instance_id,
                role_id=role_id,
                workspace_id=workspace.ref.workspace_id,
                conversation_id=conversation_id,
                output_text=output_text,
                incomplete_todos=incomplete,
            )
        )

    def thinking_for_run(self, run_id: str) -> RunThinkingConfig:
        if self.run_intent_repo is None:
            return RunThinkingConfig()
        try:
            return self.run_intent_repo.get(run_id).thinking
        except KeyError:
            return RunThinkingConfig()

    def _finalize_subtask_todos(self, task: TaskEnvelope, instance_id: str) -> None:
        """Finalize sub-task-scoped todos.

        Only marks ``IN_PROGRESS`` items as ``COMPLETED``.  ``PENDING``
        items are left untouched because concurrent sub-tasks may own
        them.  This avoids corrupting shared todo state when parallel
        delegation is active.
        """
        assert self.todo_service is not None  # guarded by caller
        snapshot = self.todo_service.get_for_run(
            run_id=task.trace_id,
            session_id=task.session_id,
        )
        in_progress_indices = tuple(
            idx
            for idx, item in enumerate(snapshot.items)
            if item.status == TodoStatus.IN_PROGRESS
        )
        if not in_progress_indices:
            return
        finalized = tuple(
            TodoItem(content=item.content, status=TodoStatus.COMPLETED)
            if idx in in_progress_indices
            else item
            for idx, item in enumerate(snapshot.items)
        )
        self.todo_service.replace_for_run(
            run_id=task.trace_id,
            session_id=task.session_id,
            items=finalized,
            updated_by_instance_id=instance_id,
        )
        log_event(
            LOGGER,
            logging.INFO,
            event="task.execution.subtask_todos_finalized",
            message="Finalized in-progress sub-task todos as completed",
            payload={
                "task_id": task.task_id,
                "trace_id": task.trace_id,
                "finalized_count": len(in_progress_indices),
            },
        )

    async def _finalize_subtask_todos_async(
        self, task: TaskEnvelope, instance_id: str
    ) -> None:
        """Async counterpart of :meth:`_finalize_subtask_todos`."""
        assert self.todo_service is not None  # guarded by caller
        snapshot = await self.todo_service.get_for_run_async(
            run_id=task.trace_id,
            session_id=task.session_id,
        )
        in_progress_indices = tuple(
            idx
            for idx, item in enumerate(snapshot.items)
            if item.status == TodoStatus.IN_PROGRESS
        )
        if not in_progress_indices:
            return
        finalized = tuple(
            TodoItem(content=item.content, status=TodoStatus.COMPLETED)
            if idx in in_progress_indices
            else item
            for idx, item in enumerate(snapshot.items)
        )
        await self.todo_service.replace_for_run_async(
            run_id=task.trace_id,
            session_id=task.session_id,
            items=finalized,
            updated_by_instance_id=instance_id,
        )
        log_event(
            LOGGER,
            logging.INFO,
            event="task.execution.subtask_todos_finalized",
            message="Finalized in-progress sub-task todos as completed",
            payload={
                "task_id": task.task_id,
                "trace_id": task.trace_id,
                "finalized_count": len(in_progress_indices),
            },
        )
