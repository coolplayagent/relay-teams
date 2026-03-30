# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sqlite3
import threading

from pytest import MonkeyPatch

import agent_teams.persistence.sqlite_repository as sqlite_repository_module
from agent_teams.persistence.sqlite_repository import SharedSqliteRepository
from agent_teams.retrieval.sqlite_store import SqliteFts5RetrievalStore


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
        conn: sqlite3.Connection,
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
