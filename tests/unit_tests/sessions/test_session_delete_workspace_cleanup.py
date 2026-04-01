# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.agents.instances.enums import InstanceStatus
from agent_teams.sessions.runs.exec_session_models import (
    ExecSessionRecord,
    ExecSessionStatus,
)
from agent_teams.sessions.runs.exec_session_repo import ExecSessionRepository
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.session_service import SessionService
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from agent_teams.sessions.session_repository import SessionRepository
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.providers.token_usage_repo import TokenUsageRepository
from agent_teams.workspace.workspace_repository import WorkspaceRepository
from agent_teams.workspace.workspace_service import WorkspaceService
from agent_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from agent_teams.workspace import (
    WorkspaceManager,
    build_conversation_id,
    build_instance_conversation_id,
    build_instance_role_scope_id,
    build_instance_session_scope_id,
)


def _build_service(
    db_path: Path,
    project_root: Path,
    *,
    app_config_dir: Path,
) -> SessionService:
    shared_store = SharedStateRepository(db_path)
    workspace_repo = WorkspaceRepository(db_path)
    workspace_service = WorkspaceService(repository=workspace_repo)
    _ = workspace_service.create_workspace(
        workspace_id="default",
        root_path=project_root,
    )
    return SessionService(
        session_repo=SessionRepository(db_path),
        task_repo=TaskRepository(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        message_repo=MessageRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        exec_session_repo=ExecSessionRepository(db_path),
        event_log=EventLog(db_path),
        token_usage_repo=TokenUsageRepository(db_path),
        run_event_hub=RunEventHub(),
        shared_store=shared_store,
        workspace_manager=WorkspaceManager(
            project_root=project_root,
            app_config_dir=app_config_dir,
            shared_store=shared_store,
            workspace_repo=workspace_repo,
        ),
        workspace_service=workspace_service,
    )


def test_delete_session_cleans_workspace_and_role_state(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    db_path = tmp_path / "session_cleanup.db"
    service = _build_service(
        db_path,
        project_root,
        app_config_dir=tmp_path / ".agent-teams",
    )
    session = service.create_session(session_id="session-1", workspace_id="default")
    conversation_id = build_conversation_id("session-1", "time")
    workspace_id = "default"
    instance_workspace_id = workspace_id
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
    exec_session_repo = ExecSessionRepository(db_path)
    shared_store = SharedStateRepository(db_path)
    workspace_manager = WorkspaceManager(
        project_root=project_root,
        app_config_dir=tmp_path / ".agent-teams",
        shared_store=shared_store,
        workspace_repo=WorkspaceRepository(db_path),
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

    session_dir = workspace_manager.session_artifact_dir(
        workspace_id=session.workspace_id,
        session_id="session-1",
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "artifact.txt").write_text("artifact", encoding="utf-8")
    exec_record = exec_session_repo.upsert(
        ExecSessionRecord(
            exec_session_id="exec-1",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="time",
            tool_call_id="call-1",
            command="sleep 30",
            cwd=str(project_root),
            status=ExecSessionStatus.RUNNING,
            log_path="tmp/exec_sessions/exec-1.log",
        )
    )

    service.delete_session("session-1")

    workspace_snapshot = shared_store.snapshot(
        ScopeRef(scope_type=ScopeType.WORKSPACE, scope_id=workspace_id)
    )
    assert dict(workspace_snapshot) == {
        "workspace_note": '"workspace"',
        "subagent_workspace_note": '"subagent-workspace"',
    }
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
    assert exec_session_repo.get(exec_record.exec_session_id) is None
    assert not session_dir.exists()
    assert project_root.exists()
    with pytest.raises(KeyError):
        SessionRepository(db_path).get("session-1")
