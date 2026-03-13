# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.agents.enums import InstanceStatus
from agent_teams.runs.event_stream import RunEventHub
from agent_teams.reflection.config_manager import ReflectionConfigManager
from agent_teams.reflection.repository import ReflectionJobRepository
from agent_teams.reflection.models import DailyReflectionResult, LongTermMemoryDocument
from agent_teams.reflection.service import (
    ConsolidationPromptInput,
    ReflectionPromptInput,
    ReflectionService,
)
from agent_teams.sessions.service import SessionService
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.run_runtime_repo import RunRuntimeRepository
from agent_teams.state.scope_models import ScopeRef, ScopeType, StateMutation
from agent_teams.state.session_repo import SessionRepository
from agent_teams.state.shared_state_repo import SharedStateRepository
from agent_teams.state.task_repo import TaskRepository
from agent_teams.state.token_usage_repo import TokenUsageRepository
from agent_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from agent_teams.workspace import (
    WorkspaceManager,
    build_conversation_id,
    build_instance_conversation_id,
    build_instance_role_scope_id,
    build_instance_session_scope_id,
    build_instance_workspace_id,
    build_workspace_id,
)


def _build_service(db_path: Path, project_root: Path) -> SessionService:
    shared_store = SharedStateRepository(db_path)
    return SessionService(
        session_repo=SessionRepository(db_path),
        task_repo=TaskRepository(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        message_repo=MessageRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        event_log=EventLog(db_path),
        token_usage_repo=TokenUsageRepository(db_path),
        run_event_hub=RunEventHub(),
        shared_store=shared_store,
        workspace_manager=WorkspaceManager(
            project_root=project_root,
            shared_store=shared_store,
        ),
    )


def test_delete_session_cleans_workspace_and_role_state(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    db_path = tmp_path / "session_cleanup.db"
    service = _build_service(db_path, project_root)
    session = service.create_session(session_id="session-1")
    conversation_id = build_conversation_id("session-1", "time")
    workspace_id = build_workspace_id("session-1")
    instance_workspace_id = build_instance_workspace_id(
        "session-1",
        "time",
        "inst-1",
    )
    instance_conversation_id = build_instance_conversation_id(
        "session-1",
        "time",
        "inst-1",
    )
    instance_session_scope_id = build_instance_session_scope_id(
        "session-1",
        "inst-1",
    )
    instance_role_scope_id = build_instance_role_scope_id(
        "session-1",
        "time",
        "inst-1",
    )

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    shared_store = SharedStateRepository(db_path)
    workspace_manager = WorkspaceManager(
        project_root=project_root,
        shared_store=shared_store,
    )

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-1",
            session_id="session-1",
            parent_task_id="root-task",
            trace_id="run-1",
            objective="query time",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="time",
        workspace_id=instance_workspace_id,
        conversation_id=instance_conversation_id,
        status=InstanceStatus.IDLE,
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.WORKSPACE, scope_id=workspace_id),
            key="workspace_note",
            value_json='"workspace"',
        )
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.ROLE, scope_id="session-1:time"),
            key="role_note",
            value_json='"role"',
        )
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.CONVERSATION, scope_id=conversation_id),
            key="recent_note",
            value_json='"conversation"',
        )
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(
                scope_type=ScopeType.SESSION,
                scope_id=instance_session_scope_id,
            ),
            key="subagent_session_note",
            value_json='"subagent-session"',
        )
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(
                scope_type=ScopeType.ROLE,
                scope_id=instance_role_scope_id,
            ),
            key="subagent_role_note",
            value_json='"subagent-role"',
        )
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(
                scope_type=ScopeType.CONVERSATION,
                scope_id=instance_conversation_id,
            ),
            key="subagent_conversation_note",
            value_json='"subagent-conversation"',
        )
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(
                scope_type=ScopeType.WORKSPACE,
                scope_id=instance_workspace_id,
            ),
            key="subagent_workspace_note",
            value_json='"subagent-workspace"',
        )
    )

    workspace_dir = workspace_manager.locations_for(session.workspace_id).workspace_dir
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "artifact.txt").write_text("artifact", encoding="utf-8")
    subagent_workspace_dir = workspace_manager.locations_for(
        instance_workspace_id
    ).workspace_dir
    subagent_workspace_dir.mkdir(parents=True, exist_ok=True)
    (subagent_workspace_dir / "memory.json").write_text("{}", encoding="utf-8")

    service.delete_session("session-1")

    assert (
        shared_store.snapshot(
            ScopeRef(scope_type=ScopeType.WORKSPACE, scope_id=workspace_id)
        )
        == ()
    )
    assert (
        shared_store.snapshot(
            ScopeRef(scope_type=ScopeType.ROLE, scope_id="session-1:time")
        )
        == ()
    )
    assert (
        shared_store.snapshot(
            ScopeRef(scope_type=ScopeType.CONVERSATION, scope_id=conversation_id)
        )
        == ()
    )
    assert (
        shared_store.snapshot(
            ScopeRef(
                scope_type=ScopeType.SESSION,
                scope_id=instance_session_scope_id,
            )
        )
        == ()
    )
    assert (
        shared_store.snapshot(
            ScopeRef(
                scope_type=ScopeType.ROLE,
                scope_id=instance_role_scope_id,
            )
        )
        == ()
    )
    assert (
        shared_store.snapshot(
            ScopeRef(
                scope_type=ScopeType.CONVERSATION,
                scope_id=instance_conversation_id,
            )
        )
        == ()
    )
    assert (
        shared_store.snapshot(
            ScopeRef(
                scope_type=ScopeType.WORKSPACE,
                scope_id=instance_workspace_id,
            )
        )
        == ()
    )
    assert not workspace_dir.exists()
    assert not subagent_workspace_dir.exists()
    with pytest.raises(KeyError):
        SessionRepository(db_path).get("session-1")


