from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Sequence
import logging
import sqlite3
import time
from pathlib import Path
from threading import Condition, Event, RLock, Thread, get_ident
from typing import Awaitable, Callable, Optional, Protocol, TypeVar
from weakref import WeakKeyDictionary

import aiosqlite

from relay_teams.logger import get_logger, log_event

MEMORY_DSN = "file:agent_teams_shared?mode=memory&cache=shared"
SQLITE_TIMEOUT_SECONDS = 30.0
SQLITE_BUSY_TIMEOUT_MS = 30_000
SQLITE_WRITE_RETRY_ATTEMPTS = 8
SQLITE_WRITE_RETRY_INITIAL_DELAY_SECONDS = 0.01
SQLITE_WRITE_RETRY_MAX_DELAY_SECONDS = 0.2

LOGGER = get_logger(__name__)
type _WriteOwnerToken = tuple[str, int]
type SqliteParameters = Sequence[object]
_CROSS_WRITE_COORDINATORS: dict[str, CrossModeWriteCoordinator] = {}
_WRITE_COORDINATORS: dict[str, RLock] = {}
_WRITE_COORDINATORS_LOCK = RLock()
_ASYNC_WRITE_COORDINATORS: dict[
    str, WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]
] = {}
_RESOLVED_DB_PATH_KEYS: dict[str, str] = {}
_ASYNC_BLOCKING_RUNNER: _AsyncBlockingRunner | None = None
ResultT = TypeVar("ResultT")


class BlockingSqliteCursor(Protocol):
    @property
    def rowcount(self) -> int:
        raise NotImplementedError

    def fetchall(self) -> list[sqlite3.Row]:
        raise NotImplementedError


class BlockingSqliteConnection(Protocol):
    def execute(
        self,
        sql: str,
        parameters: SqliteParameters = (),
    ) -> BlockingSqliteCursor:
        raise NotImplementedError

    def commit(self) -> None:
        raise NotImplementedError

    def rollback(self) -> None:
        raise NotImplementedError


