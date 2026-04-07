from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading
import time

import pytest

from relay_teams.sessions.session_repository import SessionRepository


def test_list_all_tolerates_blank_session_metadata_json(tmp_path: Path) -> None:
    db_path = tmp_path / "session_repository_blank_metadata.db"
    repository = SessionRepository(db_path)
    _insert_session_row(
        db_path,
        session_id="session-blank",
        metadata_json="",
    )

    records = repository.list_all()

    assert len(records) == 1
    assert records[0].session_id == "session-blank"
    assert records[0].metadata == {}


def test_list_all_tolerates_invalid_session_metadata_json(tmp_path: Path) -> None:
    db_path = tmp_path / "session_repository_invalid_metadata.db"
    repository = SessionRepository(db_path)
    _insert_session_row(
        db_path,
        session_id="session-invalid",
        metadata_json="{",
    )

    records = repository.list_all()

    assert len(records) == 1
    assert records[0].session_id == "session-invalid"
    assert records[0].metadata == {}


def test_list_all_filters_non_string_metadata_values(tmp_path: Path) -> None:
    db_path = tmp_path / "session_repository_mixed_metadata.db"
    repository = SessionRepository(db_path)
    _insert_session_row(
        db_path,
        session_id="session-mixed",
        metadata_json=(
            '{"title":"Ops Run","retries":2,"enabled":true,"payload":{"bad":1}}'
        ),
    )

    records = repository.list_all()

    assert len(records) == 1
    assert records[0].metadata == {
        "title": "Ops Run",
        "retries": "2",
        "enabled": "True",
    }


@pytest.mark.parametrize("started_at", ["", "None", "null"])
def test_list_all_tolerates_missing_or_none_like_started_at(
    tmp_path: Path,
    started_at: str,
) -> None:
    db_path = tmp_path / "session_repository_blank_started_at.db"
    repository = SessionRepository(db_path)
    _insert_session_row(
        db_path,
        session_id="session-blank-started-at",
        metadata_json="{}",
        started_at=started_at,
    )

    records = repository.list_all()

    assert len(records) == 1
    assert records[0].session_id == "session-blank-started-at"
    assert records[0].started_at is None
    assert records[0].can_switch_mode is True


def test_list_all_tolerates_blank_created_at_by_falling_back_to_updated_at(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_repository_blank_created_at.db"
    repository = SessionRepository(db_path)
    updated_at = datetime.now(tz=timezone.utc).isoformat()
    _insert_session_row(
        db_path,
        session_id="session-blank-created-at",
        metadata_json="{}",
        created_at="",
        updated_at=updated_at,
    )

    records = repository.list_all()

    assert len(records) == 1
    assert records[0].session_id == "session-blank-created-at"
    assert records[0].created_at == datetime.fromisoformat(updated_at)
    assert records[0].updated_at == datetime.fromisoformat(updated_at)


def test_list_all_skips_rows_with_blank_session_id(tmp_path: Path) -> None:
    db_path = tmp_path / "session_repository_blank_session_id.db"
    repository = SessionRepository(db_path)
    _insert_session_row(
        db_path,
        session_id="session-valid",
        metadata_json="{}",
    )
    _insert_session_row(
        db_path,
        session_id="",
        metadata_json="{}",
    )

    records = repository.list_all()

    assert [record.session_id for record in records] == ["session-valid"]


@pytest.mark.parametrize("session_id", ["None", "null"])
def test_list_all_skips_rows_with_none_like_session_id(
    tmp_path: Path,
    session_id: str,
) -> None:
    db_path = tmp_path / "session_repository_none_like_session_id.db"
    repository = SessionRepository(db_path)
    _insert_session_row(
        db_path,
        session_id="session-valid",
        metadata_json="{}",
    )
    _insert_session_row(
        db_path,
        session_id=session_id,
        metadata_json="{}",
    )

    records = repository.list_all()

    assert [record.session_id for record in records] == ["session-valid"]


def test_get_raises_key_error_for_invalid_persisted_row(tmp_path: Path) -> None:
    db_path = tmp_path / "session_repository_invalid_get.db"
    repository = SessionRepository(db_path)
    _insert_session_row(
        db_path,
        session_id="None",
        metadata_json="{}",
    )

    with pytest.raises(KeyError):
        repository.get("None")


@pytest.mark.parametrize("started_at", ["", "None", "null"])
def test_repository_init_normalizes_missing_or_none_like_started_at_for_mark_started(
    tmp_path: Path,
    started_at: str,
) -> None:
    db_path = tmp_path / "session_repository_started_at_cleanup.db"
    now = datetime.now(tz=timezone.utc).isoformat()
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL DEFAULT '',
            project_kind TEXT NOT NULL DEFAULT 'workspace',
            project_id TEXT NOT NULL DEFAULT '',
            metadata   TEXT NOT NULL,
            session_mode TEXT NOT NULL DEFAULT 'normal',
            normal_root_role_id TEXT,
            orchestration_preset_id TEXT,
            started_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO sessions(
            session_id,
            workspace_id,
            project_kind,
            project_id,
            metadata,
            session_mode,
            normal_root_role_id,
            orchestration_preset_id,
            started_at,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "session-preexisting-blank-started-at",
            "default",
            "workspace",
            "default",
            "{}",
            "normal",
            None,
            None,
            started_at,
            now,
            now,
        ),
    )
    connection.commit()
    connection.close()

    repository = SessionRepository(db_path)

    record = repository.mark_started("session-preexisting-blank-started-at")

    assert record.started_at is not None
    assert record.can_switch_mode is False


def test_mark_started_retries_transient_write_lock(tmp_path: Path) -> None:
    db_path = tmp_path / "session_repository_retry.db"
    repository = SessionRepository(db_path)
    repository.create(session_id="session-retry", workspace_id="default")
    repository._conn.execute("PRAGMA busy_timeout = 0")

    blocker = sqlite3.connect(db_path, check_same_thread=False)
    blocker.execute("PRAGMA busy_timeout = 0")
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute(
        "UPDATE sessions SET updated_at=updated_at WHERE session_id=?",
        ("session-retry",),
    )

    released = threading.Event()

    def release_lock() -> None:
        time.sleep(0.05)
        blocker.commit()
        blocker.close()
        released.set()

    thread = threading.Thread(target=release_lock)
    thread.start()

    record = repository.mark_started("session-retry")

    thread.join(timeout=1)

    assert released.is_set()
    assert record.started_at is not None
    assert record.can_switch_mode is False


def _insert_session_row(
    db_path: Path,
    *,
    session_id: str,
    metadata_json: str,
    started_at: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> None:
    now = datetime.now(tz=timezone.utc).isoformat()
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        INSERT INTO sessions(
            session_id,
            workspace_id,
            project_kind,
            project_id,
            metadata,
            session_mode,
            normal_root_role_id,
            orchestration_preset_id,
            started_at,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            "default",
            "workspace",
            "default",
            metadata_json,
            "normal",
            None,
            None,
            started_at,
            now if created_at is None else created_at,
            now if updated_at is None else updated_at,
        ),
    )
    connection.commit()
    connection.close()
