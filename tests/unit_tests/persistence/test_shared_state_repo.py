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


@pytest.mark.asyncio
async def test_manage_states_and_get_states_preserve_requested_order(
    tmp_path: Path,
) -> None:
    repository = SharedStateRepository(tmp_path / "state.db")
    scope = ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1")

    await repository.manage_states_async(
        (
            StateMutation(scope=scope, key="b", value_json='"two"'),
            StateMutation(scope=scope, key="a", value_json='"one"'),
            StateMutation(scope=scope, key="c", value_json='"three"'),
        )
    )

    values = await repository.get_states_async(scope, (" c ", "a", "", "c", "missing"))

    assert values == (("c", '"three"'), ("a", '"one"'))


@pytest.mark.asyncio
async def test_manage_states_respects_ttl(tmp_path: Path) -> None:
    repository = SharedStateRepository(tmp_path / "state.db")
    scope = ScopeRef(scope_type=ScopeType.TASK, scope_id="task-ttl")

    await repository.manage_states_async(
        (StateMutation(scope=scope, key="short", value_json='"gone"'),),
        ttl_seconds=-60,
    )
    await repository.manage_states_async(
        (StateMutation(scope=scope, key="long", value_json='"kept"'),),
        ttl_seconds=60,
    )
    assert await repository.get_states_async(scope, ("short", "long")) == (
        ("long", '"kept"'),
    )


@pytest.mark.asyncio
async def test_manage_states_empty_input_is_noop(tmp_path: Path) -> None:
    repository = SharedStateRepository(tmp_path / "state.db")
    scope = ScopeRef(scope_type=ScopeType.TASK, scope_id="task-empty")

    await repository.manage_states_async(())

    assert await repository.get_states_async(scope, ()) == ()
