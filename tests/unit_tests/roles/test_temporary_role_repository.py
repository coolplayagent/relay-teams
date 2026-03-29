from __future__ import annotations

from pathlib import Path

from agent_teams.computer import ExecutionSurface
from agent_teams.roles.temporary_role_models import (
    TemporaryRoleRecord,
    TemporaryRoleSource,
    TemporaryRoleSpec,
)
from agent_teams.roles.temporary_role_repository import TemporaryRoleRepository


def test_temporary_role_repository_roundtrip_and_cleanup(tmp_path: Path) -> None:
    repo = TemporaryRoleRepository(tmp_path / "roles.db")
    spec = TemporaryRoleSpec(
        role_id="tmp_researcher",
        name="Temporary Researcher",
        description="Run scoped role",
        system_prompt="You are temporary.",
        tools=("read",),
        execution_surface=ExecutionSurface.DESKTOP,
    )
    stored = repo.upsert(
        TemporaryRoleRecord(
            run_id="run-1",
            session_id="session-1",
            source=TemporaryRoleSource.META_AGENT_GENERATED,
            role=spec,
        )
    )

    assert stored.role.role_id == "tmp_researcher"
    assert stored.role.execution_surface == ExecutionSurface.DESKTOP
    assert stored.source == TemporaryRoleSource.META_AGENT_GENERATED
    assert repo.get(run_id="run-1", role_id="tmp_researcher").role.name == (
        "Temporary Researcher"
    )
    assert (
        repo.get(run_id="run-1", role_id="tmp_researcher").role.execution_surface
        == ExecutionSurface.DESKTOP
    )

    repo.delete_by_run("run-1")
    assert repo.list_by_run("run-1") == ()
