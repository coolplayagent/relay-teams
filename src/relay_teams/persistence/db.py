from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from threading import RLock
from typing import Awaitable, Callable, Optional, TypeVar

import aiosqlite

from relay_teams.logger import get_logger, log_event

MEMORY_DSN = "file:agent_teams_shared?mode=memory&cache=shared"
SQLITE_TIMEOUT_SECONDS = 30.0
SQLITE_BUSY_TIMEOUT_MS = 30_000
SQLITE_WRITE_RETRY_ATTEMPTS = 8
SQLITE_WRITE_RETRY_INITIAL_DELAY_SECONDS = 0.01
SQLITE_WRITE_RETRY_MAX_DELAY_SECONDS = 0.2

LOGGER = get_logger(__name__)
_WRITE_COORDINATORS: dict[str, RLock] = {}
_WRITE_COORDINATORS_LOCK = RLock()
_ASYNC_WRITE_COORDINATORS: dict[str, asyncio.Lock] = {}
_RESOLVED_DB_PATH_KEYS: dict[str, str] = {}
ResultT = TypeVar("ResultT")


def _configure_connection(
    conn: sqlite3.Connection,
    *,
    enable_wal: bool,
) -> sqlite3.Connection:
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        if enable_wal:
            conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        # WAL is best-effort. In-memory fallback and some filesystems do not support it.
        pass
    return conn


def open_sqlite(db_path: Path) -> sqlite3.Connection:
    file_path = Path(db_path)
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return _configure_connection(
            sqlite3.connect(
                str(file_path),
                timeout=SQLITE_TIMEOUT_SECONDS,
                check_same_thread=False,
            ),
            enable_wal=True,
        )
    except sqlite3.OperationalError:
        pass

    return _configure_connection(
        sqlite3.connect(
            MEMORY_DSN,
            uri=True,
            timeout=SQLITE_TIMEOUT_SECONDS,
            check_same_thread=False,
        ),
        enable_wal=False,
    )


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


def sqlite_compile_options(conn: sqlite3.Connection) -> frozenset[str]:
    rows = conn.execute("PRAGMA compile_options").fetchall()
    return frozenset(str(row[0]) for row in rows)


def sqlite_supports_fts5(conn: sqlite3.Connection) -> bool:
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
    write_lock = _async_write_coordinator_for(db_path)
    for attempt in range(max_retries + 1):
        try:
            async with write_lock:
                if lock is not None:
                    async with lock:
                        result = await operation()
                        await conn.commit()
                        return result
                result = await operation()
                await conn.commit()
                return result
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
    raise RuntimeError(
        f"SQLite write helper exhausted retries for {repository_name}.{operation_name}"
    )


# noinspection PyTypeHints
def run_sqlite_write_with_retry(
    *,
    conn: sqlite3.Connection,
    db_path: Path,
    operation: Callable[[], ResultT],
    lock: Optional[RLock] = None,
    repository_name: str,
    operation_name: str,
    max_retries: int = SQLITE_WRITE_RETRY_ATTEMPTS,
) -> ResultT:
    delay = SQLITE_WRITE_RETRY_INITIAL_DELAY_SECONDS
    write_lock = _write_coordinator_for(db_path)
    for attempt in range(max_retries + 1):
        try:
            with write_lock:
                if lock is not None:
                    with lock:
                        result = operation()
                        conn.commit()
                        return result
                result = operation()
                conn.commit()
                return result
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


def _async_write_coordinator_for(db_path: Path) -> asyncio.Lock:
    key = _resolved_db_path_key(db_path)
    coordinator = _ASYNC_WRITE_COORDINATORS.get(key)
    if coordinator is None:
        coordinator = asyncio.Lock()
        _ASYNC_WRITE_COORDINATORS[key] = coordinator
    return coordinator


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    try:
        conn.rollback()
    except sqlite3.Error:
        return


async def _async_rollback_quietly(conn: aiosqlite.Connection) -> None:
    try:
        await conn.rollback()
    except sqlite3.Error:
        return
