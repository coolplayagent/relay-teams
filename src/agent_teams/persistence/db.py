from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import TypeVar

from agent_teams.logger import get_logger, log_event

MEMORY_DSN = "file:agent_teams_shared?mode=memory&cache=shared"
SQLITE_TIMEOUT_SECONDS = 30.0
SQLITE_BUSY_TIMEOUT_MS = 30_000
SQLITE_WRITE_RETRY_ATTEMPTS = 8
SQLITE_WRITE_RETRY_INITIAL_DELAY_SECONDS = 0.01
SQLITE_WRITE_RETRY_MAX_DELAY_SECONDS = 0.2

LOGGER = get_logger(__name__)
_WRITE_COORDINATORS: dict[str, RLock] = {}
_WRITE_COORDINATORS_LOCK = RLock()
_ResultT = TypeVar("_ResultT")


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


def sqlite_compile_options(conn: sqlite3.Connection) -> frozenset[str]:
    rows = conn.execute("PRAGMA compile_options").fetchall()
    return frozenset(str(row[0]) for row in rows)


def sqlite_supports_fts5(conn: sqlite3.Connection) -> bool:
    return "ENABLE_FTS5" in sqlite_compile_options(conn)


def is_retryable_sqlite_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return (
        "database is locked" in message
        or "database table is locked" in message
        or "another row available" in message
    )


def run_sqlite_write_with_retry(
    *,
    conn: sqlite3.Connection,
    db_path: Path,
    operation: Callable[[], _ResultT],
    lock: RLock | None = None,
    repository_name: str,
    operation_name: str,
    max_retries: int = SQLITE_WRITE_RETRY_ATTEMPTS,
) -> _ResultT:
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


def _write_coordinator_for(db_path: Path) -> RLock:
    key = str(Path(db_path).resolve(strict=False))
    with _WRITE_COORDINATORS_LOCK:
        coordinator = _WRITE_COORDINATORS.get(key)
        if coordinator is None:
            coordinator = RLock()
            _WRITE_COORDINATORS[key] = coordinator
        return coordinator


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    try:
        conn.rollback()
    except sqlite3.Error:
        return
