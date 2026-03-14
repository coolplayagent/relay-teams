# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.roles.memory_repository import RoleMemoryRepository
from agent_teams.roles.memory_service import RoleMemoryService


def test_role_memory_service_builds_injected_memory_and_daily_entries(
    tmp_path: Path,
) -> None:
    service = RoleMemoryService(
        repository=RoleMemoryRepository(tmp_path / "role_memory.db")
    )

    service.record_task_result(
        role_id="writer",
        workspace_id="workspace-a",
        session_id="session-1",
        task_id="task-1",
        objective="Draft a summary",
        result="Summarized the design.",
        transcript_lines=("line-1", "line-2"),
        memory_date="2026-03-14",
    )

    injected = service.build_injected_memory(
        role_id="writer",
        workspace_id="workspace-a",
        memory_date="2026-03-14",
    )

    assert "## Role Memory" in injected
    assert "## Daily Memory" in injected
    assert "Draft a summary: Summarized the design." in injected
    assert "Summarized the design." in injected


def test_role_memory_service_isolates_memory_by_workspace(tmp_path: Path) -> None:
    service = RoleMemoryService(
        repository=RoleMemoryRepository(tmp_path / "role_memory_scoped.db")
    )

    service.record_task_result(
        role_id="writer",
        workspace_id="workspace-a",
        session_id="session-a",
        task_id="task-a",
        objective="Draft A",
        result="Result A",
        transcript_lines=(),
        memory_date="2026-03-14",
    )
    service.record_task_result(
        role_id="writer",
        workspace_id="workspace-b",
        session_id="session-b",
        task_id="task-b",
        objective="Draft B",
        result="Result B",
        transcript_lines=(),
        memory_date="2026-03-14",
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

    assert "Draft A: Result A" in injected_a
    assert "Result B" not in injected_a
    assert "Draft B: Result B" in injected_b
    assert "Result A" not in injected_b