class _AsyncBlockingRunner:
    def __init__(self) -> None:
        self._ready = Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = Thread(
            target=self._run_loop,
            name="relay-teams-sqlite-async-bridge",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()

    def run(self, coro: Coroutine[object, object, ResultT]) -> ResultT:
        loop = self._loop
        if loop is None:
            raise RuntimeError("SQLite async bridge loop did not start")
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is loop:
            raise RuntimeError("Cannot block on SQLite async bridge from its own loop")
        return asyncio.run_coroutine_threadsafe(coro, loop).result()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()


class CrossModeWriteCoordinator:
    def __init__(self) -> None:
        self._condition = Condition()
        self._owner: _WriteOwnerToken | None = None
        self._depth = 0

    def acquire_sync(self) -> _WriteOwnerToken:
        token = ("thread", get_ident())
        with self._condition:
            while self._owner is not None and self._owner != token:
                self._condition.wait()
            self._owner = token
            self._depth += 1
        return token

    async def acquire_async(self) -> _WriteOwnerToken:
        task = asyncio.current_task()
        token = (
            "async",
            id(task) if task is not None else id(asyncio.get_running_loop()),
        )
        initial_depth = self._owned_depth(token)
        try:
            while not self._try_acquire(token):
                await asyncio.sleep(0.001)
        except asyncio.CancelledError:
            self._release_cancelled_acquire(token, initial_depth)
            raise
        return token

    def release(self, token: _WriteOwnerToken) -> None:
        with self._condition:
            if self._owner != token:
                raise RuntimeError("SQLite write coordinator released by non-owner")
            self._depth -= 1
            if self._depth > 0:
                return
            self._owner = None
            self._condition.notify_all()

    def _try_acquire(self, token: _WriteOwnerToken) -> bool:
        with self._condition:
            if self._owner is not None and self._owner != token:
                return False
            self._owner = token
            self._depth += 1
            return True

    def _owned_depth(self, token: _WriteOwnerToken) -> int:
        with self._condition:
            if self._owner != token:
                return 0
            return self._depth

    def _release_cancelled_acquire(
        self, token: _WriteOwnerToken, initial_depth: int
    ) -> None:
        with self._condition:
            if self._owner != token or self._depth <= initial_depth:
                return
        self.release(token)


async def _configure_async_connection(
    conn: aiosqlite.Connection,
    *,
    enable_wal: bool,
) -> aiosqlite.Connection:
    await conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA temp_store = MEMORY")
    await conn.execute("PRAGMA synchronous = NORMAL")
    try:
        if enable_wal:
            await conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        # WAL is best-effort. In-memory fallback and some filesystems do not support it.
        pass
    return conn


async def open_async_sqlite(db_path: Path) -> aiosqlite.Connection:
    file_path = Path(db_path)
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return await _configure_async_connection(
            await aiosqlite.connect(
                str(file_path),
                timeout=SQLITE_TIMEOUT_SECONDS,
            ),
            enable_wal=True,
        )
    except sqlite3.OperationalError:
        pass

    return await _configure_async_connection(
        await aiosqlite.connect(
            MEMORY_DSN,
            uri=True,
            timeout=SQLITE_TIMEOUT_SECONDS,
        ),
        enable_wal=False,
    )


def run_async_blocking(coro: Coroutine[object, object, ResultT]) -> ResultT:
    return _async_blocking_runner().run(coro)


def _async_blocking_runner() -> _AsyncBlockingRunner:
    global _ASYNC_BLOCKING_RUNNER
    with _WRITE_COORDINATORS_LOCK:
        if _ASYNC_BLOCKING_RUNNER is None:
            _ASYNC_BLOCKING_RUNNER = _AsyncBlockingRunner()
        return _ASYNC_BLOCKING_RUNNER


def sqlite_compile_options(conn: BlockingSqliteConnection) -> frozenset[str]:
    rows = conn.execute("PRAGMA compile_options").fetchall()
    return frozenset(str(row[0]) for row in rows)


def sqlite_supports_fts5(conn: BlockingSqliteConnection) -> bool:
    return "ENABLE_FTS5" in sqlite_compile_options(conn)


async def async_sqlite_compile_options(
    conn: aiosqlite.Connection,
) -> frozenset[str]:
    cursor = await conn.execute("PRAGMA compile_options")
    rows = await cursor.fetchall()
    await cursor.close()
    return frozenset(str(row[0]) for row in rows)


async def async_sqlite_supports_fts5(conn: aiosqlite.Connection) -> bool:
    return "ENABLE_FTS5" in await async_sqlite_compile_options(conn)


def is_retryable_sqlite_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return (
        "database is locked" in message
        or "database table is locked" in message
        or "another row available" in message
    )


# noinspection PyTypeHints
async def run_async_sqlite_write_with_retry(
    *,
    conn: aiosqlite.Connection,
    db_path: Path,
    operation: Callable[[], Awaitable[ResultT]],
    lock: Optional[asyncio.Lock] = None,
    repository_name: str,
    operation_name: str,
    max_retries: int = SQLITE_WRITE_RETRY_ATTEMPTS,
) -> ResultT:
    delay = SQLITE_WRITE_RETRY_INITIAL_DELAY_SECONDS
    cross_write_lock = _cross_write_coordinator_for(db_path)
    write_lock = _async_write_coordinator_for(db_path)
    for attempt in range(max_retries + 1):
        operation_started = False
        try:
            cross_write_token = await cross_write_lock.acquire_async()
            try:
                async with write_lock:
                    if lock is not None:
                        async with lock:
                            operation_started = True
                            result = await operation()
                            await conn.commit()
                            return result
                    operation_started = True
                    result = await operation()
                    await conn.commit()
                    return result
            finally:
                cross_write_lock.release(cross_write_token)
        except sqlite3.OperationalError as exc:
            await _async_rollback_quietly(conn)
            if not is_retryable_sqlite_error(exc) or attempt >= max_retries:
                raise
            log_event(
                LOGGER,
                logging.WARNING,
                event="sqlite.write.retry",
                message="Retrying SQLite write after lock contention",
                payload={
                    "repository": repository_name,
                    "operation": operation_name,
                    "attempt": attempt + 1,
                    "max_retries": max_retries,
                    "delay_seconds": round(delay, 3),
                },
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, SQLITE_WRITE_RETRY_MAX_DELAY_SECONDS)
        except BaseException:
            if operation_started:
                await _async_rollback_quietly(conn)
            raise
    raise RuntimeError(
        f"SQLite write helper exhausted retries for {repository_name}.{operation_name}"
    )


# noinspection PyTypeHints
def run_sqlite_write_with_retry(
    *,
    conn: BlockingSqliteConnection,
    db_path: Path,
    operation: Callable[[], ResultT],
    lock: Optional[RLock] = None,
    repository_name: str,
    operation_name: str,
    max_retries: int = SQLITE_WRITE_RETRY_ATTEMPTS,
) -> ResultT:
    delay = SQLITE_WRITE_RETRY_INITIAL_DELAY_SECONDS
    cross_write_lock = _cross_write_coordinator_for(db_path)
    write_lock = _write_coordinator_for(db_path)
    for attempt in range(max_retries + 1):
        operation_started = False
        try:
            cross_write_token = cross_write_lock.acquire_sync()
            try:
                with write_lock:
                    if lock is not None:
                        with lock:
                            operation_started = True
                            result = operation()
                            conn.commit()
                            return result
                    operation_started = True
                    result = operation()
                    conn.commit()
                    return result
            finally:
                cross_write_lock.release(cross_write_token)
        except sqlite3.OperationalError as exc:
            _rollback_quietly(conn)
            if not is_retryable_sqlite_error(exc) or attempt >= max_retries:
                raise
            log_event(
                LOGGER,
                logging.WARNING,
                event="sqlite.write.retry",
                message="Retrying SQLite write after lock contention",
                payload={
                    "repository": repository_name,
                    "operation": operation_name,
                    "attempt": attempt + 1,
                    "max_retries": max_retries,
                    "delay_seconds": round(delay, 3),
                },
            )
            time.sleep(delay)
            delay = min(delay * 2, SQLITE_WRITE_RETRY_MAX_DELAY_SECONDS)
        except BaseException:
            if operation_started:
                _rollback_quietly(conn)
            raise
    raise RuntimeError(
        f"SQLite write helper exhausted retries for {repository_name}.{operation_name}"
    )


def _resolved_db_path_key(db_path: Path) -> str:
    candidate = Path(db_path)
    raw_key = str(candidate)
    if not candidate.is_absolute():
        return str(candidate.resolve(strict=False))
    with _WRITE_COORDINATORS_LOCK:
        cached = _RESOLVED_DB_PATH_KEYS.get(raw_key)
        if cached is not None:
            return cached
    resolved_key = str(candidate.resolve(strict=False))
    with _WRITE_COORDINATORS_LOCK:
        existing = _RESOLVED_DB_PATH_KEYS.get(raw_key)
        if existing is not None:
            return existing
        _RESOLVED_DB_PATH_KEYS[raw_key] = resolved_key
        return resolved_key


def _write_coordinator_for(db_path: Path) -> RLock:
    key = _resolved_db_path_key(db_path)
    with _WRITE_COORDINATORS_LOCK:
        coordinator = _WRITE_COORDINATORS.get(key)
        if coordinator is None:
            coordinator = RLock()
            _WRITE_COORDINATORS[key] = coordinator
    return coordinator


def _cross_write_coordinator_for(db_path: Path) -> CrossModeWriteCoordinator:
    key = _resolved_db_path_key(db_path)
    with _WRITE_COORDINATORS_LOCK:
        coordinator = _CROSS_WRITE_COORDINATORS.get(key)
        if coordinator is None:
            coordinator = CrossModeWriteCoordinator()
            _CROSS_WRITE_COORDINATORS[key] = coordinator
    return coordinator


def _async_write_coordinator_for(db_path: Path) -> asyncio.Lock:
    key = _resolved_db_path_key(db_path)
    loop = asyncio.get_running_loop()
    with _WRITE_COORDINATORS_LOCK:
        locks_by_loop = _ASYNC_WRITE_COORDINATORS.get(key)
        if locks_by_loop is None:
            locks_by_loop = WeakKeyDictionary()
            _ASYNC_WRITE_COORDINATORS[key] = locks_by_loop
        coordinator = locks_by_loop.get(loop)
        if coordinator is None:
            coordinator = asyncio.Lock()
            locks_by_loop[loop] = coordinator
    return coordinator


def _rollback_quietly(conn: BlockingSqliteConnection) -> None:
    try:
        conn.rollback()
    except sqlite3.Error:
        return


async def _async_rollback_quietly(conn: aiosqlite.Connection) -> None:
    try:
        await conn.rollback()
    except sqlite3.Error:
        return
