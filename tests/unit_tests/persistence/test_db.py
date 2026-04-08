from __future__ import annotations

import sqlite3
from pathlib import Path

from relay_teams.persistence.db import (
    SQLITE_BUSY_TIMEOUT_MS,
    is_retryable_sqlite_error,
    open_sqlite,
    run_sqlite_write_with_retry,
    sqlite_compile_options,
    sqlite_supports_fts5,
)


def test_open_sqlite_enables_busy_timeout_and_wal_for_file_db(tmp_path: Path) -> None:
    conn = open_sqlite(tmp_path / "relay_teams.db")
    try:
        foreign_keys = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
        busy_timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()

        assert foreign_keys == 1
        assert busy_timeout == SQLITE_BUSY_TIMEOUT_MS
        assert journal_mode == "wal"
    finally:
        conn.close()


def test_sqlite_compile_options_reports_fts5_support(tmp_path: Path) -> None:
    conn = open_sqlite(tmp_path / "compile-options.db")
    try:
        options = sqlite_compile_options(conn)
        assert "ENABLE_FTS5" in options
        assert sqlite_supports_fts5(conn) is True
    finally:
        conn.close()


def test_is_retryable_sqlite_error_matches_lock_contention() -> None:
    assert is_retryable_sqlite_error(sqlite3.OperationalError("database is locked"))
    assert is_retryable_sqlite_error(
        sqlite3.OperationalError("database table is locked")
    )
    assert not is_retryable_sqlite_error(sqlite3.OperationalError("no such table"))


def test_run_sqlite_write_with_retry_retries_transient_lock_errors(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "retry.db"
    conn = open_sqlite(db_path)
    try:
        conn.execute("CREATE TABLE items (value TEXT NOT NULL)")
        conn.commit()
        attempts = 0

        def operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise sqlite3.OperationalError("database is locked")
            conn.execute("INSERT INTO items(value) VALUES(?)", ("ok",))
            return "done"

        result = run_sqlite_write_with_retry(
            conn=conn,
            db_path=db_path,
            operation=operation,
            repository_name="test",
            operation_name="insert_item",
        )

        stored = conn.execute("SELECT value FROM items").fetchall()
        assert result == "done"
        assert attempts == 3
        assert [row[0] for row in stored] == ["ok"]
    finally:
        conn.close()
