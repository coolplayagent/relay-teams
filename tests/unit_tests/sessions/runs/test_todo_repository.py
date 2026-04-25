# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from relay_teams.sessions.runs.todo_models import (
    TodoItem,
    TodoStatus,
    build_todo_snapshot,
)
from relay_teams.sessions.runs.todo_repository import TodoRepository


def test_get_returns_none_for_malformed_items_json(tmp_path: Path) -> None:
    db_path = tmp_path / "todo-repository-invalid-json.db"
    repository = TodoRepository(db_path)
    _insert_raw_row(
        db_path=db_path,
        run_id="run-invalid",
        session_id="session-1",
        items_json="{",
    )

    snapshot = repository.get("run-invalid")

    assert snapshot is None


def test_list_by_session_skips_rows_with_non_list_items_payload(tmp_path: Path) -> None:
    db_path = tmp_path / "todo-repository-invalid-payload.db"
    repository = TodoRepository(db_path)
    _ = repository.upsert(
        build_todo_snapshot(
            run_id="run-valid",
            session_id="session-1",
            items=(TodoItem(content="Inspect repo", status=TodoStatus.PENDING),),
            version=1,
        )
    )
    _insert_raw_row(
        db_path=db_path,
        run_id="run-invalid",
        session_id="session-1",
        items_json='{"status":"pending"}',
    )

    snapshots = repository.list_by_session("session-1")

    assert [snapshot.run_id for snapshot in snapshots] == ["run-valid"]


@pytest.mark.asyncio
async def test_async_todo_repository_methods_share_persisted_state(
    tmp_path: Path,
) -> None:
    repository = TodoRepository(tmp_path / "todo-repository-async.db")

    try:
        persisted = await repository.upsert_async(
            build_todo_snapshot(
                run_id="run-async",
                session_id="session-1",
                items=(TodoItem(content="Inspect repo", status=TodoStatus.PENDING),),
                version=1,
            )
        )
        fetched = await repository.get_async("run-async")
        listed = await repository.list_by_session_async("session-1")
        await repository.delete_by_run_async("run-async")
        deleted = await repository.get_async("run-async")
    finally:
        await repository.close_async()

    assert persisted.run_id == "run-async"
    assert fetched is not None
    assert fetched.items == persisted.items
    assert tuple(snapshot.run_id for snapshot in listed) == ("run-async",)
    assert deleted is None


@pytest.mark.asyncio
async def test_todo_repository_async_hot_paths_do_not_reinitialize_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = TodoRepository(tmp_path / "todo-repository-no-reinit.db")
    run_snapshot = build_todo_snapshot(
        run_id="run-async",
        session_id="session-1",
        items=(TodoItem(content="Inspect repo", status=TodoStatus.PENDING),),
        version=1,
    )
    session_delete_snapshot = build_todo_snapshot(
        run_id="run-delete-session",
        session_id="session-delete",
        items=(TodoItem(content="Delete by session", status=TodoStatus.PENDING),),
        version=1,
    )
    run_delete_snapshot = build_todo_snapshot(
        run_id="run-delete",
        session_id="session-2",
        items=(TodoItem(content="Delete by run", status=TodoStatus.PENDING),),
        version=1,
    )

    async def _fail_init() -> None:
        raise AssertionError("async schema init should not run on hot paths")

    try:
        await repository._init_tables_async()
        monkeypatch.setattr(repository, "_init_tables_async", _fail_init)
        persisted = await repository.upsert_async(run_snapshot)
        fetched = await repository.get_async("run-async")
        listed = await repository.list_by_session_async("session-1")
        await repository.upsert_async(session_delete_snapshot)
        await repository.delete_by_session_async("session-delete")
        await repository.upsert_async(run_delete_snapshot)
        await repository.delete_by_run_async("run-delete")
    finally:
        await repository.close_async()

    assert persisted.run_id == "run-async"
    assert fetched is not None
    assert tuple(snapshot.run_id for snapshot in listed) == ("run-async",)


def _insert_raw_row(
    *,
    db_path: Path,
    run_id: str,
    session_id: str,
    items_json: str,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO run_todos(
                run_id,
                session_id,
                items_json,
                version,
                updated_at,
                updated_by_role_id,
                updated_by_instance_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                session_id,
                items_json,
                1,
                datetime.now(tz=timezone.utc).isoformat(),
                "MainAgent",
                "instance-1",
            ),
        )
        conn.commit()
