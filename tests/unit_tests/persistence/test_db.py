from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from relay_teams.persistence.db import (
    SQLITE_BUSY_TIMEOUT_MS,
    async_sqlite_supports_fts5,
    is_retryable_sqlite_error,
    open_async_sqlite,
    open_sqlite,
    run_async_sqlite_write_with_retry,
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


@pytest.mark.asyncio
async def test_open_async_sqlite_enables_busy_timeout_and_wal_for_file_db(
    tmp_path: Path,
) -> None:
    conn = await open_async_sqlite(tmp_path / "relay_teams_async.db")
    try:
        foreign_keys_row = await (await conn.execute("PRAGMA foreign_keys")).fetchone()
        busy_timeout_row = await (await conn.execute("PRAGMA busy_timeout")).fetchone()
        journal_mode_row = await (await conn.execute("PRAGMA journal_mode")).fetchone()

        assert foreign_keys_row is not None
        assert busy_timeout_row is not None
        assert journal_mode_row is not None
        assert int(foreign_keys_row[0]) == 1
        assert int(busy_timeout_row[0]) == SQLITE_BUSY_TIMEOUT_MS
        assert str(journal_mode_row[0]).lower() == "wal"
        assert await async_sqlite_supports_fts5(conn) is True
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_run_async_sqlite_write_with_retry_retries_transient_lock_errors(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "async_retry.db"
    conn = await open_async_sqlite(db_path)
    try:
        await conn.execute("CREATE TABLE items (value TEXT NOT NULL)")
        await conn.commit()
        attempts = 0

        async def operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise sqlite3.OperationalError("database is locked")
            await conn.execute("INSERT INTO items(value) VALUES(?)", ("ok",))
            return "done"

        result = await run_async_sqlite_write_with_retry(
            conn=conn,
            db_path=db_path,
            operation=operation,
            repository_name="test",
            operation_name="insert_item",
        )

        rows = await (await conn.execute("SELECT value FROM items")).fetchall()
        assert result == "done"
        assert attempts == 3
        assert [row[0] for row in rows] == ["ok"]
    finally:
        await conn.close()