class _NoopReflectionModelClient:
    async def generate_daily_reflection(
        self,
        prompt_input: ReflectionPromptInput,
    ) -> DailyReflectionResult:
        raise AssertionError(f"unexpected daily reflection call: {prompt_input}")

    async def consolidate_long_term_memory(
        self,
        prompt_input: ConsolidationPromptInput,
    ) -> LongTermMemoryDocument:
        raise AssertionError(f"unexpected long-term consolidation call: {prompt_input}")


def test_delete_session_cleans_reflection_artifacts(tmp_path: Path) -> None:
    project_root = tmp_path / "project_reflection"
    project_root.mkdir()
    config_dir = tmp_path / ".config" / "agent-teams"
    config_dir.mkdir(parents=True)
    db_path = tmp_path / "session_cleanup_reflection.db"
    shared_store = SharedStateRepository(db_path)
    reflection_service = ReflectionService(
        config_manager=ReflectionConfigManager(config_dir=config_dir),
        repository=ReflectionJobRepository(db_path),
        workspace_manager=WorkspaceManager(
            project_root=project_root, shared_store=shared_store
        ),
        message_repo=MessageRepository(db_path),
        task_repo=TaskRepository(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        model_client=_NoopReflectionModelClient(),
    )
    service = SessionService(
        session_repo=SessionRepository(db_path),
        task_repo=TaskRepository(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        message_repo=MessageRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        event_log=EventLog(db_path),
        token_usage_repo=TokenUsageRepository(db_path),
        run_event_hub=RunEventHub(),
        shared_store=shared_store,
        workspace_manager=WorkspaceManager(
            project_root=project_root, shared_store=shared_store
        ),
        reflection_service=reflection_service,
    )
    _ = service.create_session(session_id="session-1")
    TaskRepository(db_path).create(
        TaskEnvelope(
            task_id="task-1",
            session_id="session-1",
            parent_task_id="root-task",
            trace_id="run-1",
            objective="query time",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    AgentInstanceRepository(db_path).upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="time",
        workspace_id="instance-workspace-1",
        conversation_id="instance-conversation-1",
        status=InstanceStatus.IDLE,
    )
    _ = reflection_service.enqueue_daily_reflection(
        session_id="session-1",
        run_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="time",
        workspace_id="instance-workspace-1",
        conversation_id="instance-conversation-1",
    )
    memory_dir = config_dir / "memory" / "session_roles" / "session-1" / "time"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("# MEMORY", encoding="utf-8")

    service.delete_session("session-1")

    assert reflection_service.list_jobs() == ()
    assert not (memory_dir / "MEMORY.md").exists()
