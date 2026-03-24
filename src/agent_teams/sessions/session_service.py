# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
import uuid
from collections.abc import Callable
from typing import cast

from agent_teams.agents.instances.models import AgentRuntimeRecord
from agent_teams.agents.execution.subagent_reflection import SubagentReflectionService
from agent_teams.agents.orchestration.settings_service import (
    OrchestrationSettingsService,
)
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.metrics import SqliteMetricAggregateStore
from agent_teams.persistence.scope_models import ScopeRef, ScopeType
from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.roles.role_registry import (
    LEGACY_COORDINATOR_IDENTIFIERS,
    MAIN_AGENT_IDENTIFIERS,
)
from agent_teams.sessions.runs.active_run_registry import ActiveSessionRunRegistry
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.runtime_config import RuntimeConfig
from agent_teams.sessions.session_rounds_projection import (
    approvals_to_projection,
    build_session_rounds,
    find_round_by_run_id,
    paginate_rounds,
)
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.sessions.runs.run_state_repo import RunStateRepository
from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.sessions.external_session_binding_repository import (
    ExternalSessionBindingRepository,
)
from agent_teams.sessions.session_models import SessionMode, SessionRecord
from agent_teams.sessions.session_repository import SessionRepository
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.providers.token_usage_repo import (
    RunTokenUsage,
    SessionTokenUsage,
    TokenUsageRepository,
)
from agent_teams.workspace import (
    WorkspaceManager,
    WorkspaceService,
    build_conversation_id,
    build_instance_role_scope_id,
    build_instance_session_scope_id,
)


