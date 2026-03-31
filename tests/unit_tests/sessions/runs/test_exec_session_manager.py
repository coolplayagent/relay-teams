# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_teams.sessions.runs.exec_session_manager import (
    ExecSessionManager,
)
from agent_teams.sessions.runs.exec_session_models import (
    ExecSessionStatus,
)
from agent_teams.sessions.runs.exec_session_repo import (
    ExecSessionRepository,
)
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.workspace import WorkspaceHandle
from agent_teams.workspace.workspace_models import (
    WorkspaceLocations,
    WorkspaceRef,
    default_workspace_profile,
)


def _build_workspace_handle(tmp_path: Path) -> WorkspaceHandle:
    scope_root = tmp_path / "project"
    workspace_dir = tmp_path / ".agent-teams" / "workspaces" / "project"
    scope_root.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    tmp_root = workspace_dir / "tmp"
    profile = default_workspace_profile()
    return WorkspaceHandle(
        ref=WorkspaceRef(
            workspace_id="workspace-1",
            session_id="session-1",
            role_id="writer",
            conversation_id="conversation-1",
            profile=profile,
        ),
        profile=profile,
        locations=WorkspaceLocations(
            workspace_dir=workspace_dir,
            scope_root=scope_root,
            execution_root=scope_root,
            tmp_root=tmp_root,
            readable_roots=(scope_root, tmp_root),
            writable_roots=(scope_root, tmp_root),
        ),
    )


@pytest.mark.asyncio
async def test_exec_session_manager_completes_and_publishes_events(
    tmp_path: Path,
) -> None:
    repo = ExecSessionRepository(tmp_path / "background-terminal-manager.db")
    hub = RunEventHub()
    manager = ExecSessionManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)
    queue = hub.subscribe("run-1")

    try:
        started, completed = await manager.exec_command(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            workspace=workspace,
            command="printf 'hello\\n'",
            cwd=workspace.execution_root,
            timeout_ms=5000,
            yield_time_ms=5000,
            env=None,
            tty=False,
        )
        await asyncio.sleep(0)
    finally:
        await manager.close()

    assert completed is True
    assert started.status == ExecSessionStatus.COMPLETED
    assert "hello" in started.recent_output
    log_path = workspace.resolve_read_path(started.log_path)
    assert log_path.read_text(encoding="utf-8") == "hello\n"
    event_types = []
    while not queue.empty():
        event_types.append(queue.get_nowait().event_type)
    assert RunEventType.EXEC_SESSION_STARTED in event_types
    assert RunEventType.EXEC_SESSION_COMPLETED in event_types


@pytest.mark.asyncio
async def test_exec_session_manager_write_finishes_shell_prompt(
    tmp_path: Path,
) -> None:
    repo = ExecSessionRepository(tmp_path / "background-terminal-write.db")
    hub = RunEventHub()
    manager = ExecSessionManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)

    try:
        started = await manager.start_session(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            workspace=workspace,
            command='read line; echo "line:$line"',
            cwd=workspace.execution_root,
            timeout_ms=5000,
            env=None,
            tty=False,
        )
        _, _ = await manager.interact_for_run(
            run_id="run-1",
            exec_session_id=started.exec_session_id,
            chars="hello\n",
            yield_time_ms=5000,
        )
        completed_record, completed = await manager.wait_for_run(
            run_id="run-1",
            exec_session_id=started.exec_session_id,
            wait_ms=5000,
        )
    finally:
        await manager.close()

    assert completed is True
    assert completed_record.status == ExecSessionStatus.COMPLETED
    assert "line:hello" in completed_record.recent_output


@pytest.mark.asyncio
async def test_exec_session_manager_stop_marks_terminal_stopped(
    tmp_path: Path,
) -> None:
    repo = ExecSessionRepository(tmp_path / "background-terminal-stop.db")
    hub = RunEventHub()
    manager = ExecSessionManager(repository=repo, run_event_hub=hub)
    workspace = _build_workspace_handle(tmp_path)

    try:
        started = await manager.start_session(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id="call-1",
            workspace=workspace,
            command="sleep 30",
            cwd=workspace.execution_root,
            timeout_ms=5000,
            env=None,
            tty=False,
        )
        stopped = await manager.stop_for_run(
            run_id="run-1",
            exec_session_id=started.exec_session_id,
        )
    finally:
        await manager.close()

    assert stopped.status == ExecSessionStatus.STOPPED
