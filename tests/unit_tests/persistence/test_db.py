from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import cast

import aiosqlite
import pytest

import relay_teams.persistence.db as db_module
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


def test_open_sqlite_falls_back_to_shared_memory_when_file_open_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    real_connect = sqlite3.connect
    calls: list[tuple[str, bool]] = []

    def fake_connect(
        database: str,
        *,
        timeout: float,
        check_same_thread: bool = True,
        uri: bool = False,
    ) -> sqlite3.Connection:
        _ = (timeout, check_same_thread)
        calls.append((database, uri))
        if len(calls) == 1:
            raise sqlite3.OperationalError("readonly")
        return real_connect(
            database,
            timeout=timeout,
            check_same_thread=check_same_thread,
            uri=uri,
        )

    monkeypatch.setattr(db_module.sqlite3, "connect", fake_connect)

    conn = open_sqlite(tmp_path / "readonly" / "relay_teams.db")
    try:
        assert calls == [
            (str(tmp_path / "readonly" / "relay_teams.db"), False),
            (db_module.MEMORY_DSN, True),
        ]
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


def test_resolved_db_path_key_caches_absolute_paths(tmp_path: Path) -> None:
    db_path = (tmp_path / "relay_teams.db").resolve()
    db_module._RESOLVED_DB_PATH_KEYS.clear()

    first = db_module._resolved_db_path_key(db_path)
    second = db_module._resolved_db_path_key(db_path)

    assert first == str(db_path)
    assert second == first
    assert db_module._RESOLVED_DB_PATH_KEYS[str(db_path)] == str(db_path)


def test_resolved_db_path_key_resolves_relative_paths_from_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_module._RESOLVED_DB_PATH_KEYS.clear()
    monkeypatch.chdir(tmp_path)

    resolved = db_module._resolved_db_path_key(Path("relative.db"))

    assert resolved == str((tmp_path / "relative.db").resolve())


