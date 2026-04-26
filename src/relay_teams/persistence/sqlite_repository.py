# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from threading import RLock
from typing import Awaitable, Callable, Optional, ParamSpec, Self, TypeVar

import aiosqlite

from relay_teams.persistence.db import (
    open_async_sqlite,
    open_sqlite,
    run_async_sqlite_write_with_retry,
    run_sqlite_write_with_retry,
)

ResultT = TypeVar("ResultT")
ParamT = ParamSpec("ParamT")
SqliteParameters = Sequence[object]


async def async_fetchone(
    conn: aiosqlite.Connection,
    sql: str,
    parameters: SqliteParameters = (),
) -> sqlite3.Row | None:
    cursor = await conn.execute(sql, tuple(parameters))
    try:
        return await cursor.fetchone()
    finally:
        await cursor.close()


async def async_fetchall(
    conn: aiosqlite.Connection,
    sql: str,
    parameters: SqliteParameters = (),
) -> list[sqlite3.Row]:
    cursor = await conn.execute(sql, tuple(parameters))
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    return list(rows)


class SharedSqliteRepository:
    def __init__(
        self,
        db_path: Path,
        *,
        repository_name: Optional[str] = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._conn = open_sqlite(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._async_conn: aiosqlite.Connection | None = None
        self._async_conn_lock = asyncio.Lock()
        self._async_lock = asyncio.Lock()
        self._repository_name = repository_name or type(self).__name__

    # noinspection PyTypeHints
    def _run_read(self, operation: Callable[[], ResultT]) -> ResultT:
        with self._lock:
            return operation()

    # noinspection PyTypeHints
    def _run_write(
        self,
        *,
        operation_name: str,
        operation: Callable[[], ResultT],
    ) -> ResultT:
        return run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name=self._repository_name,
            operation_name=operation_name,
        )

    async def close_async(self) -> None:
        async with self._async_conn_lock:
            if self._async_conn is None:
                return
            await self._async_conn.close()
            self._async_conn = None

    async def _get_async_conn(self) -> aiosqlite.Connection:
        async with self._async_conn_lock:
            if self._async_conn is None:
                self._async_conn = await open_async_sqlite(self._db_path)
                self._async_conn.row_factory = sqlite3.Row
            return self._async_conn

    # noinspection PyTypeHints
    async def _run_async_read(
        self,
        operation: Callable[[aiosqlite.Connection], Awaitable[ResultT]],
    ) -> ResultT:
        conn = await self._get_async_conn()
        async with self._async_lock:
            return await operation(conn)

    # noinspection PyTypeHints
    async def _run_async_write(
        self,
        *,
        operation_name: str,
        operation: Callable[[aiosqlite.Connection], Awaitable[ResultT]],
    ) -> ResultT:
        conn = await self._get_async_conn()
        return await run_async_sqlite_write_with_retry(
            conn=conn,
            db_path=self._db_path,
            operation=lambda: operation(conn),
            lock=self._async_lock,
            repository_name=self._repository_name,
            operation_name=operation_name,
        )

    # noinspection PyMethodMayBeStatic
    async def _call_sync_async(
        self,
        function: Callable[ParamT, ResultT],
        /,
        *args: ParamT.args,
        **kwargs: ParamT.kwargs,
    ) -> ResultT:
        return await asyncio.to_thread(function, *args, **kwargs)


class AsyncSharedSqliteRepository:
    def __init__(
        self,
        db_path: Path,
        conn: aiosqlite.Connection,
        *,
        repository_name: Optional[str] = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        self._repository_name = repository_name or type(self).__name__

    @classmethod
    async def open(
        cls,
        db_path: Path,
        *,
        repository_name: Optional[str] = None,
    ) -> Self:
        repository = cls(
            db_path,
            await open_async_sqlite(db_path),
            repository_name=repository_name,
        )
        await repository._initialize()
        return repository

    async def close(self) -> None:
        await self._conn.close()

    async def _initialize(self) -> None:
        self._conn.row_factory = sqlite3.Row

    # noinspection PyTypeHints
    async def _run_read(
        self,
        operation: Callable[[], Awaitable[ResultT]],
    ) -> ResultT:
        async with self._lock:
            return await operation()
        raise RuntimeError("Async SQLite read helper exited without a result")

    # noinspection PyTypeHints
    async def _run_write(
        self,
        *,
        operation_name: str,
        operation: Callable[[], Awaitable[ResultT]],
    ) -> ResultT:
        return await run_async_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name=self._repository_name,
            operation_name=operation_name,
        )
