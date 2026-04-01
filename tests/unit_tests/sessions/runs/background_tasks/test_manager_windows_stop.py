# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from agent_teams.sessions.runs.background_tasks.manager import BackgroundTaskManager
from agent_teams.sessions.runs.background_tasks.models import BackgroundTaskStatus
from agent_teams.sessions.runs.background_tasks.repository import (
    BackgroundTaskRepository,
)
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
@pytest.mark.skipif(os.name != "nt", reason="Windows-specific stop regression")
async def test_background_task_manager_stop_unblocks_windows_pipe_runtime(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-terminal-stop-win-pipe.db")
    hub = RunEventHub()
    manager = BackgroundTaskManager(repository=repo, run_event_hub=hub)
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
                'python -c "import time,sys; '
                "print('ready', flush=True); time.sleep(90)\""
            ),
            cwd=workspace.execution_root,
            timeout_ms=30_000,
            env=None,
            tty=False,
        )
        updated, completed = await manager.interact_for_run(
            run_id="run-1",
            background_task_id=started.background_task_id,
            chars="",
            yield_time_ms=1_000,
            is_initial_poll=True,
        )
        stopped = await asyncio.wait_for(
            manager.stop_for_run(
                run_id="run-1",
                background_task_id=started.background_task_id,
            ),
            timeout=10,
        )
    finally:
        await manager.close()

    assert completed is False
    assert updated.status == BackgroundTaskStatus.RUNNING
    assert stopped.status == BackgroundTaskStatus.STOPPED
