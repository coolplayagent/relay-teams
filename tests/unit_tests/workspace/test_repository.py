# -*- coding: utf-8 -*-
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest
from relay_teams.workspace import WorkspaceRepository


def test_workspace_repository_supports_concurrent_reads(tmp_path: Path) -> None:
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    repository = WorkspaceRepository(tmp_path / "workspace.db")
    _ = repository.create(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    def read_workspace() -> tuple[bool, bool, str]:
        record = repository.get("project-alpha")
        listed = repository.list_all()
        exists = repository.exists("project-alpha")
        return record.workspace_id == "project-alpha", exists, listed[0].workspace_id

    futures = []
    with ThreadPoolExecutor(max_workers=16) as executor:
        for _ in range(128):
            futures.append(executor.submit(read_workspace))

        results = [future.result() for future in as_completed(futures)]

    assert len(results) == 128
    assert all(result == (True, True, "project-alpha") for result in results)


def test_workspace_repository_skips_invalid_persisted_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "workspace_invalid_rows.db"
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    repository = WorkspaceRepository(db_path)
    _ = repository.create(
        workspace_id="project-alpha",
        root_path=root_path,
    )
    _insert_workspace_row(
        db_path,
        workspace_id="None",
        root_path=root_path,
    )

    records = repository.list_all()

    assert [record.workspace_id for record in records] == ["project-alpha"]
    with pytest.raises(KeyError):
        repository.get("None")


def test_workspace_repository_skips_invalid_legacy_profile_rows_on_init(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "workspace_legacy_invalid.db"
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    now = datetime.now(tz=timezone.utc).isoformat()
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE workspaces (
            workspace_id TEXT PRIMARY KEY,
            root_path TEXT NOT NULL,
            backend TEXT NOT NULL,
            profile_json TEXT NOT NULL DEFAULT '{}',
            default_mount_name TEXT NOT NULL DEFAULT 'default',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO workspaces(
            workspace_id,
            root_path,
            backend,
            profile_json,
            default_mount_name,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "project-alpha",
            str(root_path),
            "filesystem",
            '{"backend": "filesystem", "file_scope": "broken"}',
            "default",
            now,
            now,
        ),
    )
    connection.commit()
    connection.close()

    repository = WorkspaceRepository(db_path)

    assert repository.list_all() == ()


def _insert_workspace_row(
    db_path: Path,
    *,
    workspace_id: str,
    root_path: Path,
) -> None:
    now = datetime.now(tz=timezone.utc).isoformat()
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        INSERT INTO workspaces(workspace_id, root_path, backend, profile_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            str(root_path),
            "filesystem",
            "{}",
            now,
            now,
        ),
    )
    connection.commit()
    connection.close()
