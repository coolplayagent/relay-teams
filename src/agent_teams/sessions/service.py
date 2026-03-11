# -*- coding: utf-8 -*-
from __future__ import annotations

import uuid
from typing import Callable, cast

from agent_teams.agents.models import AgentRuntimeRecord
from agent_teams.runs.event_stream import RunEventHub
from agent_teams.reflection.service import ReflectionService
from agent_teams.sessions.rounds_projection import (
    approvals_to_projection,
    build_session_rounds,
    find_round_by_run_id,
    paginate_rounds,
)
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.state.session_models import SessionRecord
from agent_teams.state.session_repo import SessionRepository
from agent_teams.state.shared_state_repo import SharedStateRepository
from agent_teams.state.task_repo import TaskRepository
from agent_teams.state.token_usage_repo import (
    RunTokenUsage,
    SessionTokenUsage,
    TokenUsageRepository,
)
from agent_teams.state.workflow_graph_repo import WorkflowGraphRepository
from agent_teams.workspace import (
    WorkspaceManager,
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
        workflow_graph_repo: WorkflowGraphRepository,
        approval_ticket_repo: ApprovalTicketRepository,
        run_runtime_repo: RunRuntimeRepository,
        token_usage_repo: TokenUsageRepository,
        run_event_hub: RunEventHub | None = None,
        resolve_active_run_id: Callable[[str], str | None] | None = None,
        event_log: EventLog | None = None,
        shared_store: SharedStateRepository | None = None,
        workspace_manager: WorkspaceManager | None = None,
        reflection_service: ReflectionService | None = None,
    ) -> None:
        self._session_repo = session_repo
        self._task_repo = task_repo
        self._agent_repo = agent_repo
        self._message_repo = message_repo
        self._workflow_graph_repo = workflow_graph_repo
        self._approval_ticket_repo = approval_ticket_repo
        self._run_runtime_repo = run_runtime_repo
        self._token_usage_repo = token_usage_repo
        self._run_event_hub = run_event_hub
        self._resolve_active_run_id = resolve_active_run_id
        self._event_log = event_log
        self._shared_store = shared_store
        self._workspace_manager = workspace_manager
        self._reflection_service = reflection_service

    def create_session(
        self,
        *,
        session_id: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> SessionRecord:
        if not session_id:
            session_id = f"session-{uuid.uuid4().hex[:8]}"
        return self._session_repo.create(session_id=session_id, metadata=metadata)

    def update_session(self, session_id: str, metadata: dict[str, str]) -> None:
        self._session_repo.update_metadata(session_id, metadata)

    def delete_session(self, session_id: str) -> None:
        session = self._session_repo.get(session_id)
        task_records = self._task_repo.list_by_session(session_id)
        agent_records = self._agent_repo.list_by_session(session_id)
        task_ids = [record.envelope.task_id for record in task_records]
        instance_ids = [record.instance_id for record in agent_records]
        role_ids = sorted({record.role_id for record in agent_records})
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
        workspace_ids = sorted(
            {record.workspace_id for record in agent_records if record.workspace_id}
            | ({session.workspace_id} if session.workspace_id else set())
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
                workspace_ids=workspace_ids,
            )
        self._approval_ticket_repo.delete_by_session(session_id)
        if self._reflection_service is not None:
            self._reflection_service.delete_session_artifacts(
                session_id=session_id,
                role_ids=role_ids,
            )
        self._workflow_graph_repo.delete_by_session(session_id)
        self._run_runtime_repo.delete_by_session(session_id)
        self._task_repo.delete_by_session(session_id)
        self._agent_repo.delete_by_session(session_id)
        self._session_repo.delete(session_id)
        self._token_usage_repo.delete_by_session(session_id)
        if self._workspace_manager is not None:
            for workspace_id in workspace_ids:
                self._workspace_manager.delete_workspace(workspace_id)

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

    def list_agents_in_session(self, session_id: str) -> tuple[AgentRuntimeRecord, ...]:
        return self._agent_repo.list_by_session(session_id)

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

    def get_session_workflows(self, session_id: str) -> list[dict[str, object]]:
        return [
            record.graph
            for record in self._workflow_graph_repo.list_by_session(session_id)
        ]

    def build_session_rounds(self, session_id: str) -> list[dict[str, object]]:
        rounds = build_session_rounds(
            session_id=session_id,
            agent_repo=self._agent_repo,
            task_repo=self._task_repo,
            workflow_graph_repo=self._workflow_graph_repo,
            approval_tickets_by_run=approvals_to_projection(
                self._approval_ticket_repo.list_open_by_session(session_id)
            ),
            run_runtime_repo=self._run_runtime_repo,
            get_session_messages=self.get_session_messages,
        )
        for round_item in rounds:
            runtime = self._run_runtime_repo.get(str(round_item.get("run_id") or ""))
            pending = round_item.get("pending_tool_approvals")
            approval_count = len(pending) if isinstance(pending, list) else 0
            if runtime is None:
                continue
            round_item["run_status"] = runtime.status.value
            round_item["run_phase"] = self._public_phase(runtime, approval_count)
            round_item["is_recoverable"] = runtime.is_recoverable
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
        active_run = {
            "run_id": run_id,
            "status": runtime.status.value,
            "phase": self._public_phase(runtime, len(approvals)),
            "is_recoverable": runtime.is_recoverable,
            "pending_tool_approval_count": len(approvals),
            "stream_connected": stream_connected,
            "should_show_recover": runtime.is_recoverable and not stream_connected,
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
            self._resolve_active_run_id(session_id)
            if self._resolve_active_run_id is not None
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
        try:
            agent = self._agent_repo.get_instance(instance_id)
        except KeyError:
            return {
                "instance_id": instance_id,
                "role_id": runtime.active_role_id or "",
                "task_id": runtime.active_task_id,
            }
        return {
            "instance_id": agent.instance_id,
            "role_id": agent.role_id,
            "task_id": runtime.active_task_id,
        }

    def _public_phase(self, runtime: RunRuntimeRecord, approval_count: int) -> str:
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
