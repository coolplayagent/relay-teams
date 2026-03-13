from __future__ import annotations

import sqlite3
from pathlib import Path

MEMORY_DSN = "file:agent_teams_shared?mode=memory&cache=shared"
SQLITE_TIMEOUT_SECONDS = 30.0
SQLITE_BUSY_TIMEOUT_MS = 30_000


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
