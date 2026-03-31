# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
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
    updated_payload = None
    completed_payload = None
    while not queue.empty():
        event = queue.get_nowait()
        event_types.append(event.event_type)
        if event.event_type == RunEventType.EXEC_SESSION_UPDATED:
            updated_payload = json.loads(event.payload_json)
        if event.event_type == RunEventType.EXEC_SESSION_COMPLETED:
            completed_payload = json.loads(event.payload_json)
    assert RunEventType.EXEC_SESSION_STARTED in event_types
    assert RunEventType.EXEC_SESSION_COMPLETED in event_types
    assert updated_payload is not None
    assert "output_excerpt" not in updated_payload
    assert updated_payload["delta"] == "hello\n"
    assert completed_payload is not None
    assert completed_payload["output_excerpt"] == "hello\n"


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


@pytest.mark.asyncio
async def test_exec_session_manager_stop_marks_tty_terminal_stopped(
    tmp_path: Path,
) -> None:
    repo = ExecSessionRepository(tmp_path / "background-terminal-stop-tty.db")
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
            command=(
                "python -u -c \"import time; print('ready', flush=True); "
                "value = input(); print('echo:' + value, flush=True); time.sleep(30)\""
            ),
            cwd=workspace.execution_root,
            timeout_ms=5000,
            env=None,
            tty=True,
        )
        _, _ = await manager.interact_for_run(
            run_id="run-1",
            exec_session_id=started.exec_session_id,
            chars="hello\n",
            yield_time_ms=5000,
        )
        stopped = await asyncio.wait_for(
            manager.stop_for_run(
                run_id="run-1",
                exec_session_id=started.exec_session_id,
            ),
            timeout=5,
        )
    finally:
        await manager.close()

    assert stopped.status == ExecSessionStatus.STOPPED
