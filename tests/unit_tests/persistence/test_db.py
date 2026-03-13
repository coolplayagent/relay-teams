from __future__ import annotations

from pathlib import Path

from agent_teams.persistence.db import SQLITE_BUSY_TIMEOUT_MS, open_sqlite


def test_open_sqlite_enables_busy_timeout_and_wal_for_file_db(tmp_path: Path) -> None:
    conn = open_sqlite(tmp_path / "agent_teams.db")
    try:
        foreign_keys = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
        busy_timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()

        assert foreign_keys == 1
        assert busy_timeout == SQLITE_BUSY_TIMEOUT_MS
        assert journal_mode == "wal"
    finally:
        conn.close()
