from __future__ import annotations

from pathlib import Path

from agent_teams.roles.memory_repository import RoleMemoryRepository


def test_role_memory_repository_scopes_records_by_workspace(tmp_path: Path) -> None:
    repository = RoleMemoryRepository(tmp_path / "role_memory_repository.db")

    repository.write_role_memory(
        role_id="Crafter",
        workspace_id="workspace-a",
        content_markdown="Memory A",
    )
    repository.write_role_memory(
        role_id="Crafter",
        workspace_id="workspace-b",
        content_markdown="Memory B",
    )

    durable_a = repository.read_role_memory(
        role_id="Crafter",
        workspace_id="workspace-a",
    )
    durable_b = repository.read_role_memory(
        role_id="Crafter",
        workspace_id="workspace-b",
    )

    assert durable_a.content_markdown == "Memory A"
    assert durable_b.content_markdown == "Memory B"
