# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

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
