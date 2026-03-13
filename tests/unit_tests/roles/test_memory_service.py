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
        session_id="session-1",
        task_id="task-1",
        objective="Draft a summary",
        result="Summarized the design.",
        transcript_lines=("line-1", "line-2"),
        memory_date="2026-03-14",
    )

    injected = service.build_injected_memory(
        role_id="writer",
        memory_date="2026-03-14",
    )

    assert "## Role Memory" in injected
    assert "## Daily Memory" in injected
    assert "Draft a summary: Summarized the design." in injected
    assert "Summarized the design." in injected