def test_resolved_db_path_key_returns_existing_cached_value_after_resolve(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = (tmp_path / "relay_teams.db").resolve()
    db_module._RESOLVED_DB_PATH_KEYS.clear()
    real_resolve = Path.resolve
    resolve_calls = 0

    def fake_resolve(self: Path, *, strict: bool = False) -> Path:
        nonlocal resolve_calls
        resolved = real_resolve(self, strict=strict)
        if resolved == db_path:
            resolve_calls += 1
            if resolve_calls == 1:
                db_module._RESOLVED_DB_PATH_KEYS[str(db_path)] = "cached-after-resolve"
        return resolved

    monkeypatch.setattr(db_module.Path, "resolve", fake_resolve)

    resolved = db_module._resolved_db_path_key(db_path)

    assert resolved == "cached-after-resolve"


@pytest.mark.asyncio
async def test_write_coordinators_reuse_same_lock_for_same_path(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "relay_teams.db"
    db_module._CROSS_WRITE_COORDINATORS.clear()
    db_module._WRITE_COORDINATORS.clear()
    db_module._ASYNC_WRITE_COORDINATORS.clear()
    db_module._RESOLVED_DB_PATH_KEYS.clear()

    cross_first = db_module._cross_write_coordinator_for(db_path)
    cross_second = db_module._cross_write_coordinator_for(db_path)
    sync_first = db_module._write_coordinator_for(db_path)
    sync_second = db_module._write_coordinator_for(db_path)
    async_first = db_module._async_write_coordinator_for(db_path)
    async_second = db_module._async_write_coordinator_for(db_path)

    assert cross_first is cross_second
    assert sync_first is sync_second
    assert async_first is async_second


def test_async_write_coordinator_is_scoped_to_event_loop(tmp_path: Path) -> None:
    db_path = tmp_path / "relay_teams.db"
    db_module._ASYNC_WRITE_COORDINATORS.clear()
    db_module._RESOLVED_DB_PATH_KEYS.clear()

    async def bind_lock_to_current_loop() -> asyncio.Lock:
        lock = db_module._async_write_coordinator_for(db_path)
        await lock.acquire()
        waiter = asyncio.create_task(lock.acquire())
        await asyncio.sleep(0)

        assert not waiter.done()

        lock.release()
        await waiter
        lock.release()
        return lock

    first = asyncio.run(bind_lock_to_current_loop())
    second = asyncio.run(bind_lock_to_current_loop())

    assert first is not second


def test_cross_write_coordinator_allows_sync_reentry() -> None:
    coordinator = db_module.CrossModeWriteCoordinator()

    first_token = coordinator.acquire_sync()
    second_token = coordinator.acquire_sync()
    coordinator.release(second_token)
    coordinator.release(first_token)


@pytest.mark.asyncio
async def test_cross_write_coordinator_allows_async_reentry() -> None:
    coordinator = db_module.CrossModeWriteCoordinator()

    first_token = await coordinator.acquire_async()
    second_token = await coordinator.acquire_async()
    coordinator.release(second_token)
    coordinator.release(first_token)


@pytest.mark.asyncio
async def test_cross_write_coordinator_releases_cancelled_async_acquire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = db_module.CrossModeWriteCoordinator()

    async def _cancel_after_acquire(
        func: Callable[[tuple[str, int]], bool],
        token: tuple[str, int],
    ) -> bool:
        assert func(token) is True
        raise asyncio.CancelledError

    monkeypatch.setattr(db_module.asyncio, "to_thread", _cancel_after_acquire)

    with pytest.raises(asyncio.CancelledError):
        await coordinator.acquire_async()

    assert coordinator._owner is None
    assert coordinator._depth == 0


@pytest.mark.asyncio
async def test_cross_write_coordinator_cancelled_reentry_preserves_outer_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = db_module.CrossModeWriteCoordinator()
    first_token = await coordinator.acquire_async()

    async def _cancel_after_reentry(
        func: Callable[[tuple[str, int]], bool],
        token: tuple[str, int],
    ) -> bool:
        assert func(token) is True
        raise asyncio.CancelledError

    monkeypatch.setattr(db_module.asyncio, "to_thread", _cancel_after_reentry)

    with pytest.raises(asyncio.CancelledError):
        await coordinator.acquire_async()

    assert coordinator._owner == first_token
    assert coordinator._depth == 1

    coordinator.release(first_token)
    assert coordinator._owner is None
    assert coordinator._depth == 0


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


def test_run_sqlite_write_with_retry_rolls_back_failed_operation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rollback.db"
    conn = open_sqlite(db_path)
    try:
        conn.execute("CREATE TABLE items (value TEXT NOT NULL)")
        conn.commit()

        def failed_operation() -> None:
            conn.execute("INSERT INTO items(value) VALUES(?)", ("stale",))
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            run_sqlite_write_with_retry(
                conn=conn,
                db_path=db_path,
                operation=failed_operation,
                repository_name="test",
                operation_name="failed_insert",
            )

        def committed_operation() -> None:
            conn.execute("INSERT INTO items(value) VALUES(?)", ("ok",))

        run_sqlite_write_with_retry(
            conn=conn,
            db_path=db_path,
            operation=committed_operation,
            repository_name="test",
            operation_name="insert_item",
        )

        stored = conn.execute("SELECT value FROM items").fetchall()
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


@pytest.mark.asyncio
async def test_run_async_sqlite_write_with_retry_rolls_back_cancelled_operation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "async_rollback.db"
    conn = await open_async_sqlite(db_path)
    try:
        await conn.execute("CREATE TABLE items (value TEXT NOT NULL)")
        await conn.commit()

        async def cancelled_operation() -> None:
            await conn.execute("INSERT INTO items(value) VALUES(?)", ("stale",))
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await run_async_sqlite_write_with_retry(
                conn=conn,
                db_path=db_path,
                operation=cancelled_operation,
                repository_name="test",
                operation_name="cancelled_insert",
            )

        async def committed_operation() -> None:
            await conn.execute("INSERT INTO items(value) VALUES(?)", ("ok",))

        await run_async_sqlite_write_with_retry(
            conn=conn,
            db_path=db_path,
            operation=committed_operation,
            repository_name="test",
            operation_name="insert_item",
        )

        rows = await (await conn.execute("SELECT value FROM items")).fetchall()
        assert [row[0] for row in rows] == ["ok"]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_run_async_sqlite_write_with_retry_does_not_rollback_while_waiting(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "async_waiting_cancel.db"
    conn = await open_async_sqlite(db_path)
    try:
        await conn.execute("CREATE TABLE items (value TEXT NOT NULL)")
        await conn.commit()
        first_operation_started = asyncio.Event()
        release_first_operation = asyncio.Event()

        async def slow_operation() -> None:
            await conn.execute("INSERT INTO items(value) VALUES(?)", ("first",))
            first_operation_started.set()
            await release_first_operation.wait()

        first_task = asyncio.create_task(
            run_async_sqlite_write_with_retry(
                conn=conn,
                db_path=db_path,
                operation=slow_operation,
                repository_name="test",
                operation_name="slow_insert",
            )
        )
        await first_operation_started.wait()

        second_task = asyncio.create_task(
            run_async_sqlite_write_with_retry(
                conn=conn,
                db_path=db_path,
                operation=lambda: asyncio.sleep(0),
                repository_name="test",
                operation_name="waiting_insert",
            )
        )
        await asyncio.sleep(0.01)
        second_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await second_task

        release_first_operation.set()
        await first_task

        rows = await (await conn.execute("SELECT value FROM items")).fetchall()
        assert [row[0] for row in rows] == ["first"]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_run_async_sqlite_write_with_retry_uses_explicit_lock(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "async_retry_lock.db"
    conn = await open_async_sqlite(db_path)
    lock = asyncio.Lock()
    try:
        await conn.execute("CREATE TABLE items (value TEXT NOT NULL)")
        await conn.commit()

        async def operation() -> str:
            await conn.execute("INSERT INTO items(value) VALUES(?)", ("locked",))
            return "done"

        result = await run_async_sqlite_write_with_retry(
            conn=conn,
            db_path=db_path,
            operation=operation,
            lock=lock,
            repository_name="test",
            operation_name="insert_item",
        )

        rows = await (await conn.execute("SELECT value FROM items")).fetchall()
        assert result == "done"
        assert [row[0] for row in rows] == ["locked"]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_async_rollback_quietly_ignores_sqlite_errors() -> None:
    class _FailingRollbackConnection:
        async def rollback(self) -> None:
            raise sqlite3.Error("rollback failed")

    await db_module._async_rollback_quietly(
        cast(aiosqlite.Connection, _FailingRollbackConnection())
    )
