# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.roles.memory_repository import RoleMemoryRepository
from agent_teams.roles.memory_service import RoleMemoryService


def test_role_memory_service_builds_injected_memory_and_preview(
    tmp_path: Path,
) -> None:
    service = RoleMemoryService(
        repository=RoleMemoryRepository(tmp_path / "role_memory.db")
    )

    service.update_reflection_memory(
        role_id="writer",
        workspace_id="workspace-a",
        content_markdown="- Prefer concise output\n- Check tool results before responding",
    )

    injected = service.build_injected_memory(
        role_id="writer",
        workspace_id="workspace-a",
        memory_date="2026-03-14",
    )
    preview = service.build_reflection_preview(
        role_id="writer",
        workspace_id="workspace-a",
        max_chars=40,
    )

    assert "Prefer concise output" in injected
    assert "Check tool results before responding" in injected
    assert preview.startswith("- Prefer concise output")


def test_role_memory_service_isolates_memory_by_workspace(tmp_path: Path) -> None:
    service = RoleMemoryService(
        repository=RoleMemoryRepository(tmp_path / "role_memory_scoped.db")
    )

    service.update_reflection_memory(
        role_id="writer",
        workspace_id="workspace-a",
        content_markdown="- Memory A",
    )
    service.update_reflection_memory(
        role_id="writer",
        workspace_id="workspace-b",
        content_markdown="- Memory B",
    )

    injected_a = service.build_injected_memory(
        role_id="writer",
        workspace_id="workspace-a",
        memory_date="2026-03-14",
    )
    injected_b = service.build_injected_memory(
        role_id="writer",
        workspace_id="workspace-b",
        memory_date="2026-03-14",
    )

    assert "Memory A" in injected_a
    assert "Memory B" not in injected_a
    assert "Memory B" in injected_b
    assert "Memory A" not in injected_b


def test_role_memory_service_delete_clears_summary_and_timestamp(
    tmp_path: Path,
) -> None:
    service = RoleMemoryService(
        repository=RoleMemoryRepository(tmp_path / "role_memory_delete.db")
    )

    service.update_reflection_memory(
        role_id="writer",
        workspace_id="workspace-a",
        content_markdown="- Temporary note",
    )

    service.delete_reflection_memory(
        role_id="writer",
        workspace_id="workspace-a",
    )

    record = service.get_reflection_record(
        role_id="writer",
        workspace_id="workspace-a",
    )

    assert record.content_markdown == ""
    assert record.updated_at is None