class SessionService:
    def __init__(
        self,
        *,
        session_repo: SessionRepository,
        task_repo: TaskRepository,
        agent_repo: AgentInstanceRepository,
        message_repo: MessageRepository,
        approval_ticket_repo: ApprovalTicketRepository,
        run_runtime_repo: RunRuntimeRepository,
        token_usage_repo: TokenUsageRepository,
        run_state_repo: RunStateRepository | None = None,
        run_event_hub: RunEventHub | None = None,
        active_run_registry: ActiveSessionRunRegistry | None = None,
        event_log: EventLog | None = None,
        shared_store: SharedStateRepository | None = None,
        metrics_store: SqliteMetricAggregateStore | None = None,
        workspace_manager: WorkspaceManager | None = None,
        workspace_service: WorkspaceService | None = None,
        external_session_binding_repo: ExternalSessionBindingRepository | None = None,
        role_memory_service: RoleMemoryService | None = None,
        subagent_reflection_service: SubagentReflectionService | None = None,
        role_registry: RoleRegistry | None = None,
        skill_registry: SkillRegistry | None = None,
        mcp_registry: McpRegistry | None = None,
        orchestration_settings_service: OrchestrationSettingsService | None = None,
        get_runtime: Callable[[], RuntimeConfig] | None = None,
    ) -> None:
        self._session_repo = session_repo
        self._task_repo = task_repo
        self._agent_repo = agent_repo
        self._message_repo = message_repo
        self._approval_ticket_repo = approval_ticket_repo
        self._run_runtime_repo = run_runtime_repo
        self._token_usage_repo = token_usage_repo
        self._run_state_repo = run_state_repo
        self._run_event_hub = run_event_hub
        self._active_run_registry = active_run_registry
        self._event_log = event_log
        self._shared_store = shared_store
        self._metrics_store = metrics_store
        self._workspace_manager = workspace_manager
        self._workspace_service = workspace_service
        self._external_session_binding_repo = external_session_binding_repo
        self._role_memory_service = role_memory_service
        self._subagent_reflection_service = subagent_reflection_service
        self._role_registry = role_registry
        self._skill_registry = skill_registry
        self._mcp_registry = mcp_registry
        self._orchestration_settings_service = orchestration_settings_service
        self._get_runtime = get_runtime

    def create_session(
        self,
        *,
        session_id: str | None = None,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
    ) -> SessionRecord:
        if not session_id:
            session_id = f"session-{uuid.uuid4().hex[:8]}"
        if self._workspace_service is not None:
            self._workspace_service.require_workspace(workspace_id)
        session_mode = SessionMode.NORMAL
        orchestration_preset_id: str | None = None
        if self._orchestration_settings_service is not None:
            session_mode = self._orchestration_settings_service.default_session_mode()
            orchestration_preset_id = (
                self._orchestration_settings_service.default_orchestration_preset_id()
            )
        return self._session_repo.create(
            session_id=session_id,
            workspace_id=workspace_id,
            metadata=metadata,
            session_mode=session_mode,
            orchestration_preset_id=orchestration_preset_id,
        )

    def update_session(self, session_id: str, metadata: dict[str, str]) -> None:
        self._session_repo.update_metadata(session_id, metadata)

    def update_session_topology(
        self,
        session_id: str,
        *,
        session_mode: SessionMode,
        orchestration_preset_id: str | None,
    ) -> SessionRecord:
        session = self._session_repo.get(session_id)
        if session.started_at is not None:
            raise RuntimeError("Session mode can no longer be changed")
        if (
            session_mode == SessionMode.ORCHESTRATION
            and self._orchestration_settings_service is not None
        ):
            probe = session.model_copy(
                update={
                    "session_mode": SessionMode.ORCHESTRATION,
                    "orchestration_preset_id": orchestration_preset_id,
                }
            )
            _ = self._orchestration_settings_service.resolve_run_topology(probe)
        self._session_repo.update_topology(
            session_id,
            session_mode=session_mode,
            orchestration_preset_id=orchestration_preset_id,
        )
        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> None:
        session = self._session_repo.get(session_id)
        task_records = self._task_repo.list_by_session(session_id)
        agent_records = self._agent_repo.list_by_session(session_id)
        task_ids = [record.envelope.task_id for record in task_records]
        instance_ids = [record.instance_id for record in agent_records]
        role_scope_ids = sorted(
            {f"{record.session_id}:{record.role_id}" for record in agent_records}
            | {
                build_instance_role_scope_id(
                    record.session_id,
                    record.role_id,
                    record.instance_id,
                )
                for record in agent_records
            }
        )
        session_scope_ids = sorted(
            {
                build_instance_session_scope_id(
                    record.session_id,
                    record.instance_id,
                )
                for record in agent_records
            }
        )
        conversation_ids = sorted(
            {
                record.conversation_id
                for record in agent_records
                if record.conversation_id
            }
            | {
                build_conversation_id(
                    record.session_id,
                    record.role_id,
                )
                for record in agent_records
            }
        )
        self._message_repo.delete_by_session(session_id)
        if self._event_log is not None:
            self._event_log.delete_by_session(session_id)
        if self._shared_store is not None:
            self._shared_store.delete_by_session(
                session_id,
                task_ids=task_ids,
                instance_ids=instance_ids,
                role_scope_ids=role_scope_ids,
                session_scope_ids=session_scope_ids,
                conversation_ids=conversation_ids,
                workspace_ids=[],
            )
        self._approval_ticket_repo.delete_by_session(session_id)
        self._run_runtime_repo.delete_by_session(session_id)
        self._task_repo.delete_by_session(session_id)
        self._agent_repo.delete_by_session(session_id)
        if self._external_session_binding_repo is not None:
            self._external_session_binding_repo.delete_by_session(session_id)
        self._session_repo.delete(session_id)
        self._token_usage_repo.delete_by_session(session_id)
        if self._metrics_store is not None:
            self._metrics_store.delete_by_session(session_id)
        if self._workspace_manager is not None:
            session_dir = self._workspace_manager.session_artifact_dir(
                workspace_id=session.workspace_id,
                session_id=session_id,
            )
            if session_dir.exists():
                shutil.rmtree(session_dir, ignore_errors=True)

    def get_session(self, session_id: str) -> SessionRecord:
        return self._session_repo.get(session_id)

    def list_sessions(self) -> tuple[SessionRecord, ...]:
        sessions = self._session_repo.list_all()
        enriched: list[SessionRecord] = []
        for record in sessions:
            selected = self._select_active_run(record.session_id)
            if selected is None:
                enriched.append(record)
                continue
            run_id, runtime = selected
            approval_count = len(self._approval_ticket_repo.list_open_by_run(run_id))
            enriched.append(
                record.model_copy(
                    update={
                        "has_active_run": True,
                        "active_run_id": run_id,
                        "active_run_status": runtime.status.value,
                        "active_run_phase": self._public_phase(runtime, approval_count),
                        "pending_tool_approval_count": approval_count,
                    }
                )
            )
        return tuple(enriched)

    def list_agents_in_session(self, session_id: str) -> tuple[dict[str, object], ...]:
        records = self._agent_repo.list_session_role_instances(session_id)
        return tuple(self._agent_projection(record) for record in records)

    def get_agent_reflection(
        self,
        session_id: str,
        instance_id: str,
    ) -> dict[str, object]:
        agent = self._require_session_agent(session_id, instance_id)
        return self._reflection_projection(agent)

    async def refresh_subagent_reflection(
        self,
        session_id: str,
        instance_id: str,
    ) -> dict[str, object]:
        if self._subagent_reflection_service is None or self._role_registry is None:
            raise RuntimeError("Subagent reflection is not available")
        agent = self._require_session_agent(session_id, instance_id)
        if self._role_registry.is_coordinator_role(agent.role_id):
            raise RuntimeError("Coordinator reflection refresh is not supported")
        role = self._role_registry.get(agent.role_id)
        record = await self._subagent_reflection_service.refresh_reflection(
            role=role,
            workspace_id=agent.workspace_id,
            conversation_id=agent.conversation_id,
        )
        return self._reflection_projection(agent, role_record=record, source="manual")

    def update_agent_reflection(
        self,
        session_id: str,
        instance_id: str,
        *,
        summary: str,
    ) -> dict[str, object]:
        if self._role_memory_service is None:
            raise RuntimeError("Subagent reflection is not available")
        agent = self._require_session_agent(session_id, instance_id)
        record = self._role_memory_service.update_reflection_memory(
            role_id=agent.role_id,
            workspace_id=agent.workspace_id,
            content_markdown=summary,
        )
        return self._reflection_projection(
            agent,
            role_record=record,
            source="manual_edit",
        )

    def delete_agent_reflection(
        self,
        session_id: str,
        instance_id: str,
    ) -> dict[str, object]:
        if self._role_memory_service is None:
            raise RuntimeError("Subagent reflection is not available")
        agent = self._require_session_agent(session_id, instance_id)
        self._role_memory_service.delete_reflection_memory(
            role_id=agent.role_id,
            workspace_id=agent.workspace_id,
        )
        return self._reflection_projection(agent, source="manual_delete")

    def get_agent_messages(
        self, session_id: str, instance_id: str
    ) -> list[dict[str, object]]:
        messages = cast(
            list[dict[str, object]],
            self._message_repo.get_messages_for_instance(session_id, instance_id),
        )
        try:
            agent = self._agent_repo.get_instance(instance_id)
        except KeyError:
            return messages
        for message in messages:
            if "role_id" not in message or not message.get("role_id"):
                message["role_id"] = agent.role_id
        return messages

    def get_global_events(self, session_id: str) -> list[dict[str, object]]:
        if self._event_log is None:
            return []
        events = self._event_log.list_by_session(session_id)
        return cast(list[dict[str, object]], list(events))

    def get_session_messages(self, session_id: str) -> list[dict[str, object]]:
        return cast(
            list[dict[str, object]],
            self._message_repo.get_messages_by_session(session_id),
        )

    def get_session_tasks(self, session_id: str) -> list[dict[str, object]]:
        records = self._task_repo.list_by_session(session_id)
        return [
            {
                "task_id": record.envelope.task_id,
                "title": record.envelope.title or record.envelope.objective[:80],
                "assigned_role_id": record.envelope.role_id,
                "status": record.status.value,
                "assigned_instance_id": record.assigned_instance_id,
                "role_id": record.envelope.role_id,
                "instance_id": record.assigned_instance_id,
                "run_id": record.envelope.trace_id,
                "created_at": record.created_at.isoformat(),
                "updated_at": record.updated_at.isoformat(),
            }
            for record in records
            if record.envelope.parent_task_id is not None
        ]

    def build_session_rounds(self, session_id: str) -> list[dict[str, object]]:
        rounds = build_session_rounds(
            session_id=session_id,
            agent_repo=self._agent_repo,
            task_repo=self._task_repo,
            approval_tickets_by_run=approvals_to_projection(
                self._approval_ticket_repo.list_open_by_session(session_id)
            ),
            run_runtime_repo=self._run_runtime_repo,
            get_session_messages=self.get_session_messages,
            get_session_events=self.get_global_events,
        )
        for round_item in rounds:
            runtime = self._run_runtime_repo.get(str(round_item.get("run_id") or ""))
            pending = round_item.get("pending_tool_approvals")
            approval_count = len(pending) if isinstance(pending, list) else 0
            if runtime is None:
                continue
            round_item["run_status"] = runtime.status.value
            round_item["run_phase"] = self._public_phase(runtime, approval_count)
            round_item["is_recoverable"] = self._is_runtime_publicly_recoverable(
                runtime
            )
        return rounds

    def get_session_rounds(
        self,
        session_id: str,
        *,
        limit: int = 8,
        cursor_run_id: str | None = None,
    ) -> dict[str, object]:
        rounds = self.build_session_rounds(session_id)
        return paginate_rounds(rounds, limit=limit, cursor_run_id=cursor_run_id)

    def get_round(self, session_id: str, run_id: str) -> dict[str, object]:
        rounds = self.build_session_rounds(session_id)
        return find_round_by_run_id(rounds, session_id=session_id, run_id=run_id)

    def get_recovery_snapshot(self, session_id: str) -> dict[str, object]:
        _ = self._session_repo.get(session_id)
        selected = self._select_active_run(session_id)
        if selected is None:
            return {
                "active_run": None,
                "pending_tool_approvals": [],
                "paused_subagent": None,
                "round_snapshot": None,
            }

        run_id, runtime = selected
        stream_connected = (
            self._run_event_hub.has_subscribers(run_id)
            if self._run_event_hub is not None
            else False
        )
        approvals = [
            {
                "tool_call_id": record.tool_call_id,
                "tool_name": record.tool_name,
                "args_preview": record.args_preview,
                "role_id": record.role_id,
                "instance_id": record.instance_id,
                "requested_at": record.created_at.isoformat(),
                "status": record.status.value,
                "feedback": record.feedback,
            }
            for record in self._approval_ticket_repo.list_open_by_run(run_id)
        ]
        run_state = (
            self._run_state_repo.get_run_state(run_id)
            if self._run_state_repo is not None
            else None
        )
        active_run = {
            "run_id": run_id,
            "status": runtime.status.value,
            "phase": self._public_phase(runtime, len(approvals)),
            "is_recoverable": self._is_runtime_publicly_recoverable(runtime),
            "last_event_id": (
                int(run_state.last_event_id) if run_state is not None else 0
            ),
            "checkpoint_event_id": (
                int(run_state.checkpoint_event_id) if run_state is not None else 0
            ),
            "pending_tool_approval_count": len(approvals),
            "stream_connected": stream_connected,
            "should_show_recover": self._is_runtime_publicly_recoverable(runtime)
            and not stream_connected,
        }
        paused_subagent = self._paused_subagent_snapshot(runtime)
        try:
            round_snapshot = self.get_round(session_id, run_id)
        except KeyError:
            round_snapshot = None
        return {
            "active_run": active_run,
            "pending_tool_approvals": approvals,
            "paused_subagent": paused_subagent,
            "round_snapshot": round_snapshot,
        }

    def get_token_usage_by_run(self, run_id: str) -> RunTokenUsage:
        return self._token_usage_repo.get_by_run(run_id)

    def get_token_usage_by_session(self, session_id: str) -> SessionTokenUsage:
        return self._token_usage_repo.get_by_session(session_id)

    def _select_active_run(
        self, session_id: str
    ) -> tuple[str, RunRuntimeRecord] | None:
        hinted_run_id = (
            self._active_run_registry.get_active_run_id(session_id)
            if self._active_run_registry is not None
            else None
        )
        if hinted_run_id:
            hinted_runtime = self._run_runtime_repo.get(hinted_run_id)
            if hinted_runtime is not None:
                return hinted_run_id, hinted_runtime

        runtimes = list(self._run_runtime_repo.list_by_session(session_id))
        if not runtimes:
            return None
        runtimes.sort(key=lambda item: item.updated_at, reverse=True)
        for runtime in runtimes:
            if runtime.status in {
                RunRuntimeStatus.RUNNING,
                RunRuntimeStatus.STOPPING,
                RunRuntimeStatus.PAUSED,
                RunRuntimeStatus.STOPPED,
                RunRuntimeStatus.QUEUED,
            }:
                return runtime.run_id, runtime
        return None

    def _paused_subagent_snapshot(
        self,
        runtime: RunRuntimeRecord,
    ) -> dict[str, object] | None:
        if runtime.phase not in {
            RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP,
            RunRuntimePhase.SUBAGENT_RUNNING,
        }:
            return None
        instance_id = runtime.active_subagent_instance_id or runtime.active_instance_id
        if not instance_id:
            return None
        role_id = runtime.active_role_id or ""
        if self._is_reserved_system_role(role_id):
            return None
        try:
            agent = self._agent_repo.get_instance(instance_id)
        except KeyError:
            return {
                "instance_id": instance_id,
                "role_id": role_id,
                "task_id": runtime.active_task_id,
            }
        if self._is_reserved_system_role(agent.role_id):
            return None
        return {
            "instance_id": agent.instance_id,
            "role_id": agent.role_id,
            "task_id": runtime.active_task_id,
        }

    def _is_reserved_system_role(self, role_id: str) -> bool:
        safe_role_id = str(role_id or "").strip()
        if not safe_role_id:
            return False
        if self._role_registry is not None and (
            self._role_registry.is_coordinator_role(safe_role_id)
            or self._role_registry.is_main_agent_role(safe_role_id)
        ):
            return True
        normalized = safe_role_id.casefold()
        return (
            normalized in LEGACY_COORDINATOR_IDENTIFIERS
            or normalized in MAIN_AGENT_IDENTIFIERS
        )

    def _require_session_agent(
        self,
        session_id: str,
        instance_id: str,
    ) -> AgentRuntimeRecord:
        agent = self._agent_repo.get_instance(instance_id)
        if agent.session_id != session_id:
            raise KeyError(instance_id)
        return agent

    def _agent_projection(self, record: AgentRuntimeRecord) -> dict[str, object]:
        reflection = self._reflection_projection(record)
        return {
            **record.model_dump(mode="json"),
            "reflection_summary_preview": reflection["preview"],
            "reflection_updated_at": reflection["updated_at"],
        }

    def _reflection_projection(
        self,
        record: AgentRuntimeRecord,
        *,
        source: str = "stored",
        role_record: object | None = None,
    ) -> dict[str, object]:
        memory = role_record
        if memory is None and self._role_memory_service is not None:
            memory = self._role_memory_service.get_reflection_record(
                role_id=record.role_id,
                workspace_id=record.workspace_id,
            )
        if memory is None:
            return {
                "instance_id": record.instance_id,
                "role_id": record.role_id,
                "summary": "",
                "preview": "",
                "updated_at": None,
                "source": source,
            }
        updated_at = getattr(memory, "updated_at", None)
        summary = str(getattr(memory, "content_markdown", "") or "").strip()
        preview = ""
        if self._role_memory_service is not None:
            preview = self._role_memory_service.build_reflection_preview(
                role_id=record.role_id,
                workspace_id=record.workspace_id,
            )
        return {
            "instance_id": record.instance_id,
            "role_id": record.role_id,
            "summary": summary,
            "preview": preview,
            "updated_at": updated_at.isoformat() if updated_at is not None else None,
            "source": source,
        }

    def _public_phase(self, runtime: RunRuntimeRecord, approval_count: int) -> str:
        if runtime.status == RunRuntimeStatus.STOPPING:
            return "stopping"
        if approval_count > 0:
            return "awaiting_tool_approval"
        if runtime.phase == RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP:
            return "awaiting_subagent_followup"
        if runtime.status == RunRuntimeStatus.RUNNING:
            return "running"
        if runtime.status == RunRuntimeStatus.PAUSED:
            return (
                "awaiting_subagent_followup"
                if runtime.phase == RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP
                else "running"
            )
        if runtime.status == RunRuntimeStatus.STOPPED:
            return "stopped"
        if runtime.status == RunRuntimeStatus.QUEUED:
            return "queued"
        if runtime.status == RunRuntimeStatus.COMPLETED:
            return "completed"
        if runtime.status == RunRuntimeStatus.FAILED:
            return "failed"
        return runtime.phase.value

    def _is_runtime_publicly_recoverable(self, runtime: RunRuntimeRecord) -> bool:
        return runtime.is_recoverable and runtime.status != RunRuntimeStatus.STOPPING

    def _shared_state_snapshot(
        self,
        *,
        session_id: str,
        role_id: str,
        conversation_id: str,
    ) -> tuple[tuple[str, str], ...]:
        if self._shared_store is None:
            return ()
        scopes = (
            ScopeRef(scope_type=ScopeType.SESSION, scope_id=session_id),
            ScopeRef(scope_type=ScopeType.ROLE, scope_id=f"{session_id}:{role_id}"),
            ScopeRef(scope_type=ScopeType.CONVERSATION, scope_id=conversation_id),
        )
        return self._shared_store.snapshot_many(scopes)
