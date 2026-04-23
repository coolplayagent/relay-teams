# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai.messages import ModelRequest, UserContent, UserPromptPart

from relay_teams.agents.execution.subagent_runner import SubAgentRunner
from relay_teams.agents.execution.prompt_instruction_state import (
    record_prompt_instruction_paths_loaded,
)
from relay_teams.agents.execution.system_prompts import (
    PromptBuildInput,
    PromptSkillInstruction,
    RuntimePromptBuilder,
    RuntimePromptSections,
    build_workspace_ssh_profile_prompt_metadata,
    compose_provider_system_prompt,
    compose_runtime_system_prompt,
)
from relay_teams.agents.execution.user_prompts import (
    UserPromptBuildInput,
    build_user_prompt,
)
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.models import (
    RuntimeToolSnapshotEntry,
    RuntimeToolsSnapshot,
)
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.events import EventEnvelope, EventType
from relay_teams.agents.tasks.models import TaskEnvelope
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.logger import get_logger, log_event
from relay_teams.media import MediaAssetService, merge_user_prompt_content
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.persistence.scope_models import ScopeRef, ScopeType
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.roles.memory_injection import build_role_with_memory
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_models import (
    RuntimePromptConversationContext,
    RunThinkingConfig,
    RunTopologySnapshot,
)
from relay_teams.sessions.runs.recoverable_pause import RecoverableRunPauseError
from relay_teams.sessions.runs.assistant_errors import (
    AssistantRunError,
    RunCompletionReason,
    build_assistant_error_message,
    build_assistant_error_response,
)
from relay_teams.agents.orchestration.task_contracts import TaskExecutionResult
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.hooks import HookEventName, HookService, TaskCompletedInput
from relay_teams.skills.skill_models import SkillInstructionEntry
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.skills.skill_routing_service import SkillRuntimeService
from relay_teams.tools.registry.registry import ToolRegistry, ToolResolutionContext
from relay_teams.tools.runtime_activation import merge_active_tools
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.workspace import WorkspaceHandle, WorkspaceManager

LOGGER = get_logger(__name__)
ProviderUserPromptContent = str | tuple[UserContent, ...]


