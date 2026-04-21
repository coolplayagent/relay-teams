# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart

from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.monitors import (
    MonitorActionType,
    MonitorSourceKind,
    MonitorSubscriptionRecord,
    MonitorTriggerRecord,
    MonitorRepository,
)
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskKind,
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from relay_teams.sessions.runs.background_tasks.repository import (
    BackgroundTaskRepository,
)
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.session_service import SessionService
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.sessions.runs.run_state_models import (
    RunStatePhase,
    RunStateRecord,
    RunStateStatus,
)
from relay_teams.sessions.runs.todo_models import TodoItem, TodoStatus
from relay_teams.sessions.runs.todo_repository import TodoRepository
from relay_teams.sessions.runs.todo_service import TodoService
from relay_teams.sessions.runs.run_state_repo import RunStateRepository
from relay_teams.workspace.workspace_repository import WorkspaceRepository
from relay_teams.workspace.workspace_service import WorkspaceService
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from relay_teams.workspace import (
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
        monitor_repository=MonitorRepository(db_path),
        run_state_repo=RunStateRepository(db_path),
        background_task_repository=BackgroundTaskRepository(db_path),
        event_log=EventLog(db_path),
        token_usage_repo=TokenUsageRepository(db_path),
        todo_service=TodoService(repository=TodoRepository(db_path)),
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
    background_task_repository = BackgroundTaskRepository(db_path)
    monitor_repository = MonitorRepository(db_path)
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
    background_log_path = workspace_manager.resolve(
        session_id="session-1",
        role_id="time",
        instance_id="inst-1",
        workspace_id=session.workspace_id,
    ).resolve_tmp_path("background_tasks/exec-1.log")
    background_log_path.parent.mkdir(parents=True, exist_ok=True)
    background_log_path.write_text("running\n", encoding="utf-8")
    exec_record = background_task_repository.upsert(
        BackgroundTaskRecord(
            background_task_id="exec-1",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="time",
            tool_call_id="call-1",
            command="sleep 30",
            cwd=str(project_root),
            status=BackgroundTaskStatus.RUNNING,
            log_path="tmp/background_tasks/exec-1.log",
        )
    )
    monitor_record = monitor_repository.create_subscription(
        MonitorSubscriptionRecord(
            monitor_id="mon-1",
            run_id="run-1",
            session_id="session-1",
            source_kind=MonitorSourceKind.BACKGROUND_TASK,
            source_key="exec-1",
        )
    )
    _ = monitor_repository.create_trigger(
        MonitorTriggerRecord(
            monitor_trigger_id="mntg-1",
            monitor_id=monitor_record.monitor_id,
            run_id="run-1",
            session_id="session-1",
            source_kind=MonitorSourceKind.BACKGROUND_TASK,
            source_key="exec-1",
            event_name="background_task.line",
            action_type=MonitorActionType.WAKE_INSTANCE,
        )
    )

    service.delete_session("session-1", cascade=True)

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
    assert background_task_repository.get(exec_record.background_task_id) is None
    assert monitor_repository.list_for_run("run-1") == ()
    assert monitor_repository.list_triggers_for_monitor(monitor_record.monitor_id) == ()
    assert not background_log_path.exists()
    assert not session_dir.exists()
    assert project_root.exists()
    with pytest.raises(KeyError):
        SessionRepository(db_path).get("session-1")


def test_delete_normal_mode_subagent_cleans_child_session_state(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    db_path = tmp_path / "subagent_cleanup.db"
    service = _build_service(
        db_path,
        project_root,
        app_config_dir=tmp_path / ".agent-teams",
    )
    _ = service.create_session(session_id="session-1", workspace_id="default")
    run_id = "subagent_run_cleanup1"
    task_id = "task-subagent-root"
    instance_id = "inst-sub-1"
    role_id = "Crafter"
    conversation_id = build_instance_conversation_id(
        "session-1",
        role_id,
        instance_id,
    )
    session_scope_id = build_instance_session_scope_id("session-1", instance_id)
    role_scope_id = build_instance_role_scope_id("session-1", role_id, instance_id)

    task_repo = TaskRepository(db_path)
    _ = task_repo.create(
        TaskEnvelope(
            task_id=task_id,
            session_id="session-1",
            parent_task_id=None,
            trace_id=run_id,
            role_id=role_id,
            title="Cleanup subagent",
            objective="clean up subagent artifacts",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )

    agent_repo = AgentInstanceRepository(db_path)
    agent_repo.upsert_instance(
        run_id=run_id,
        trace_id=run_id,
        session_id="session-1",
        instance_id=instance_id,
        role_id=role_id,
        workspace_id="default",
        conversation_id=conversation_id,
        status=InstanceStatus.COMPLETED,
    )

    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id=run_id,
        session_id="session-1",
        root_task_id=task_id,
        status=RunRuntimeStatus.COMPLETED,
        phase=RunRuntimePhase.TERMINAL,
    )

    run_state_repo = RunStateRepository(db_path)
    runtime_record = runtime_repo.get(run_id)
    assert runtime_record is not None
    run_state_repo.upsert(
        RunStateRecord(
            run_id=run_id,
            session_id="session-1",
            status=RunStateStatus.COMPLETED,
            phase=RunStatePhase.TERMINAL,
            recoverable=False,
            last_event_id=4,
            checkpoint_event_id=4,
            updated_at=runtime_record.updated_at,
        )
    )

    message_repo = MessageRepository(db_path)
    message_repo.append(
        session_id="session-1",
        instance_id=instance_id,
        task_id=task_id,
        trace_id=run_id,
        messages=[ModelRequest(parts=[UserPromptPart(content="cleanup prompt")])],
        workspace_id="default",
        conversation_id=conversation_id,
        agent_role_id=role_id,
    )

    approval_repo = ApprovalTicketRepository(db_path)
    _ = approval_repo.upsert_requested(
        tool_call_id="call-sub-1",
        run_id=run_id,
        session_id="session-1",
        task_id=task_id,
        instance_id=instance_id,
        role_id=role_id,
        tool_name="run_command",
        args_preview='{"command":"echo ok"}',
    )

    event_log = EventLog(db_path)
    _ = event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id=run_id,
            trace_id=run_id,
            task_id=task_id,
            instance_id=instance_id,
            event_type=RunEventType.RUN_COMPLETED,
            payload_json="{}",
        )
    )

    token_usage_repo = TokenUsageRepository(db_path)
    token_usage_repo.record(
        session_id="session-1",
        run_id=run_id,
        instance_id=instance_id,
        role_id=role_id,
        input_tokens=12,
        output_tokens=7,
        requests=1,
    )
    todo_service = TodoService(repository=TodoRepository(db_path))
    todo_service.replace_for_run(
        run_id=run_id,
        session_id="session-1",
        items=(
            TodoItem(
                content="Clean subagent todo snapshot",
                status=TodoStatus.PENDING,
            ),
        ),
    )

    shared_store = SharedStateRepository(db_path)
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.INSTANCE, scope_id=instance_id),
            key="instance_note",
            value_json='"instance"',
        )
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.SESSION, scope_id=session_scope_id),
            key="session_note",
            value_json='"session"',
        )
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.ROLE, scope_id=role_scope_id),
            key="role_note",
            value_json='"role"',
        )
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(
                scope_type=ScopeType.CONVERSATION,
                scope_id=conversation_id,
            ),
            key="conversation_note",
            value_json='"conversation"',
        )
    )

    background_task_repository = BackgroundTaskRepository(db_path)
    workspace_manager = WorkspaceManager(
        project_root=project_root,
        app_config_dir=tmp_path / ".agent-teams",
        shared_store=shared_store,
        workspace_repo=WorkspaceRepository(db_path),
    )
    background_log_path = workspace_manager.resolve(
        session_id="session-1",
        role_id=role_id,
        instance_id=instance_id,
        workspace_id="default",
    ).resolve_tmp_path("background_tasks/subagent-cleanup.log")
    background_log_path.parent.mkdir(parents=True, exist_ok=True)
    background_log_path.write_text("done\n", encoding="utf-8")
    background_record = background_task_repository.upsert(
        BackgroundTaskRecord(
            background_task_id="bg-subagent-1",
            run_id="run-main",
            session_id="session-1",
            kind=BackgroundTaskKind.SUBAGENT,
            instance_id="inst-main",
            role_id="MainAgent",
            tool_call_id="call-main-1",
            title="Cleanup subagent",
            command="subagent:Crafter",
            cwd=str(project_root),
            status=BackgroundTaskStatus.COMPLETED,
            log_path="tmp/background_tasks/subagent-cleanup.log",
            subagent_role_id=role_id,
            subagent_run_id=run_id,
            subagent_task_id=task_id,
            subagent_instance_id=instance_id,
        )
    )

    service.delete_normal_mode_subagent("session-1", instance_id)

    assert service.list_normal_mode_subagents("session-1") == ()
    with pytest.raises(KeyError):
        agent_repo.get_instance(instance_id)
    assert message_repo.get_messages_for_instance("session-1", instance_id) == []
    assert runtime_repo.get(run_id) is None
    assert run_state_repo.get_run_state(run_id) is None
    assert event_log.list_by_trace(run_id) == ()
    assert approval_repo.list_open_by_run(run_id) == ()
    assert token_usage_repo.get_by_run(run_id).total_tokens == 0
    assert todo_service.get_for_run(run_id=run_id, session_id="session-1").version == 0
    assert task_repo.list_by_session("session-1") == ()
    assert background_task_repository.get(background_record.background_task_id) is None
    assert not background_log_path.exists()
    assert (
        shared_store.snapshot(
            ScopeRef(scope_type=ScopeType.INSTANCE, scope_id=instance_id)
        )
        == ()
    )
    assert (
        shared_store.snapshot(
            ScopeRef(scope_type=ScopeType.SESSION, scope_id=session_scope_id)
        )
        == ()
    )
    assert (
        shared_store.snapshot(
            ScopeRef(scope_type=ScopeType.ROLE, scope_id=role_scope_id)
        )
        == ()
    )
    assert (
        shared_store.snapshot(
            ScopeRef(scope_type=ScopeType.CONVERSATION, scope_id=conversation_id)
        )
        == ()
    )


