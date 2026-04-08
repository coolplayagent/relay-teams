# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import TypeVar

from relay_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry

_ResultT = TypeVar("_ResultT")


class SharedSqliteRepository:
    def __init__(
        self,
        db_path: Path,
        *,
        repository_name: str | None = None,
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