class TaskExecutionService(BaseModel):
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
    provider_factory: Callable[[RoleDefinition, str | None], object]
    tool_registry: object
    skill_registry: object
    skill_runtime_service: object | None = None
    mcp_registry: McpRegistry
    injection_manager: RunInjectionManager | None = None
    run_control_manager: RunControlManager | None = None
    role_memory_service: RoleMemoryService | None = None
    runtime_role_resolver: RuntimeRoleResolver | None = None
    run_intent_repo: RunIntentRepository | None = None
    media_asset_service: MediaAssetService | None = None
    hook_service: HookService | None = None

    async def execute(
        self,
        *,
        instance_id: str,
        role_id: str,
        task: TaskEnvelope,
        user_prompt_override: str | None = None,
    ) -> TaskExecutionResult:
        worker = asyncio.create_task(
            self._execute_inner(
                instance_id=instance_id,
                role_id=role_id,
                task=task,
                user_prompt_override=user_prompt_override,
            )
        )
        if self.run_control_manager is not None:
            self.run_control_manager.register_instance_task(
                run_id=task.trace_id,
                session_id=task.session_id,
                instance_id=instance_id,
                role_id=role_id,
                task_id=task.task_id,
                task=worker,
            )
        try:
            return await worker
        finally:
            if self.run_control_manager is not None:
                self.run_control_manager.unregister_instance_task(
                    run_id=task.trace_id,
                    instance_id=instance_id,
                )

    async def _execute_inner(
        self,
        *,
        instance_id: str,
        role_id: str,
        task: TaskEnvelope,
        user_prompt_override: str | None,
    ) -> TaskExecutionResult:
        is_coordinator = self.role_registry.is_coordinator_role(role_id)
        log_event(
            LOGGER,
            logging.DEBUG,
            event="task.execution.started",
            message="Task execution started",
            payload={
                "task_id": task.task_id,
                "instance_id": instance_id,
                "role_id": role_id,
            },
        )
        _ = self.agent_repo.mark_status(instance_id, InstanceStatus.RUNNING)
        _ = self.task_repo.update_status(task.task_id, TaskStatus.RUNNING)
        self.run_runtime_repo.ensure(
            run_id=task.trace_id,
            session_id=task.session_id,
            root_task_id=task.parent_task_id or task.task_id,
            status=RunRuntimeStatus.RUNNING,
            phase=(
                RunRuntimePhase.COORDINATOR_RUNNING
                if is_coordinator
                else RunRuntimePhase.SUBAGENT_RUNNING
            ),
        )
        self.run_runtime_repo.update(
            task.trace_id,
            status=RunRuntimeStatus.RUNNING,
            phase=(
                RunRuntimePhase.COORDINATOR_RUNNING
                if is_coordinator
                else RunRuntimePhase.SUBAGENT_RUNNING
            ),
            active_instance_id=instance_id,
            active_task_id=task.task_id,
            active_role_id=role_id,
            active_subagent_instance_id=(None if is_coordinator else instance_id),
            last_error=None,
        )
        self.event_bus.emit(
            EventEnvelope(
                event_type=EventType.TASK_STARTED,
                trace_id=task.trace_id,
                session_id=task.session_id,
                task_id=task.task_id,
                instance_id=instance_id,
                payload_json="{}",
            )
        )

        if self.runtime_role_resolver is not None:
            role = self.runtime_role_resolver.get_effective_role(
                run_id=task.trace_id,
                role_id=role_id,
            )
        else:
            role = self.role_registry.get(role_id)
        instance_record = self.agent_repo.get_instance(instance_id)
        workspace = self.workspace_manager.resolve(
            session_id=task.session_id,
            role_id=role_id,
            instance_id=instance_id,
            workspace_id=instance_record.workspace_id,
            conversation_id=instance_record.conversation_id,
        )
        role_for_run = self._role_with_memory(
            role=role,
            role_id=role_id,
            workspace_id=workspace.ref.workspace_id,
        )
        runner = SubAgentRunner(
            role=role_for_run,
            prompt_builder=self.prompt_builder,
            provider=self.provider_factory(role_for_run, task.session_id),
        )
        snapshot = self._shared_state_snapshot(
            session_id=task.session_id,
            role_id=role_id,
            conversation_id=workspace.ref.conversation_id,
        )
        try:
            prepared_runtime_snapshot = await self._prepare_runtime_snapshot(
                role=role_for_run,
                task=task,
                working_directory=workspace.resolve_workdir(),
                worktree_root=workspace.scope_root,
                workspace=workspace,
                shared_state_snapshot=snapshot,
                objective=self._resolve_turn_objective(
                    task=task,
                    user_prompt_override=user_prompt_override,
                ),
                existing_runtime_active_tools_json=(
                    instance_record.runtime_active_tools_json
                ),
            )
            self._ensure_committed_task_prompt(
                role_id=role_id,
                workspace_id=workspace.ref.workspace_id,
                conversation_id=workspace.ref.conversation_id,
                instance_id=instance_id,
                task=task,
                user_prompt_text=prepared_runtime_snapshot.user_prompt,
                user_prompt_override=user_prompt_override,
            )
            runtime_prompt_sections = prepared_runtime_snapshot.prompt_sections
            runtime_tools_json = prepared_runtime_snapshot.runtime_tools_json
            runtime_system_prompt = self._compose_runtime_system_prompt(
                role=role_for_run,
                runtime_prompt_sections=runtime_prompt_sections,
                skill_instructions=prepared_runtime_snapshot.skill_instructions,
            )
            self.agent_repo.update_runtime_snapshot(
                instance_id,
                runtime_system_prompt=runtime_system_prompt,
                runtime_tools_json=runtime_tools_json,
                runtime_active_tools_json=(
                    prepared_runtime_snapshot.runtime_active_tools_json
                ),
            )
            provider_system_prompt = self._compose_provider_system_prompt(
                role=role_for_run,
                runtime_prompt_sections=runtime_prompt_sections,
                skill_instructions=prepared_runtime_snapshot.skill_instructions,
            )
            result = await runner.run(
                task=task,
                instance_id=instance_id,
                workspace_id=workspace.ref.workspace_id,
                working_directory=workspace.resolve_workdir(),
                conversation_id=workspace.ref.conversation_id,
                shared_state_snapshot=snapshot,
                thinking=self._thinking_for_run(task.trace_id),
                system_prompt_override=provider_system_prompt,
                user_prompt=None,
            )
            self.task_repo.update_status(
                task.task_id, TaskStatus.COMPLETED, result=result
            )
            _ = self.agent_repo.mark_status(instance_id, InstanceStatus.COMPLETED)
            self.run_runtime_repo.update(
                task.trace_id,
                status=RunRuntimeStatus.RUNNING,
                phase=RunRuntimePhase.IDLE,
                active_instance_id=None,
                active_task_id=None,
                active_role_id=None,
                active_subagent_instance_id=None,
                last_error=None,
            )
            self.event_bus.emit(
                EventEnvelope(
                    event_type=EventType.TASK_COMPLETED,
                    trace_id=task.trace_id,
                    session_id=task.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json="{}",
                )
            )
            await self._execute_task_completed_hooks(
                task=task,
                instance_id=instance_id,
                role_id=role_id,
                output_text=result,
            )
            self._record_memory_if_needed(
                role_id=role_id,
                workspace_id=workspace.ref.workspace_id,
                task=task,
                conversation_id=workspace.ref.conversation_id,
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
            return TaskExecutionResult(output=result)
        except asyncio.CancelledError:
            paused_subagent = False
            if self.run_control_manager is not None:
                run_stop_requested = self.run_control_manager.is_run_stop_requested(
                    task.trace_id
                )
                subagent_stop_requested = (
                    self.run_control_manager.is_subagent_stop_requested(
                        run_id=task.trace_id,
                        instance_id=instance_id,
                    )
                )
                stopped = self.run_control_manager.handle_instance_cancelled(
                    task=task,
                    instance_id=instance_id,
                )
                paused_subagent = (
                    stopped
                    and not is_coordinator
                    and subagent_stop_requested
                    and not run_stop_requested
                )
            else:
                stopped = False
                self.task_repo.update_status(
                    task.task_id,
                    TaskStatus.FAILED,
                    error_message="Task cancelled",
                )
                self.agent_repo.mark_status(instance_id, InstanceStatus.FAILED)
                self.event_bus.emit(
                    EventEnvelope(
                        event_type=EventType.TASK_FAILED,
                        trace_id=task.trace_id,
                        session_id=task.session_id,
                        task_id=task.task_id,
                        instance_id=instance_id,
                        payload_json="{}",
                    )
                )
            self.run_runtime_repo.update(
                task.trace_id,
                status=(
                    RunRuntimeStatus.STOPPED if stopped else RunRuntimeStatus.FAILED
                ),
                phase=(
                    RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP
                    if paused_subagent
                    else RunRuntimePhase.IDLE
                ),
                active_instance_id=None,
                active_task_id=task.task_id if paused_subagent else None,
                active_role_id=role_id if paused_subagent else None,
                active_subagent_instance_id=(instance_id if paused_subagent else None),
                last_error="Task stopped by user" if stopped else "Task cancelled",
            )
            log_event(
                LOGGER,
                logging.WARNING if stopped else logging.ERROR,
                event="task.execution.stopped"
                if stopped
                else "task.execution.cancelled",
                message="Task execution interrupted",
                payload={
                    "task_id": task.task_id,
                    "instance_id": instance_id,
                    "role_id": role_id,
                    "paused_subagent": paused_subagent,
                },
            )
            raise
        except AssistantRunError as exc:
            return self._complete_with_assistant_error(
                task=task,
                instance_id=instance_id,
                role_id=role_id,
                conversation_id=workspace.ref.conversation_id,
                workspace_id=workspace.ref.workspace_id,
                assistant_message=exc.payload.assistant_message,
                error_code=exc.payload.error_code,
                error_message=exc.payload.error_message,
                append_message=False,
            )
        except RecoverableRunPauseError as exc:
            payload = exc.payload
            _ = self.task_repo.update_status(
                task.task_id,
                TaskStatus.STOPPED,
                assigned_instance_id=instance_id,
                error_message=payload.error_message,
            )
            _ = self.agent_repo.mark_status(instance_id, InstanceStatus.IDLE)
            self.run_runtime_repo.update(
                task.trace_id,
                status=RunRuntimeStatus.PAUSED,
                phase=RunRuntimePhase.AWAITING_RECOVERY,
                active_instance_id=payload.instance_id,
                active_task_id=payload.task_id,
                active_role_id=payload.role_id,
                active_subagent_instance_id=(
                    None if is_coordinator else payload.instance_id
                ),
                last_error=payload.error_message,
            )
            self.event_bus.emit(
                EventEnvelope(
                    event_type=EventType.TASK_STOPPED,
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
                event="task.execution.paused",
                message="Task execution paused after recoverable model interruption",
                payload={
                    "task_id": task.task_id,
                    "instance_id": instance_id,
                    "role_id": role_id,
                    "error_code": payload.error_code,
                },
            )
            raise
        except TimeoutError:
            return self._complete_with_assistant_error(
                task=task,
                instance_id=instance_id,
                role_id=role_id,
                conversation_id=workspace.ref.conversation_id,
                workspace_id=workspace.ref.workspace_id,
                assistant_message=build_assistant_error_message(
                    error_code="task_timeout",
                    error_message="Task timeout",
                ),
                error_code="task_timeout",
                error_message="Task timeout",
            )
        except Exception as exc:
            return self._complete_with_assistant_error(
                task=task,
                instance_id=instance_id,
                role_id=role_id,
                conversation_id=workspace.ref.conversation_id,
                workspace_id=workspace.ref.workspace_id,
                assistant_message=build_assistant_error_message(
                    error_code="internal_execution_error",
                    error_message=str(exc),
                ),
                error_code="internal_execution_error",
                error_message=str(exc),
            )

    def _thinking_for_run(self, run_id: str) -> RunThinkingConfig:
        if self.run_intent_repo is None:
            return RunThinkingConfig()
        try:
            return self.run_intent_repo.get(run_id).thinking
        except KeyError:
            return RunThinkingConfig()

    async def _execute_task_completed_hooks(
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

    def _complete_with_assistant_error(
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
        if append_message:
            self.message_repo.prune_conversation_history_to_safe_boundary(
                conversation_id
            )
            self.message_repo.append(
                session_id=task.session_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_role_id=role_id,
                instance_id=instance_id,
                task_id=task.task_id,
                trace_id=task.trace_id,
                messages=[build_assistant_error_response(assistant_message)],
            )
        self.task_repo.update_status(
            task.task_id,
            TaskStatus.FAILED,
            assigned_instance_id=instance_id,
            result=assistant_message,
            error_message=error_message or assistant_message,
        )
        _ = self.agent_repo.mark_status(instance_id, InstanceStatus.FAILED)
        self.run_runtime_repo.update(
            task.trace_id,
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.IDLE,
            active_instance_id=None,
            active_task_id=None,
            active_role_id=None,
            active_subagent_instance_id=None,
            last_error=error_message or assistant_message,
        )
        self.event_bus.emit(
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

    def _topology_for_run(self, run_id: str) -> RunTopologySnapshot | None:
        if self.run_intent_repo is None:
            return None
        try:
            return self.run_intent_repo.get(run_id).topology
        except KeyError:
            return None

    def _conversation_context_for_run(
        self,
        run_id: str,
    ) -> RuntimePromptConversationContext | None:
        if self.run_intent_repo is None:
            return None
        try:
            return self.run_intent_repo.get(run_id).conversation_context
        except KeyError:
            return None

    def _role_with_memory(
        self,
        *,
        role: RoleDefinition,
        role_id: str,
        workspace_id: str,
    ) -> RoleDefinition:
        return build_role_with_memory(
            role_registry=self.role_registry,
            role_memory_service=self.role_memory_service,
            role=role,
            role_id=role_id,
            workspace_id=workspace_id,
        )

    async def _prepare_runtime_snapshot(
        self,
        *,
        role: RoleDefinition,
        task: TaskEnvelope,
        working_directory: Path | None,
        worktree_root: Path | None,
        workspace: WorkspaceHandle | None,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        objective: str,
        existing_runtime_active_tools_json: str = "",
    ) -> PreparedRuntimeSnapshot:
        topology = self._topology_for_run(task.trace_id)
        conversation_context = self._conversation_context_for_run(task.trace_id)
        runtime_tools = await self._build_runtime_tools_snapshot(role=role, task=task)
        prompt_sections = await self.prompt_builder.build_sections(
            PromptBuildInput(
                role=role,
                task=task,
                topology=topology,
                shared_state_snapshot=shared_state_snapshot,
                working_directory=working_directory,
                worktree_root=worktree_root,
                workspace=workspace,
                ssh_profile_metadata=(
                    ()
                    if workspace is None
                    else build_workspace_ssh_profile_prompt_metadata(
                        workspace=workspace,
                        ssh_profile_service=(
                            self.workspace_manager.ssh_profile_service
                        ),
                        consumer=(
                            "agents.orchestration.task_execution_service"
                            ".prepare_runtime_snapshot"
                        ),
                    )
                ),
                conversation_context=conversation_context,
                runtime_tools=runtime_tools,
                runtime_active_local_tools=self._resolve_active_local_tools(
                    authorized_local_tools=tuple(
                        tool.name for tool in runtime_tools.local_tools
                    ),
                    runtime_active_tools_json=existing_runtime_active_tools_json,
                ),
            )
        )
        record_prompt_instruction_paths_loaded(
            shared_store=self.shared_store,
            task_id=task.task_id,
            paths=prompt_sections.local_instruction_paths,
        )
        user_prompt, skill_instructions = self._build_user_prompt(
            role=role,
            objective=objective,
            shared_state_snapshot=shared_state_snapshot,
            conversation_context=conversation_context,
            orchestration_prompt=(
                "" if topology is None else topology.orchestration_prompt
            ),
        )
        runtime_active_tools_json = self._build_runtime_active_tools_json(
            runtime_tools=runtime_tools,
            existing_runtime_active_tools_json=existing_runtime_active_tools_json,
        )
        return PreparedRuntimeSnapshot(
            prompt_sections=prompt_sections,
            runtime_tools_json=json.dumps(
                runtime_tools.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
            runtime_active_tools_json=runtime_active_tools_json,
            user_prompt=user_prompt,
            skill_instructions=skill_instructions,
        )

    def _compose_runtime_system_prompt(
        self,
        *,
        role: RoleDefinition,
        runtime_prompt_sections: RuntimePromptSections,
        skill_instructions: tuple[PromptSkillInstruction, ...],
    ) -> str:
        del role
        return compose_runtime_system_prompt(
            runtime_prompt_sections,
            skill_instructions=skill_instructions,
        )

    def _compose_provider_system_prompt(
        self,
        *,
        role: RoleDefinition,
        runtime_prompt_sections: RuntimePromptSections,
        skill_instructions: tuple[PromptSkillInstruction, ...],
    ) -> str:
        del role
        return compose_provider_system_prompt(
            runtime_prompt_sections,
            skill_instructions=skill_instructions,
        )

    async def _build_runtime_tools_snapshot(
        self,
        role: RoleDefinition,
        task: TaskEnvelope | None = None,
    ) -> RuntimeToolsSnapshot:
        skill_registry = cast(SkillRegistry, self.skill_registry)
        tool_registry = cast(ToolRegistry, self.tool_registry)
        resolved_skills = skill_registry.resolve_known(
            role.skills,
            strict=False,
            consumer="agents.orchestration.task_execution_service.build_runtime_tools_snapshot",
        )
        skill_tool_names = frozenset(
            tool.name for tool in skill_registry.get_toolset_tools(resolved_skills)
        )
        from relay_teams.agents.execution.coordination_agent_builder import (
            build_coordination_agent,
        )

        tool_agent = build_coordination_agent(
            model_name="snapshot-model",
            base_url="https://example.invalid/v1",
            api_key="snapshot",
            system_prompt="runtime-tools-snapshot",
            allowed_tools=tool_registry.resolve_names(
                role.tools,
                context=ToolResolutionContext(
                    session_id="" if task is None else task.session_id
                ),
            ),
            allowed_mcp_servers=(),
            allowed_skills=resolved_skills,
            tool_registry=tool_registry,
            role_registry=self.role_registry,
            mcp_registry=None,
            skill_registry=skill_registry,
        )
        local_tools: list[RuntimeToolSnapshotEntry] = []
        skill_tools: list[RuntimeToolSnapshotEntry] = []
        for tool in tool_agent._function_toolset.tools.values():
            entry = self._tool_entry_from_definition(
                source=cast(
                    Literal["local", "skill", "mcp"],
                    "skill" if tool.name in skill_tool_names else "local",
                ),
                name=tool.tool_def.name,
                description=tool.tool_def.description or "",
                kind=self._normalize_tool_kind(tool.tool_def.kind),
                strict=tool.tool_def.strict,
                sequential=tool.tool_def.sequential,
                parameters_json_schema=(
                    dict(tool.tool_def.parameters_json_schema)
                    if isinstance(tool.tool_def.parameters_json_schema, dict)
                    else {}
                ),
            )
            if entry.source == "skill":
                skill_tools.append(entry)
            else:
                local_tools.append(entry)

        mcp_tools: list[RuntimeToolSnapshotEntry] = []
        for server_name in self.mcp_registry.resolve_server_names(
            role.mcp_servers,
            strict=False,
            consumer="agents.orchestration.task_execution_service.build_runtime_tools_snapshot",
        ):
            for tool in await self.mcp_registry.list_tool_schemas(server_name):
                mcp_tools.append(
                    self._tool_entry_from_definition(
                        source="mcp",
                        name=tool.name,
                        description=tool.description,
                        kind="function",
                        strict=None,
                        sequential=False,
                        parameters_json_schema=tool.input_schema,
                        server_name=server_name,
                    )
                )

        local_tools.sort(key=lambda item: item.name)
        skill_tools.sort(key=lambda item: item.name)
        mcp_tools.sort(key=lambda item: (item.server_name, item.name))
        return RuntimeToolsSnapshot(
            local_tools=tuple(local_tools),
            skill_tools=tuple(skill_tools),
            mcp_tools=tuple(mcp_tools),
        )

    def _tool_entry_from_definition(
        self,
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
        return RuntimeToolSnapshotEntry(
            source=source,
            name=name,
            description=description,
            server_name=server_name,
            kind=kind,
            strict=strict,
            sequential=sequential,
            parameters_json_schema=dict(parameters_json_schema),
        )

    def _normalize_tool_kind(
        self,
        kind: str,
    ) -> Literal["function", "output", "external", "unapproved"]:
        if kind in {"function", "output", "external", "unapproved"}:
            return cast(
                Literal["function", "output", "external", "unapproved"],
                kind,
            )
        return "function"

    def _build_runtime_active_tools_json(
        self,
        *,
        runtime_tools: RuntimeToolsSnapshot,
        existing_runtime_active_tools_json: str,
    ) -> str:
        authorized_local_tools = tuple(tool.name for tool in runtime_tools.local_tools)
        existing_active_tools = self._parse_runtime_active_tools_json(
            existing_runtime_active_tools_json
        )
        active_tools = merge_active_tools(
            authorized_tools=authorized_local_tools,
            active_tools=existing_active_tools,
        )
        return json.dumps(list(active_tools), ensure_ascii=False, indent=2)

    def _parse_runtime_active_tools_json(
        self,
        runtime_active_tools_json: str,
    ) -> tuple[str, ...]:
        raw_payload = runtime_active_tools_json.strip()
        if not raw_payload:
            return ()
        try:
            parsed = json.loads(raw_payload)
        except json.JSONDecodeError:
            return ()
        if not isinstance(parsed, list):
            return ()
        parsed_names: list[str] = []
        for item in parsed:
            if isinstance(item, str):
                parsed_names.append(item)
        return tuple(parsed_names)

    def _resolve_active_local_tools(
        self,
        *,
        authorized_local_tools: tuple[str, ...],
        runtime_active_tools_json: str,
    ) -> tuple[str, ...]:
        return merge_active_tools(
            authorized_tools=authorized_local_tools,
            active_tools=self._parse_runtime_active_tools_json(
                runtime_active_tools_json
            ),
        )

    def _record_memory_if_needed(
        self,
        *,
        role_id: str,
        workspace_id: str,
        task: TaskEnvelope,
        conversation_id: str,
        result: str,
    ) -> None:
        del role_id, workspace_id, task, conversation_id, result
        return

    def _shared_state_snapshot(
        self,
        *,
        session_id: str,
        role_id: str,
        conversation_id: str,
    ) -> tuple[tuple[str, str], ...]:
        scopes = (
            ScopeRef(scope_type=ScopeType.SESSION, scope_id=session_id),
            ScopeRef(scope_type=ScopeType.ROLE, scope_id=f"{session_id}:{role_id}"),
            ScopeRef(scope_type=ScopeType.CONVERSATION, scope_id=conversation_id),
        )
        return self.shared_store.snapshot_many(scopes)

    def _ensure_committed_task_prompt(
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
        prompt = user_prompt_text.strip()
        override_prompt = str(user_prompt_override or "").strip()
        if override_prompt:
            self.message_repo.append_user_prompt_if_missing(
                session_id=task.session_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_role_id=role_id,
                instance_id=instance_id,
                task_id=task.task_id,
                trace_id=task.trace_id,
                content=override_prompt,
            )
            return

        task_history = self.message_repo.get_history_for_conversation_task(
            conversation_id,
            task.task_id,
        )
        if task_history:
            return
        if (
            task.parent_task_id is None
            and self.run_intent_repo is not None
            and self.media_asset_service is not None
        ):
            try:
                run_intent = self.run_intent_repo.get(task.trace_id)
            except KeyError:
                run_intent = None
            if run_intent is not None and run_intent.input:
                provider_content = (
                    self.media_asset_service.to_persisted_user_prompt_content(
                        parts=run_intent.input
                    )
                )
                merged_provider_content = self._merge_provider_prompt_content(
                    provider_content=provider_content,
                    user_prompt_text=prompt,
                )
                self.message_repo.prune_conversation_history_to_safe_boundary(
                    conversation_id
                )
                self.message_repo.append(
                    session_id=task.session_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_role_id=role_id,
                    instance_id=instance_id,
                    task_id=task.task_id,
                    trace_id=task.trace_id,
                    messages=[
                        ModelRequest(
                            parts=[UserPromptPart(content=merged_provider_content)]
                        )
                    ],
                )
                return
        if prompt:
            self.message_repo.append_user_prompt_if_missing(
                session_id=task.session_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_role_id=role_id,
                instance_id=instance_id,
                task_id=task.task_id,
                trace_id=task.trace_id,
                content=prompt,
            )
            return
        self.message_repo.append_user_prompt_if_missing(
            session_id=task.session_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_role_id=role_id,
            instance_id=instance_id,
            task_id=task.task_id,
            trace_id=task.trace_id,
            content=task.objective,
        )

    def _build_user_prompt(
        self,
        *,
        role: RoleDefinition,
        objective: str,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        conversation_context: RuntimePromptConversationContext | None,
        orchestration_prompt: str,
    ) -> tuple[str, tuple[PromptSkillInstruction, ...]]:
        resolved_objective = objective.strip()
        if self.skill_runtime_service is None:
            return (
                build_user_prompt(UserPromptBuildInput(objective=resolved_objective)),
                (),
            )
        skill_runtime_service = cast(
            SkillRuntimeService,
            self.skill_runtime_service,
        )
        prepared_prompt = skill_runtime_service.prepare_prompt(
            role=role,
            objective=resolved_objective,
            shared_state_snapshot=shared_state_snapshot,
            conversation_context=conversation_context,
            orchestration_prompt=orchestration_prompt,
            consumer="agents.orchestration.task_execution_service.prepare_prompt",
        )
        return (
            prepared_prompt.user_prompt,
            self._to_prompt_skill_instructions(
                prepared_prompt.system_prompt_skill_instructions
            ),
        )

    def _to_prompt_skill_instructions(
        self,
        entries: tuple[SkillInstructionEntry, ...],
    ) -> tuple[PromptSkillInstruction, ...]:
        return tuple(
            PromptSkillInstruction(name=entry.name, description=entry.description)
            for entry in entries
        )

    def _merge_provider_prompt_content(
        self,
        *,
        provider_content: ProviderUserPromptContent,
        user_prompt_text: str,
    ) -> ProviderUserPromptContent:
        appendix = self._user_prompt_skill_appendix(user_prompt_text)
        if not appendix:
            return provider_content
        return merge_user_prompt_content(provider_content, appendix)

    def _user_prompt_skill_appendix(self, user_prompt_text: str) -> str:
        prompt = user_prompt_text.strip()
        heading = "## Skill Candidates"
        if heading not in prompt:
            return ""
        return prompt[prompt.index(heading) :].strip()

    def _resolve_turn_objective(
        self,
        *,
        task: TaskEnvelope,
        user_prompt_override: str | None,
    ) -> str:
        prompt_override = str(user_prompt_override or "").strip()
        if prompt_override:
            return prompt_override
        return task.objective.strip()


class PreparedRuntimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_sections: RuntimePromptSections
    runtime_tools_json: str
    runtime_active_tools_json: str
    user_prompt: str
    skill_instructions: tuple[PromptSkillInstruction, ...] = ()
