# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict, JsonValue

from agent_teams.agents.execution.subagent_runner import SubAgentRunner
from agent_teams.agents.execution.prompt_instruction_state import (
    record_prompt_instruction_paths_loaded,
)
from agent_teams.agents.execution.system_prompts import (
    PromptBuildInput,
    RuntimePromptBuilder,
)
from agent_teams.agents.instances.enums import InstanceStatus
from agent_teams.agents.instances.models import (
    RuntimeToolSnapshotEntry,
    RuntimeToolsSnapshot,
)
from agent_teams.agents.tasks.enums import TaskStatus
from agent_teams.agents.tasks.events import EventEnvelope, EventType
from agent_teams.agents.tasks.models import TaskEnvelope
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.logger import get_logger, log_event
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.persistence.scope_models import ScopeRef, ScopeType
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.roles.memory_injection import build_role_with_memory
from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
from agent_teams.sessions.runs.run_models import (
    RunThinkingConfig,
    RunTopologySnapshot,
)
from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)

if TYPE_CHECKING:
    from agent_teams.skills.skill_registry import SkillRegistry
    from agent_teams.tools.registry import ToolRegistry
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.workspace import WorkspaceManager

LOGGER = get_logger(__name__)


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
    workspace_manager: WorkspaceManager
    prompt_builder: RuntimePromptBuilder
    provider_factory: Callable[[RoleDefinition], object]
    tool_registry: object
    skill_registry: object
    mcp_registry: McpRegistry
    injection_manager: RunInjectionManager | None = None
    run_control_manager: RunControlManager | None = None
    role_memory_service: RoleMemoryService | None = None
    run_intent_repo: RunIntentRepository | None = None

    async def execute(
        self,
        *,
        instance_id: str,
        role_id: str,
        task: TaskEnvelope,
        user_prompt_override: str | None = None,
    ) -> str:
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
    ) -> str:
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

        role: RoleDefinition = self.role_registry.get(role_id)
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
            provider=self.provider_factory(role_for_run),
        )
        snapshot = self._shared_state_snapshot(
            session_id=task.session_id,
            role_id=role_id,
            conversation_id=workspace.ref.conversation_id,
        )
        try:
            self._ensure_committed_task_prompt(
                role_id=role_id,
                workspace_id=workspace.ref.workspace_id,
                conversation_id=workspace.ref.conversation_id,
                instance_id=instance_id,
                task=task,
                user_prompt_override=user_prompt_override,
            )
            (
                runtime_system_prompt,
                runtime_tools_json,
            ) = await self._prepare_runtime_snapshot(
                role=role_for_run,
                task=task,
                working_directory=workspace.resolve_workdir(),
                worktree_root=workspace.locations.worktree_root or workspace.root_path,
                shared_state_snapshot=snapshot,
            )
            self.agent_repo.update_runtime_snapshot(
                instance_id,
                runtime_system_prompt=runtime_system_prompt,
                runtime_tools_json=runtime_tools_json,
            )
            result = await runner.run(
                task=task,
                instance_id=instance_id,
                workspace_id=workspace.ref.workspace_id,
                working_directory=workspace.resolve_workdir(),
                conversation_id=workspace.ref.conversation_id,
                shared_state_snapshot=snapshot,
                thinking=self._thinking_for_run(task.trace_id),
                system_prompt_override=runtime_system_prompt,
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
            return result
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
                status=RunRuntimeStatus.STOPPED if stopped else RunRuntimeStatus.FAILED,
                phase=(
                    RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP
                    if paused_subagent
                    else RunRuntimePhase.TERMINAL
                    if not stopped
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
        except TimeoutError:
            _ = self.task_repo.update_status(
                task.task_id, TaskStatus.TIMEOUT, error_message="Task timeout"
            )
            _ = self.agent_repo.mark_status(instance_id, InstanceStatus.TIMEOUT)
            self.run_runtime_repo.update(
                task.trace_id,
                status=RunRuntimeStatus.FAILED,
                phase=RunRuntimePhase.TERMINAL,
                active_instance_id=None,
                active_task_id=None,
                active_role_id=None,
                active_subagent_instance_id=None,
                last_error="Task timeout",
            )
            self.event_bus.emit(
                EventEnvelope(
                    event_type=EventType.TASK_TIMEOUT,
                    trace_id=task.trace_id,
                    session_id=task.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json="{}",
                )
            )
            log_event(
                LOGGER,
                logging.ERROR,
                event="task.execution.timeout",
                message="Task execution timed out",
                payload={
                    "task_id": task.task_id,
                    "instance_id": instance_id,
                    "role_id": role_id,
                },
            )
            raise
        except Exception as exc:
            _ = self.task_repo.update_status(
                task.task_id, TaskStatus.FAILED, error_message=str(exc)
            )
            _ = self.agent_repo.mark_status(instance_id, InstanceStatus.FAILED)
            self.run_runtime_repo.update(
                task.trace_id,
                status=RunRuntimeStatus.FAILED,
                phase=RunRuntimePhase.TERMINAL,
                active_instance_id=None,
                active_task_id=None,
                active_role_id=None,
                active_subagent_instance_id=None,
                last_error=str(exc),
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
                logging.ERROR,
                event="task.execution.failed",
                message="Task execution failed",
                payload={
                    "task_id": task.task_id,
                    "instance_id": instance_id,
                    "role_id": role_id,
                },
                exc_info=exc,
            )
            raise

    def _thinking_for_run(self, run_id: str) -> RunThinkingConfig:
        if self.run_intent_repo is None:
            return RunThinkingConfig()
        try:
            return self.run_intent_repo.get(run_id).thinking
        except KeyError:
            return RunThinkingConfig()

    def _topology_for_run(self, run_id: str) -> RunTopologySnapshot | None:
        if self.run_intent_repo is None:
            return None
        try:
            return self.run_intent_repo.get(run_id).topology
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
        shared_state_snapshot: tuple[tuple[str, str], ...],
    ) -> tuple[str, str]:
        prompt_result = await self.prompt_builder.build_details(
            PromptBuildInput(
                role=role,
                task=task,
                topology=self._topology_for_run(task.trace_id),
                shared_state_snapshot=shared_state_snapshot,
                working_directory=working_directory,
                worktree_root=worktree_root,
            )
        )
        record_prompt_instruction_paths_loaded(
            shared_store=self.shared_store,
            task_id=task.task_id,
            paths=prompt_result.local_instruction_paths,
        )
        runtime_tools = await self._build_runtime_tools_snapshot(role)
        return prompt_result.prompt, json.dumps(
            runtime_tools.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )

    async def _build_runtime_tools_snapshot(
        self,
        role: RoleDefinition,
    ) -> RuntimeToolsSnapshot:
        skill_registry = cast("SkillRegistry", self.skill_registry)
        tool_registry = cast("ToolRegistry", self.tool_registry)
        skill_tool_names = frozenset(
            tool.name for tool in skill_registry.get_toolset_tools(role.skills)
        )
        from agent_teams.agents.execution.coordination_agent_builder import (
            build_coordination_agent,
        )

        tool_agent = build_coordination_agent(
            model_name="snapshot-model",
            base_url="https://example.invalid/v1",
            api_key="snapshot",
            system_prompt="runtime-tools-snapshot",
            allowed_tools=role.tools,
            allowed_mcp_servers=(),
            allowed_skills=role.skills,
            tool_registry=tool_registry,
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
        for server_name in self.mcp_registry.resolve_server_names(role.mcp_servers):
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
        user_prompt_override: str | None,
    ) -> None:
        prompt = str(user_prompt_override or "").strip()
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

        task_history = self.message_repo.get_history_for_conversation_task(
            conversation_id,
            task.task_id,
        )
        if task_history:
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
