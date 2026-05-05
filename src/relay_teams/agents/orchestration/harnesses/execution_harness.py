# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, JsonValue

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.execution.subagent_runner import SubAgentRunner
from relay_teams.agents.execution.system_prompts import (
    PromptSkillInstruction,
    RuntimePromptBuilder,
    RuntimePromptSections,
)
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.instances.models import (
    AgentRuntimeRecord,
    RuntimeToolSnapshotEntry,
    RuntimeToolsSnapshot,
)
from relay_teams.agents.orchestration.harnesses.llm_harness import TaskLlmHarness
from relay_teams.agents.orchestration.harnesses.persistence_harness import (
    TaskPersistenceHarness,
)
from relay_teams.agents.orchestration.harnesses.prompt_harness import (
    PreparedRuntimeSnapshot,
    ProviderUserPromptContent,
    TaskPromptHarness,
)
from relay_teams.agents.orchestration.harnesses.tool_harness import TaskToolHarness
from relay_teams.agents.orchestration.task_contracts import TaskExecutionResult
from relay_teams.agents.tasks.artifact_repository import TaskArtifactRepository
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.events import EventEnvelope, EventType
from relay_teams.agents.tasks.models import TaskEnvelope
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.hooks import HookService
from relay_teams.logger import get_logger, log_event
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.media import MediaAssetService
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.reminders import ReminderDecision, SystemReminderService
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.memory.event_handler import MemoryEventHandler
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_models import (
    RunKind,
    RunThinkingConfig,
    RunTopologySnapshot,
    RuntimePromptConversationContext,
)
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.todo_service import TodoService
from relay_teams.skills.skill_models import SkillInstructionEntry
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.workspace import WorkspaceHandle, WorkspaceManager

from relay_teams.agents.instances.enums import InstanceStatus

LOGGER = get_logger(__name__)


class ExecutionConfig(NamedTuple):
    """Prepared execution configuration from the compute plane."""

    runner: SubAgentRunner
    role: RoleDefinition
    role_for_run: RoleDefinition
    workspace: WorkspaceHandle
    snapshot: tuple[tuple[str, str], ...]
    provider_system_prompt: str
    session_mode: str
    run_kind: RunKind
    runtime_system_prompt: str
    runtime_tools_json: str
    instance_record: AgentRuntimeRecord


