# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterable, Iterator
from pathlib import Path
from threading import RLock
from typing import Awaitable, Callable, Optional, ParamSpec, TypeVar, cast
from weakref import WeakKeyDictionary

import aiosqlite

from relay_teams.persistence.db import (
    SqliteParameters,
    open_async_sqlite,
    run_async_blocking,
    run_async_sqlite_write_with_retry,
    run_sqlite_write_with_retry,
)

ResultT = TypeVar("ResultT")
ParamT = ParamSpec("ParamT")


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


class BlockingAsyncSqliteCursor:
    def __init__(self, cursor: aiosqlite.Cursor) -> None:
        self._cursor: aiosqlite.Cursor | None = cursor
        self._rowcount = cursor.rowcount
        self._lastrowid = cursor.lastrowid

    @property
    def rowcount(self) -> int:
        return self._rowcount

    @property
    def lastrowid(self) -> int | None:
        return self._lastrowid

    def fetchone(self) -> sqlite3.Row | None:
        return cast(sqlite3.Row | None, run_async_blocking(self._fetchone()))

    def fetchall(self) -> list[sqlite3.Row]:
        rows = run_async_blocking(self._fetchall())
        return [cast(sqlite3.Row, row) for row in rows]

    def close(self) -> None:
        run_async_blocking(self._close())

    def __iter__(self) -> Iterator[sqlite3.Row]:
        return iter(self.fetchall())

    async def _fetchone(self) -> sqlite3.Row | None:
        cursor = self._cursor
        if cursor is None:
            return None
        try:
            return cast(sqlite3.Row | None, await cursor.fetchone())
        finally:
            await self._close()

    async def _fetchall(self) -> list[sqlite3.Row]:
        cursor = self._cursor
        if cursor is None:
            return []
        try:
            rows = await cursor.fetchall()
            return [cast(sqlite3.Row, row) for row in rows]
        finally:
            await self._close()

    async def _close(self) -> None:
        cursor = self._cursor
        if cursor is None:
            return
        self._cursor = None
        await cursor.close()


class BlockingAsyncSqliteConnection:
    def __init__(self, repository: SharedSqliteRepository) -> None:
        self._repository = repository

    def execute(
        self,
        sql: str,
        parameters: SqliteParameters = (),
    ) -> BlockingAsyncSqliteCursor:
        return run_async_blocking(self._execute(sql, parameters))

    def executemany(
        self,
        sql: str,
        parameters: Iterable[SqliteParameters],
    ) -> BlockingAsyncSqliteCursor:
        return run_async_blocking(self._executemany(sql, tuple(parameters)))

    def commit(self) -> None:
        run_async_blocking(self._commit())

    def rollback(self) -> None:
        run_async_blocking(self._rollback())

    async def _execute(
        self,
        sql: str,
        parameters: SqliteParameters,
    ) -> BlockingAsyncSqliteCursor:
        conn = await self._repository._get_async_conn()
        cursor = BlockingAsyncSqliteCursor(await conn.execute(sql, tuple(parameters)))
        if _closes_without_fetch(sql):
            await cursor._close()
        return cursor

    async def _executemany(
        self,
        sql: str,
        parameters: tuple[SqliteParameters, ...],
    ) -> BlockingAsyncSqliteCursor:
        conn = await self._repository._get_async_conn()
        cursor = BlockingAsyncSqliteCursor(await conn.executemany(sql, parameters))
        await cursor._close()
        return cursor

    async def _commit(self) -> None:
        conn = await self._repository._get_async_conn()
        await conn.commit()

    async def _rollback(self) -> None:
        conn = await self._repository._get_async_conn()
        await conn.rollback()


def _closes_without_fetch(sql: str) -> bool:
    tokens = sql.lstrip().split(maxsplit=1)
    if not tokens:
        return True
    first_token = tokens[0].lower()
    return first_token not in {"select", "pragma", "with"}


class SharedSqliteRepository:
    def __init__(
        self,
        db_path: Path,
        *,
        repository_name: Optional[str] = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._conn = BlockingAsyncSqliteConnection(self)
        self._lock = RLock()
        self._async_conn_guard = RLock()
        self._async_conns: WeakKeyDictionary[
            asyncio.AbstractEventLoop, aiosqlite.Connection
        ] = WeakKeyDictionary()
        self._async_locks: WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Lock
        ] = WeakKeyDictionary()
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
        current_loop = asyncio.get_running_loop()
        with self._async_conn_guard:
            connections = tuple(self._async_conns.items())
            self._async_conns = WeakKeyDictionary()
            self._async_locks = WeakKeyDictionary()
        for loop, conn in connections:
            if loop is current_loop:
                await conn.close()
            elif loop.is_running():
                await asyncio.wrap_future(
                    asyncio.run_coroutine_threadsafe(conn.close(), loop)
                )
            else:
                await conn.close()

    async def _get_async_conn(self) -> aiosqlite.Connection:
        loop = asyncio.get_running_loop()
        with self._async_conn_guard:
            conn = self._async_conns.get(loop)
            if conn is not None:
                return conn

        new_conn = await open_async_sqlite(self._db_path)
        new_conn.row_factory = sqlite3.Row
        with self._async_conn_guard:
            existing = self._async_conns.get(loop)
            if existing is None:
                self._async_conns[loop] = new_conn
                return new_conn
        await new_conn.close()
        return existing

    def _async_lock_for_current_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        with self._async_conn_guard:
            lock = self._async_locks.get(loop)
            if lock is None:
                lock = asyncio.Lock()
                self._async_locks[loop] = lock
            return lock

    # noinspection PyTypeHints
    async def _run_async_read(
        self,
        operation: Callable[[aiosqlite.Connection], Awaitable[ResultT]],
    ) -> ResultT:
        conn = await self._get_async_conn()
        async with self._async_lock_for_current_loop():
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
            lock=self._async_lock_for_current_loop(),
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
