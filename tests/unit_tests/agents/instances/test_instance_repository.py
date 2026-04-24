# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from pathlib import Path

from relay_teams.agents.instances.enums import InstanceLifecycle, InstanceStatus
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository


def test_repository_migrates_lifecycle_columns_for_existing_tables(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy_agent_instances.db"
    timestamp = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(db_path) as conn:
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
                run_id, trace_id, session_id, instance_id, role_id,
                workspace_id, conversation_id, status, runtime_system_prompt,
                runtime_tools_json, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-1",
                "trace-1",
                "session-1",
                "inst-1",
                "writer",
                "workspace-1",
                "conversation-1",
                InstanceStatus.IDLE.value,
                "",
                "",
                timestamp,
                timestamp,
            ),
        )

    repository = AgentInstanceRepository(db_path)

    record = repository.get_instance("inst-1")
    assert record.lifecycle == InstanceLifecycle.REUSABLE
    assert record.parent_instance_id is None
    with sqlite3.connect(db_path) as conn:
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(agent_instances)")
        }
    assert "lifecycle" in columns
    assert "parent_instance_id" in columns


def test_upsert_preserves_existing_lifecycle_when_not_explicit(
    tmp_path: Path,
) -> None:
    repository = AgentInstanceRepository(tmp_path / "agent_instances_lifecycle.db")
    repository.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="writer",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        status=InstanceStatus.IDLE,
        lifecycle=InstanceLifecycle.EPHEMERAL,
        parent_instance_id="inst-parent",
    )

    repository.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="writer",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        status=InstanceStatus.RUNNING,
    )

    record = repository.get_instance("inst-1")
    assert record.status == InstanceStatus.RUNNING
    assert record.lifecycle == InstanceLifecycle.EPHEMERAL
    assert record.parent_instance_id == "inst-parent"

    repository.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="writer",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        status=InstanceStatus.IDLE,
        parent_instance_id="inst-new-parent",
    )

    reparented = repository.get_instance("inst-1")
    assert reparented.lifecycle == InstanceLifecycle.EPHEMERAL
    assert reparented.parent_instance_id == "inst-new-parent"

    repository.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="writer",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        status=InstanceStatus.IDLE,
        lifecycle=InstanceLifecycle.REUSABLE,
    )

    refreshed = repository.get_instance("inst-1")
    assert refreshed.lifecycle == InstanceLifecycle.REUSABLE
    assert refreshed.parent_instance_id == "inst-new-parent"
