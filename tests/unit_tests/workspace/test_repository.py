# -*- coding: utf-8 -*-
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from agent_teams.workspace import WorkspaceRepository


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