def test_delete_normal_mode_subagent_rejects_running_child_session(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    db_path = tmp_path / "subagent_delete_conflict.db"
    service = _build_service(
        db_path,
        project_root,
        app_config_dir=tmp_path / ".agent-teams",
    )
    _ = service.create_session(session_id="session-1", workspace_id="default")
    run_id = "subagent_run_busy1"
    task_id = "task-subagent-busy"
    instance_id = "inst-sub-busy"

    _ = TaskRepository(db_path).create(
        TaskEnvelope(
            task_id=task_id,
            session_id="session-1",
            parent_task_id=None,
            trace_id=run_id,
            role_id="Explorer",
            title="Busy subagent",
            objective="stay busy",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    AgentInstanceRepository(db_path).upsert_instance(
        run_id=run_id,
        trace_id=run_id,
        session_id="session-1",
        instance_id=instance_id,
        role_id="Explorer",
        workspace_id="default",
        conversation_id=build_instance_conversation_id(
            "session-1",
            "Explorer",
            instance_id,
        ),
        status=InstanceStatus.RUNNING,
    )
    RunRuntimeRepository(db_path).ensure(
        run_id=run_id,
        session_id="session-1",
        root_task_id=task_id,
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.SUBAGENT_RUNNING,
    )

    with pytest.raises(RuntimeError, match="Cannot delete a running subagent"):
        service.delete_normal_mode_subagent("session-1", instance_id)
