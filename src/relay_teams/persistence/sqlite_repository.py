# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Awaitable, Callable, Optional, Self, TypeVar

import aiosqlite

from relay_teams.persistence.db import (
    open_async_sqlite,
    open_sqlite,
    run_async_sqlite_write_with_retry,
    run_sqlite_write_with_retry,
)

_ResultT = TypeVar("_ResultT")


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
        self._repository_name = repository_name or type(self).__name__

    def _run_read(self, operation: Callable[[], _ResultT]) -> _ResultT:
        with self._lock:
            return operation()

    def _run_write(
        self,
        *,
        operation_name: str,
        operation: Callable[[], _ResultT],
    ) -> _ResultT:
        return run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name=self._repository_name,
            operation_name=operation_name,
        )


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

    async def _run_read(
        self,
        operation: Callable[[], Awaitable[_ResultT]],
    ) -> _ResultT:
        async with self._lock:
            return await operation()
        raise RuntimeError("Async SQLite read helper exited without a result")

    async def _run_write(
        self,
        *,
        operation_name: str,
        operation: Callable[[], Awaitable[_ResultT]],
    ) -> _ResultT:
        return await run_async_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name=self._repository_name,
            operation_name=operation_name,
        )
