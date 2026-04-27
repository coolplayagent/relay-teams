# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

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


@pytest.mark.asyncio
async def test_async_repository_methods_use_direct_sqlite_paths(
    tmp_path: Path,
) -> None:
    repository = AgentInstanceRepository(tmp_path / "agent_instances_async.db")
    try:
        await repository.upsert_instance_async(
            run_id="run-1",
            trace_id="run-1",
            session_id="session-1",
            instance_id="inst-coordinator",
            role_id="Coordinator",
            workspace_id="workspace-1",
            conversation_id="conversation-coordinator",
            status=InstanceStatus.RUNNING,
        )
        await repository.upsert_instance_async(
            run_id="run-1",
            trace_id="run-1",
            session_id="session-1",
            instance_id="inst-reviewer",
            role_id="reviewer",
            workspace_id="workspace-1",
            conversation_id="conversation-reviewer",
            status=InstanceStatus.IDLE,
            lifecycle=InstanceLifecycle.REUSABLE,
        )
        await repository.upsert_instance_async(
            run_id="subagent_run_1",
            trace_id="run-1",
            session_id="session-1",
            instance_id="inst-ephemeral",
            role_id="worker",
            workspace_id="workspace-1",
            conversation_id="conversation-worker",
            status=InstanceStatus.RUNNING,
            lifecycle=InstanceLifecycle.EPHEMERAL,
            parent_instance_id="inst-coordinator",
        )

        await repository.update_session_workspace_async(
            "session-1",
            workspace_id="workspace-updated",
        )

        all_records = await repository.list_all_async()
        assert [record.instance_id for record in all_records] == [
            "inst-coordinator",
            "inst-reviewer",
            "inst-ephemeral",
        ]
        assert {record.workspace_id for record in all_records} == {"workspace-updated"}
        assert [
            record.instance_id for record in await repository.list_by_run_async("run-1")
        ] == ["inst-coordinator", "inst-reviewer"]
        assert [
            record.instance_id
            for record in await repository.list_by_session_async("session-1")
        ] == ["inst-coordinator", "inst-reviewer", "inst-ephemeral"]
        assert [
            record.instance_id
            for record in await repository.list_running_async("run-1")
        ] == ["inst-coordinator"]

        assert (
            await repository.count_normal_mode_subagents_by_session_ids_async(()) == {}
        )
        assert await repository.count_normal_mode_subagents_by_session_ids_async(
            ("session-1", "missing-session")
        ) == {"session-1": 1}

        assert [
            record.role_id
            for record in await repository.list_session_role_instances_async(
                "session-1"
            )
        ] == ["Coordinator", "reviewer"]
        reviewer = await repository.get_session_role_instance_async(
            "session-1",
            "reviewer",
        )
        assert reviewer is not None
        assert reviewer.instance_id == "inst-reviewer"
        assert (
            await repository.get_session_role_instance_id_async(
                "session-1",
                "reviewer",
            )
            == "inst-reviewer"
        )
        assert (
            await repository.get_session_role_instance_id_async(
                "session-1",
                "missing-role",
            )
            is None
        )

        failed_instances = await repository.mark_running_instances_failed_async()
        assert failed_instances == ("inst-coordinator", "inst-ephemeral")
        assert await repository.mark_running_instances_failed_async() == ()
        assert (
            await repository.get_instance_async("inst-coordinator")
        ).status == InstanceStatus.FAILED

        await repository.delete_instance_async("inst-reviewer")
        with pytest.raises(KeyError):
            await repository.get_instance_async("inst-reviewer")

        await repository.delete_by_session_async("session-1")
        assert await repository.list_all_async() == ()
    finally:
        await repository.close_async()


@pytest.mark.asyncio
async def test_async_methods_preserve_existing_instance_fields(
    tmp_path: Path,
) -> None:
    repository = AgentInstanceRepository(tmp_path / "agent_instances_async.db")
    try:
        await repository.upsert_instance_async(
            run_id="run-1",
            trace_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            status=InstanceStatus.IDLE,
            lifecycle=InstanceLifecycle.EPHEMERAL,
            parent_instance_id="parent-1",
        )
        await repository.upsert_instance_async(
            run_id="run-1",
            trace_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            status=InstanceStatus.RUNNING,
        )

        record = await repository.get_instance_async("inst-1")
        assert record.status == InstanceStatus.RUNNING
        assert record.lifecycle == InstanceLifecycle.EPHEMERAL
        assert record.parent_instance_id == "parent-1"

        await repository.update_runtime_snapshot_async(
            "inst-1",
            runtime_system_prompt="system",
            runtime_tools_json='{"tools":[]}',
        )
        await repository.upsert_instance_async(
            run_id="run-2",
            trace_id="run-2",
            session_id="session-1",
            instance_id="inst-2",
            role_id="reviewer",
            workspace_id="workspace-1",
            conversation_id="conversation-2",
            status=InstanceStatus.RUNNING,
        )

        assert (
            await repository.get_session_role_instance_id_async("session-1", "reviewer")
        ) == "inst-2"
        assert len(await repository.list_by_run_async("run-1")) == 1
        assert len(await repository.list_by_session_async("session-1")) == 2

        failed_ids = await repository.mark_running_instances_failed_async()
        assert failed_ids == ("inst-1", "inst-2")
        assert (
            await repository.get_instance_async("inst-2")
        ).status == InstanceStatus.FAILED

        await repository.delete_instance_async("inst-2")
        assert len(await repository.list_all_async()) == 1
        await repository.delete_by_session_async("session-1")
        assert await repository.list_all_async() == ()
    finally:
        await repository.close_async()