class ExecutionHarness(BaseModel):
    """Compute-plane harness -- all sandbox and execution-side operations."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    role_registry: RoleRegistry
    task_repo: TaskRepository
    shared_store: SharedStateRepository
    event_bus: EventLog
    agent_repo: AgentInstanceRepository
    message_repo: MessageRepository
    approval_ticket_repo: ApprovalTicketRepository
    run_runtime_repo: RunRuntimeRepository
    run_event_hub: RunEventHub | None = None
    workspace_manager: WorkspaceManager
    prompt_builder: RuntimePromptBuilder
    provider_factory: Callable[..., object]
    tool_registry: object
    skill_registry: object
    skill_runtime_service: object | None = None
    mcp_registry: McpRegistry
    run_control_manager: object | None = None
    role_memory_service: RoleMemoryService | None = None
    memory_event_handler: MemoryEventHandler | None = None
    run_intent_repo: RunIntentRepository | None = None
    media_asset_service: MediaAssetService | None = None
    hook_service: HookService | None = None
    todo_service: TodoService | None = None
    reminder_service: SystemReminderService | None = None
    artifact_repo: TaskArtifactRepository | None = None
    runtime_role_resolver: RuntimeRoleResolver | None = None

    # ── Harness factories ─────────────────────────────────────────────

    def _tool_harness(self) -> TaskToolHarness:
        return TaskToolHarness.model_construct(
            role_registry=self.role_registry,
            tool_registry=self.tool_registry,
            skill_registry=self.skill_registry,
            mcp_registry=self.mcp_registry,
        )

    def _prompt_harness(self) -> TaskPromptHarness:
        return TaskPromptHarness.model_construct(
            role_registry=self.role_registry,
            shared_store=self.shared_store,
            message_repo=self.message_repo,
            workspace_manager=self.workspace_manager,
            prompt_builder=self.prompt_builder,
            tool_harness=self._tool_harness(),
            skill_runtime_service=self.skill_runtime_service,
            role_memory_service=self.role_memory_service,
            runtime_role_resolver=self.runtime_role_resolver,
            run_intent_repo=self.run_intent_repo,
            media_asset_service=self.media_asset_service,
        )

    def _full_persistence_harness(self) -> TaskPersistenceHarness:
        return TaskPersistenceHarness.model_construct(
            task_repo=self.task_repo,
            shared_store=self.shared_store,
            event_bus=self.event_bus,
            agent_repo=self.agent_repo,
            message_repo=self.message_repo,
            run_runtime_repo=self.run_runtime_repo,
            run_event_hub=self.run_event_hub,
            run_control_manager=self.run_control_manager,
            hook_service=self.hook_service,
        )

    def _llm_harness(self) -> TaskLlmHarness:
        return TaskLlmHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
            todo_service=self.todo_service,
            reminder_service=self.reminder_service,
            persistence_harness=self._full_persistence_harness(),
        )

    def _runtime_persistence_harness(self) -> TaskPersistenceHarness:
        return TaskPersistenceHarness.model_construct(
            task_repo=self.task_repo,
            run_runtime_repo=self.run_runtime_repo,
            run_control_manager=self.run_control_manager,
        )

    # ── Core compute-plane methods ────────────────────────────────────

    async def prepare_execution_config(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        user_prompt_override: str | None,
        workspace: WorkspaceHandle,
        instance_record: AgentRuntimeRecord,
    ) -> ExecutionConfig:
        """Resolve role, build runner, prepare prompts and return config."""
        if self.runtime_role_resolver is not None:
            role = await self.runtime_role_resolver.get_effective_role_async(
                run_id=task.trace_id,
                role_id=role_id,
            )
        else:
            role = self.role_registry.get(role_id)

        prompt_harness = self._prompt_harness()
        role_for_run = prompt_harness.role_with_memory(
            role=role,
            role_id=role_id,
            workspace_id=workspace.ref.workspace_id,
        )
        session_mode = "normal"
        run_kind = RunKind.CONVERSATION
        if self.run_intent_repo is not None:
            try:
                intent = self.run_intent_repo.get(
                    task.trace_id,
                    fallback_session_id=task.session_id,
                )
                session_mode = intent.session_mode.value
                run_kind = intent.run_kind
            except KeyError:
                LOGGER.debug(
                    "Missing run intent for trace_id=%s session_id=%s; using defaults",
                    task.trace_id,
                    task.session_id,
                )
        if task.parent_task_id is not None:
            session_mode = "normal"
            run_kind = RunKind.CONVERSATION

        runner = SubAgentRunner(
            role=role_for_run,
            prompt_builder=self.prompt_builder,
            provider=self.provider_factory(role_for_run, task.session_id),
            session_mode=session_mode,
            run_kind=run_kind,
        )
        snapshot = await prompt_harness.shared_state_snapshot_async(
            session_id=task.session_id,
            role_id=role_id,
            conversation_id=workspace.ref.conversation_id,
        )
        prepared = await prompt_harness.prepare_runtime_snapshot(
            role=role_for_run,
            task=task,
            working_directory=workspace.resolve_workdir(),
            worktree_root=workspace.scope_root,
            workspace=workspace,
            shared_state_snapshot=snapshot,
            objective=prompt_harness.resolve_turn_objective(
                task=task,
                user_prompt_override=user_prompt_override,
            ),
        )
        await prompt_harness.ensure_committed_task_prompt_async(
            role_id=role_id,
            workspace_id=workspace.ref.workspace_id,
            conversation_id=workspace.ref.conversation_id,
            instance_id=instance_id,
            task=task,
            user_prompt_text=prepared.user_prompt,
            user_prompt_override=user_prompt_override,
        )
        runtime_system_prompt = prompt_harness.compose_runtime_system_prompt(
            runtime_prompt_sections=prepared.prompt_sections,
            skill_instructions=prepared.skill_instructions,
        )
        provider_system_prompt = prompt_harness.compose_provider_system_prompt(
            runtime_prompt_sections=prepared.prompt_sections,
            skill_instructions=prepared.skill_instructions,
        )
        await self.agent_repo.update_runtime_snapshot_async(
            instance_id,
            runtime_system_prompt=runtime_system_prompt,
            runtime_tools_json=prepared.runtime_tools_json,
        )
        return ExecutionConfig(
            runner=runner,
            role=role,
            role_for_run=role_for_run,
            workspace=workspace,
            snapshot=snapshot,
            provider_system_prompt=provider_system_prompt,
            session_mode=session_mode,
            run_kind=run_kind,
            runtime_system_prompt=runtime_system_prompt,
            runtime_tools_json=prepared.runtime_tools_json,
            instance_record=instance_record,
        )

    async def run_llm_execution(
        self,
        config: ExecutionConfig,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
    ) -> str | TaskExecutionResult:
        """Run LLM execution through the completion guard."""
        return await self._llm_harness().run_with_completion_guard(
            runner=config.runner,
            task=task,
            instance_id=instance_id,
            role_id=role_id,
            workspace=config.workspace,
            conversation_id=config.workspace.ref.conversation_id,
            shared_state_snapshot=config.snapshot,
            system_prompt_override=config.provider_system_prompt,
        )

    async def handle_execution_result(
        self,
        *,
        result: str,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        workspace: WorkspaceHandle,
        instance_record: AgentRuntimeRecord,
    ) -> None:
        """Post-LLM result handling: hooks, status, events, memory."""
        persistence = self._full_persistence_harness()
        await persistence.execute_task_completed_hooks(
            task=task,
            instance_id=instance_id,
            role_id=role_id,
            output_text=result,
        )
        await self.task_repo.update_status_async(
            task.task_id, TaskStatus.COMPLETED, result=result
        )
        await self.agent_repo.mark_status_async(instance_id, InstanceStatus.COMPLETED)
        await persistence.mark_runtime_idle_after_success_async(
            run_id=task.trace_id,
            completed_task_id=task.task_id,
        )
        await self.event_bus.emit_async(
            EventEnvelope(
                event_type=EventType.TASK_COMPLETED,
                trace_id=task.trace_id,
                session_id=task.session_id,
                task_id=task.task_id,
                instance_id=instance_id,
                payload_json="{}",
            )
        )
        await persistence.record_memory_if_needed_async(
            role_id=role_id,
            workspace_id=workspace.ref.workspace_id,
            task=task,
            conversation_id=workspace.ref.conversation_id,
            instance_id=instance_id,
            lifecycle=instance_record.lifecycle.value,
            result=result,
        )
        # Memory bank lifecycle: record task result as a WORKING entry
        # and trigger run/session consolidation.  The handler is a
        # no-op when ``memory_event_handler`` is ``None`` (e.g. tests or
        # lightweight runtimes that skip the container wiring).
        memory_handler = self.memory_event_handler
        if memory_handler is not None:
            memory_handler.on_task_completed(
                workspace_id=workspace.ref.workspace_id,
                role_id=role_id,
                session_id=task.session_id,
                run_id=task.trace_id,
                task_id=task.task_id,
                objective=task.objective or "",
                result=result,
            )
        log_event(
            LOGGER,
            logging.DEBUG,
            event="task.execution.completed",
            message="Task execution completed",
            payload={
                "task_id": task.task_id,
                "instance_id": instance_id,
                "role_id": role_id,
            },
        )

    @staticmethod
    async def handle_execution_error(
        *,
        _error: Exception,
        task: TaskEnvelope,
        instance_id: str,
        error_message: str,
    ) -> TaskExecutionResult:
        """Handle execution error by creating error result."""
        log_event(
            LOGGER,
            logging.ERROR,
            event="execution_harness.execution_error",
            message="Execution harness caught error",
            payload={
                "task_id": task.task_id,
                "instance_id": instance_id,
                "error": error_message,
            },
        )
        return TaskExecutionResult(
            output="",
            error_message=f"Execution failed: {error_message}",
        )

    # ── LLM / completion guard ────────────────────────────────────────

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
        return await self._llm_harness().run_with_completion_guard(
            runner=runner,
            task=task,
            instance_id=instance_id,
            role_id=role_id,
            workspace=workspace,
            conversation_id=conversation_id,
            shared_state_snapshot=shared_state_snapshot,
            system_prompt_override=system_prompt_override,
        )

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
        return await self._llm_harness().run_agent_once(
            runner=runner,
            task=task,
            instance_id=instance_id,
            workspace=workspace,
            conversation_id=conversation_id,
            shared_state_snapshot=shared_state_snapshot,
            system_prompt_override=system_prompt_override,
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
        return await TaskLlmHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
            todo_service=self.todo_service,
            reminder_service=self.reminder_service,
            persistence_harness=TaskPersistenceHarness.model_construct(),
        ).evaluate_completion_guard_async(
            task=task,
            instance_id=instance_id,
            role_id=role_id,
            workspace=workspace,
            conversation_id=conversation_id,
            output_text=output_text,
        )

    async def thinking_for_run_async(self, run_id: str) -> RunThinkingConfig:
        return await TaskLlmHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
            persistence_harness=TaskPersistenceHarness.model_construct(),
        ).thinking_for_run_async(run_id)

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
        return TaskLlmHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
            todo_service=self.todo_service,
            reminder_service=self.reminder_service,
            persistence_harness=TaskPersistenceHarness.model_construct(),
        ).evaluate_completion_guard(
            task=task,
            instance_id=instance_id,
            role_id=role_id,
            workspace=workspace,
            conversation_id=conversation_id,
            output_text=output_text,
        )

    def thinking_for_run(self, run_id: str) -> RunThinkingConfig:
        return TaskLlmHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
            persistence_harness=TaskPersistenceHarness.model_construct(),
        ).thinking_for_run(run_id)

    # ── Hook execution ────────────────────────────────────────────────

    async def execute_task_completed_hooks(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        output_text: str,
    ) -> None:
        await TaskPersistenceHarness.model_construct(
            run_event_hub=self.run_event_hub,
            hook_service=self.hook_service,
        ).execute_task_completed_hooks(
            task=task,
            instance_id=instance_id,
            role_id=role_id,
            output_text=output_text,
        )

    # ── Persistence helpers ───────────────────────────────────────────

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
        return self._full_persistence_harness().complete_with_assistant_error(
            task=task,
            instance_id=instance_id,
            role_id=role_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            assistant_message=assistant_message,
            error_code=error_code,
            error_message=error_message,
            append_message=append_message,
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
        return (
            await self._full_persistence_harness().complete_with_assistant_error_async(
                task=task,
                instance_id=instance_id,
                role_id=role_id,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                assistant_message=assistant_message,
                error_code=error_code,
                error_message=error_message,
                append_message=append_message,
            )
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
        TaskPersistenceHarness.model_construct(
            shared_store=self.shared_store,
        ).record_memory_if_needed(
            role_id=role_id,
            workspace_id=workspace_id,
            task=task,
            conversation_id=conversation_id,
            instance_id=instance_id,
            lifecycle=lifecycle,
            result=result,
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
        await TaskPersistenceHarness.model_construct(
            shared_store=self.shared_store,
        ).record_memory_if_needed_async(
            role_id=role_id,
            workspace_id=workspace_id,
            task=task,
            conversation_id=conversation_id,
            instance_id=instance_id,
            lifecycle=lifecycle,
            result=result,
        )

    def mark_runtime_idle_after_success(
        self,
        *,
        run_id: str,
        completed_task_id: str,
    ) -> None:
        self._runtime_persistence_harness().mark_runtime_idle_after_success(
            run_id=run_id,
            completed_task_id=completed_task_id,
        )

    async def mark_runtime_idle_after_success_async(
        self,
        *,
        run_id: str,
        completed_task_id: str,
    ) -> None:
        await TaskPersistenceHarness.model_construct(
            task_repo=self.task_repo,
            run_runtime_repo=self.run_runtime_repo,
            run_control_manager=self.run_control_manager,
        ).mark_runtime_idle_after_success_async(
            run_id=run_id,
            completed_task_id=completed_task_id,
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
        self._runtime_persistence_harness().mark_runtime_after_terminal_task_update(
            run_id=run_id,
            terminal_task_id=terminal_task_id,
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
        await TaskPersistenceHarness.model_construct(
            task_repo=self.task_repo,
            run_runtime_repo=self.run_runtime_repo,
            run_control_manager=self.run_control_manager,
        ).mark_runtime_after_terminal_task_update_async(
            run_id=run_id,
            terminal_task_id=terminal_task_id,
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
        return self._runtime_persistence_harness().promote_running_runtime_lane(
            run_id=run_id,
            terminal_task_id=terminal_task_id,
            last_error=last_error,
        )

    async def promote_running_runtime_lane_async(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        last_error: str | None,
    ) -> bool:
        return await TaskPersistenceHarness.model_construct(
            task_repo=self.task_repo,
            run_runtime_repo=self.run_runtime_repo,
            run_control_manager=self.run_control_manager,
        ).promote_running_runtime_lane_async(
            run_id=run_id,
            terminal_task_id=terminal_task_id,
            last_error=last_error,
        )

    def promote_paused_runtime_lane(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        last_error: str | None,
    ) -> bool:
        return self._runtime_persistence_harness().promote_paused_runtime_lane(
            run_id=run_id,
            terminal_task_id=terminal_task_id,
            last_error=last_error,
        )

    async def promote_paused_runtime_lane_async(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        last_error: str | None,
    ) -> bool:
        return await TaskPersistenceHarness.model_construct(
            task_repo=self.task_repo,
            run_runtime_repo=self.run_runtime_repo,
            run_control_manager=self.run_control_manager,
        ).promote_paused_runtime_lane_async(
            run_id=run_id,
            terminal_task_id=terminal_task_id,
            last_error=last_error,
        )

    # ── Prompt helpers ────────────────────────────────────────────────

    def topology_for_run(self, run_id: str) -> RunTopologySnapshot | None:
        return TaskPromptHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
        ).topology_for_run(run_id)

    def conversation_context_for_run(
        self, run_id: str
    ) -> RuntimePromptConversationContext | None:
        return TaskPromptHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
        ).conversation_context_for_run(run_id)

    async def topology_for_run_async(self, run_id: str) -> RunTopologySnapshot | None:
        return await TaskPromptHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
        ).topology_for_run_async(run_id)

    async def conversation_context_for_run_async(
        self, run_id: str
    ) -> RuntimePromptConversationContext | None:
        return await TaskPromptHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
        ).conversation_context_for_run_async(run_id)

    def role_with_memory(
        self, *, role: RoleDefinition, role_id: str, workspace_id: str
    ) -> RoleDefinition:
        return TaskPromptHarness.model_construct(
            role_registry=self.role_registry,
            role_memory_service=self.role_memory_service,
        ).role_with_memory(role=role, role_id=role_id, workspace_id=workspace_id)

    async def prepare_runtime_snapshot(
        self,
        *,
        role: RoleDefinition,
        task: TaskEnvelope,
        working_directory: Path | None,
        worktree_root: Path | None,
        workspace: WorkspaceHandle | None,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        objective: str,
    ) -> PreparedRuntimeSnapshot:
        return await self._prompt_harness().prepare_runtime_snapshot(
            role=role,
            task=task,
            working_directory=working_directory,
            worktree_root=worktree_root,
            workspace=workspace,
            shared_state_snapshot=shared_state_snapshot,
            objective=objective,
        )

    @staticmethod
    def compose_runtime_system_prompt(
        *,
        runtime_prompt_sections: RuntimePromptSections,
        skill_instructions: tuple[PromptSkillInstruction, ...],
    ) -> str:
        return TaskPromptHarness.model_construct().compose_runtime_system_prompt(
            runtime_prompt_sections=runtime_prompt_sections,
            skill_instructions=skill_instructions,
        )

    @staticmethod
    def compose_provider_system_prompt(
        *,
        runtime_prompt_sections: RuntimePromptSections,
        skill_instructions: tuple[PromptSkillInstruction, ...],
    ) -> str:
        return TaskPromptHarness.model_construct().compose_provider_system_prompt(
            runtime_prompt_sections=runtime_prompt_sections,
            skill_instructions=skill_instructions,
        )

    def shared_state_snapshot(
        self, *, session_id: str, role_id: str, conversation_id: str
    ) -> tuple[tuple[str, str], ...]:
        return TaskPromptHarness.model_construct(
            shared_store=self.shared_store,
        ).shared_state_snapshot(
            session_id=session_id,
            role_id=role_id,
            conversation_id=conversation_id,
        )

    async def shared_state_snapshot_async(
        self, *, session_id: str, role_id: str, conversation_id: str
    ) -> tuple[tuple[str, str], ...]:
        return await TaskPromptHarness.model_construct(
            shared_store=self.shared_store,
        ).shared_state_snapshot_async(
            session_id=session_id,
            role_id=role_id,
            conversation_id=conversation_id,
        )

    def ensure_committed_task_prompt(
        self,
        *,
        role_id: str,
        workspace_id: str,
        conversation_id: str,
        instance_id: str,
        task: TaskEnvelope,
        user_prompt_text: str,
        user_prompt_override: str | None,
    ) -> None:
        TaskPromptHarness.model_construct(
            message_repo=self.message_repo,
            run_intent_repo=self.run_intent_repo,
            media_asset_service=self.media_asset_service,
        ).ensure_committed_task_prompt(
            role_id=role_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            instance_id=instance_id,
            task=task,
            user_prompt_text=user_prompt_text,
            user_prompt_override=user_prompt_override,
        )

    async def ensure_committed_task_prompt_async(
        self,
        *,
        role_id: str,
        workspace_id: str,
        conversation_id: str,
        instance_id: str,
        task: TaskEnvelope,
        user_prompt_text: str,
        user_prompt_override: str | None,
    ) -> None:
        await TaskPromptHarness.model_construct(
            message_repo=self.message_repo,
            run_intent_repo=self.run_intent_repo,
            media_asset_service=self.media_asset_service,
        ).ensure_committed_task_prompt_async(
            role_id=role_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            instance_id=instance_id,
            task=task,
            user_prompt_text=user_prompt_text,
            user_prompt_override=user_prompt_override,
        )

    def build_user_prompt(
        self,
        *,
        role: RoleDefinition,
        objective: str,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        conversation_context: RuntimePromptConversationContext | None,
        orchestration_prompt: str,
        skill_names: tuple[str, ...] | None = None,
    ) -> tuple[str, tuple[PromptSkillInstruction, ...]]:
        return TaskPromptHarness.model_construct(
            skill_runtime_service=self.skill_runtime_service,
        ).build_user_prompt(
            role=role,
            objective=objective,
            shared_state_snapshot=shared_state_snapshot,
            conversation_context=conversation_context,
            orchestration_prompt=orchestration_prompt,
            skill_names=skill_names,
        )

    @staticmethod
    def to_prompt_skill_instructions(
        entries: tuple[SkillInstructionEntry, ...],
    ) -> tuple[PromptSkillInstruction, ...]:
        return TaskPromptHarness.model_construct().to_prompt_skill_instructions(entries)

    @staticmethod
    def merge_provider_prompt_content(
        *,
        provider_content: ProviderUserPromptContent,
        user_prompt_text: str,
    ) -> ProviderUserPromptContent:
        return TaskPromptHarness.model_construct().merge_provider_prompt_content(
            provider_content=provider_content,
            user_prompt_text=user_prompt_text,
        )

    @staticmethod
    def user_prompt_skill_appendix(user_prompt_text: str) -> str:
        return TaskPromptHarness.model_construct().user_prompt_skill_appendix(
            user_prompt_text
        )

    @staticmethod
    def resolve_turn_objective(
        *, task: TaskEnvelope, user_prompt_override: str | None
    ) -> str:
        return TaskPromptHarness.model_construct().resolve_turn_objective(
            task=task, user_prompt_override=user_prompt_override
        )

    # ── Tool helpers ──────────────────────────────────────────────────

    async def build_runtime_tools_snapshot(
        self, role: RoleDefinition, task: TaskEnvelope | None = None
    ) -> RuntimeToolsSnapshot:
        return await self._tool_harness().build_runtime_tools_snapshot(
            role=role, task=task
        )

    @staticmethod
    def tool_entry_from_definition(
        *,
        source: Literal["local", "skill", "mcp"],
        name: str,
        description: str,
        kind: Literal["function", "output", "external", "unapproved"],
        strict: bool | None,
        sequential: bool,
        parameters_json_schema: Mapping[str, JsonValue],
        server_name: str = "",
    ) -> RuntimeToolSnapshotEntry:
        return TaskToolHarness.model_construct().tool_entry_from_definition(
            source=source,
            name=name,
            description=description,
            kind=kind,
            strict=strict,
            sequential=sequential,
            parameters_json_schema=parameters_json_schema,
            server_name=server_name,
        )

    @staticmethod
    def normalize_tool_kind(
        kind: str,
    ) -> Literal["function", "output", "external", "unapproved"]:
        return TaskToolHarness.model_construct().normalize_tool_kind(kind)
