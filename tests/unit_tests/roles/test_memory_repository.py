from __future__ import annotations

from pathlib import Path

from agent_teams.roles.memory_models import MemoryKind
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
    repository.write_daily_memory(
        role_id="Crafter",
        workspace_id="workspace-a",
        memory_date="2026-03-15",
        kind=MemoryKind.DIGEST,
        content_markdown="- Digest A",
        source_session_id="session-a",
        source_task_id="task-a",
    )
    repository.write_daily_memory(
        role_id="Crafter",
        workspace_id="workspace-b",
        memory_date="2026-03-15",
        kind=MemoryKind.DIGEST,
        content_markdown="- Digest B",
        source_session_id="session-b",
        source_task_id="task-b",
    )

    durable_a = repository.read_role_memory(
        role_id="Crafter",
        workspace_id="workspace-a",
    )
    durable_b = repository.read_role_memory(
        role_id="Crafter",
        workspace_id="workspace-b",
    )
    daily_a = repository.read_daily_memory(
        role_id="Crafter",
        workspace_id="workspace-a",
        memory_date="2026-03-15",
        kind=MemoryKind.DIGEST,
    )
    daily_b = repository.read_daily_memory(
        role_id="Crafter",
        workspace_id="workspace-b",
        memory_date="2026-03-15",
        kind=MemoryKind.DIGEST,
    )

    assert durable_a.content_markdown == "Memory A"
    assert durable_b.content_markdown == "Memory B"
    assert daily_a.content_markdown == "- Digest A"
    assert daily_b.content_markdown == "- Digest B"
