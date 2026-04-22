# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from pathlib import Path

from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository


def test_update_runtime_snapshot_persists_active_tools_json(tmp_path: Path) -> None:
    repo = AgentInstanceRepository(tmp_path / "instances.db")
    repo.upsert_instance(
        run_id="run-1",
        trace_id="trace-1",
        session_id="session-1",
        instance_id="instance-1",
        role_id="reader",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        status=InstanceStatus.IDLE,
    )

    repo.update_runtime_snapshot(
        "instance-1",
        runtime_system_prompt="prompt",
        runtime_tools_json='{"local_tools":[],"skill_tools":[],"mcp_tools":[]}',
        runtime_active_tools_json='["tool_search"]',
    )

    record = repo.get_instance("instance-1")
    assert record.runtime_system_prompt == "prompt"
    assert record.runtime_tools_json == (
        '{"local_tools":[],"skill_tools":[],"mcp_tools":[]}'
    )
    assert record.runtime_active_tools_json == '["tool_search"]'


def test_repository_migrates_runtime_active_tools_column(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_instances.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE agent_instances (
            run_id TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            instance_id TEXT PRIMARY KEY,
            role_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL DEFAULT '',
            conversation_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            runtime_system_prompt TEXT NOT NULL DEFAULT '',
            runtime_tools_json TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO agent_instances(
            run_id,
            trace_id,
            session_id,
            instance_id,
            role_id,
            workspace_id,
            conversation_id,
            status,
            runtime_system_prompt,
            runtime_tools_json,
            created_at,
            updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "run-1",
            "trace-1",
            "session-1",
            "instance-1",
            "reader",
            "workspace-1",
            "conversation-1",
            InstanceStatus.IDLE.value,
            "prompt",
            '{"local_tools":[],"skill_tools":[],"mcp_tools":[]}',
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    repo = AgentInstanceRepository(db_path)

    record = repo.get_instance("instance-1")
    assert record.runtime_active_tools_json == ""
