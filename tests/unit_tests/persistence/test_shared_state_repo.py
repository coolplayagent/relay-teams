from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.persistence.shared_state_repo import SharedStateRepository


def test_snapshot_many_can_exclude_internal_key_prefixes(tmp_path: Path) -> None:
    repository = SharedStateRepository(tmp_path / "state.db")
    scope = ScopeRef(scope_type=ScopeType.SESSION, scope_id="session-1")
    repository.manage_state(
        StateMutation(
            scope=scope,
            key="workspace_read:/repo/file.py",
            value_json='{"path": "/repo/file.py"}',
        )
    )
    repository.manage_state(
        StateMutation(
            scope=scope,
            key="ticket",
            value_json='"BUG-123"',
        )
    )

    assert repository.snapshot_many(
        (scope,),
        exclude_key_prefixes=("workspace_read:",),
    ) == (("ticket", '"BUG-123"'),)


def test_manage_states_performs_real_sync_bulk_write(tmp_path: Path) -> None:
    repository = SharedStateRepository(tmp_path / "state_bulk.db")
    scope = ScopeRef(scope_type=ScopeType.SESSION, scope_id="session-1")

    repository.manage_states(
        (
            StateMutation(scope=scope, key="first", value_json='"one"'),
            StateMutation(scope=scope, key="second", value_json='"two"'),
        )
    )
    repository.manage_states(
        (StateMutation(scope=scope, key="second", value_json='"updated"'),)
    )
    repository.manage_states(())

    assert set(repository.snapshot(scope)) == {
        ("first", '"one"'),
        ("second", '"updated"'),
    }


@pytest.mark.asyncio
async def test_get_states_async_reads_requested_keys_in_order(tmp_path: Path) -> None:
    repository = SharedStateRepository(tmp_path / "state_bulk_read.db")
    scope = ScopeRef(scope_type=ScopeType.SESSION, scope_id="session-1")
    other_scope = ScopeRef(scope_type=ScopeType.SESSION, scope_id="session-2")
    await repository.manage_states_async(
        (
            StateMutation(scope=scope, key="first", value_json='"one"'),
            StateMutation(scope=scope, key="second", value_json='"two"'),
            StateMutation(scope=other_scope, key="first", value_json='"other"'),
        )
    )

    assert await repository.get_states_async(
        scope,
        (" second ", "missing", "first", "second", ""),
    ) == (("second", '"two"'), ("first", '"one"'))


def test_delete_by_scope_key_prefix_removes_only_matching_scope_and_prefix(
    tmp_path: Path,
) -> None:
    repository = SharedStateRepository(tmp_path / "state.db")
    session_scope = ScopeRef(scope_type=ScopeType.SESSION, scope_id="session-1")
    other_session_scope = ScopeRef(scope_type=ScopeType.SESSION, scope_id="session-2")
    for scope, key in (
        (session_scope, "workspace_read:conversation-1:/repo/file.py"),
        (session_scope, "workspace_read:conversation-2:/repo/file.py"),
        (session_scope, "ticket"),
        (other_session_scope, "workspace_read:conversation-1:/repo/file.py"),
    ):
        repository.manage_state(
            StateMutation(
                scope=scope,
                key=key,
                value_json='"value"',
            )
        )

    repository.delete_by_scope_key_prefix(
        session_scope,
        "workspace_read:conversation-1:",
    )

    assert set(repository.snapshot(session_scope)) == {
        ("workspace_read:conversation-2:/repo/file.py", '"value"'),
        ("ticket", '"value"'),
    }
    assert repository.snapshot(other_session_scope) == (
        ("workspace_read:conversation-1:/repo/file.py", '"value"'),
    )
