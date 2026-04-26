# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from pathlib import Path
import threading

import aiosqlite
import pytest
from pytest import MonkeyPatch

import relay_teams.persistence.sqlite_repository as sqlite_repository_module
from relay_teams.persistence.sqlite_repository import (
    BlockingAsyncSqliteConnection,
    SharedSqliteRepository,
    async_fetchone,
)
from relay_teams.retrieval.sqlite_store import SqliteFts5RetrievalStore


class _DummyRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path, *, repository_name: str | None = None) -> None:
        super().__init__(db_path, repository_name=repository_name)


def test_shared_sqlite_repository_run_write_uses_retry_helper(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    repo = _DummyRepository(tmp_path / "shared_repo.db")
    calls: list[tuple[Path, object, str, str]] = []

    def fake_run_sqlite_write_with_retry(
        *,
        conn: BlockingAsyncSqliteConnection,
        db_path: Path,
        operation: Callable[[], str],
        lock: object,
        repository_name: str,
        operation_name: str,
        max_retries: int = 8,
    ) -> str:
        calls.append((db_path, lock, repository_name, operation_name))
        return operation()

    monkeypatch.setattr(
        sqlite_repository_module,
        "run_sqlite_write_with_retry",
        fake_run_sqlite_write_with_retry,
    )

    result = repo._run_write(
        operation_name="insert_item",
        operation=lambda: "ok",
    )

    assert result == "ok"
    assert calls == [
        (tmp_path / "shared_repo.db", repo._lock, "_DummyRepository", "insert_item")
    ]


def test_shared_sqlite_repository_run_read_uses_repository_lock(
    tmp_path: Path,
) -> None:
    repo = _DummyRepository(tmp_path / "shared_repo_read.db")
    started = threading.Event()
    finished = threading.Event()

    def worker() -> None:
        started.set()
        repo._run_read(lambda: finished.set())

    with repo._lock:
        thread = threading.Thread(target=worker)
        thread.start()
        assert started.wait(timeout=1)
        assert finished.wait(timeout=0.1) is False

    thread.join(timeout=1)

    assert finished.wait(timeout=1)
    assert thread.is_alive() is False


def test_shared_sqlite_repository_defaults_repository_name_to_class_name(
    tmp_path: Path,
) -> None:
    repo = _DummyRepository(tmp_path / "shared_repo_name.db")

    assert repo._repository_name == "_DummyRepository"


def test_sqlite_retrieval_store_uses_stable_repository_name(tmp_path: Path) -> None:
    store = SqliteFts5RetrievalStore(tmp_path / "retrieval.db")

    assert store._repository_name == "retrieval.sqlite"


@pytest.mark.asyncio
async def test_shared_sqlite_repository_run_async_write_uses_retry_helper(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    repo = _DummyRepository(tmp_path / "async_shared_repo.db")
    calls: list[tuple[Path, str, str]] = []
    locks: list[asyncio.Lock] = []

    async def fake_run_async_sqlite_write_with_retry(
        *,
        conn: aiosqlite.Connection,
        db_path: Path,
        operation: Callable[[], Awaitable[str]],
        lock: asyncio.Lock,
        repository_name: str,
        operation_name: str,
        max_retries: int = 8,
    ) -> str:
        _ = conn
        _ = max_retries
        locks.append(lock)
        calls.append((db_path, repository_name, operation_name))
        return await operation()

    monkeypatch.setattr(
        sqlite_repository_module,
        "run_async_sqlite_write_with_retry",
        fake_run_async_sqlite_write_with_retry,
    )

    try:
        result = await repo._run_async_write(
            operation_name="insert_item",
            operation=lambda _conn: _async_value("ok"),
        )
    finally:
        await repo.close_async()

    assert result == "ok"
    assert calls == [
        (
            tmp_path / "async_shared_repo.db",
            "_DummyRepository",
            "insert_item",
        )
    ]
    assert len(locks) == 1
    assert not locks[0].locked()


@pytest.mark.asyncio
async def test_shared_sqlite_repository_run_async_read_uses_lock(
    tmp_path: Path,
) -> None:
    repo = _DummyRepository(tmp_path / "async_shared_repo_read.db")
    try:
        result = await repo._run_async_read(lambda _conn: _async_value("ok"))
    finally:
        await repo.close_async()

    assert result == "ok"


@pytest.mark.asyncio
async def test_shared_sqlite_repository_async_helpers_use_retry_helper(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    repo = _DummyRepository(tmp_path / "shared_repo_async.db")
    calls: list[tuple[Path, str, str]] = []
    locks: list[asyncio.Lock] = []

    async def fake_run_async_sqlite_write_with_retry(
        *,
        conn: aiosqlite.Connection,
        db_path: Path,
        operation: Callable[[], Awaitable[str]],
        lock: asyncio.Lock,
        repository_name: str,
        operation_name: str,
        max_retries: int = 8,
    ) -> str:
        _ = conn
        _ = max_retries
        locks.append(lock)
        calls.append((db_path, repository_name, operation_name))
        return await operation()

    monkeypatch.setattr(
        sqlite_repository_module,
        "run_async_sqlite_write_with_retry",
        fake_run_async_sqlite_write_with_retry,
    )

    try:
        result = await repo._run_async_write(
            operation_name="insert_item_async",
            operation=lambda _conn: _async_value("ok"),
        )
    finally:
        await repo.close_async()

    assert result == "ok"
    assert calls == [
        (
            tmp_path / "shared_repo_async.db",
            "_DummyRepository",
            "insert_item_async",
        )
    ]
    assert len(locks) == 1
    assert not locks[0].locked()


@pytest.mark.asyncio
async def test_shared_sqlite_repository_sync_facade_writes_are_visible_to_async_helpers(
    tmp_path: Path,
) -> None:
    repo = _DummyRepository(tmp_path / "shared_repo_unified.db")
    try:
        repo._conn.execute("CREATE TABLE items (value TEXT NOT NULL)")
        repo._conn.execute("INSERT INTO items(value) VALUES(?)", ("sync",))
        repo._conn.commit()

        row = await repo._run_async_read(
            lambda conn: async_fetchone(conn, "SELECT value FROM items")
        )
    finally:
        await repo.close_async()

    assert row is not None
    assert row["value"] == "sync"


async def _async_value(value: str) -> str:
    return value
