from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from relay_teams.tools.runtime.approval_ticket_repo import (
    ApprovalTicketRepository,
    ApprovalTicketStatus,
    ApprovalTicketStatusConflictError,
    approval_signature_key,
)
from relay_teams.tools.workspace_tools.shell import build_shell_cache_key


def test_approval_ticket_repo_skips_invalid_persisted_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "approval_ticket_invalid_rows.db"
    repository = ApprovalTicketRepository(db_path)
    _ = repository.upsert_requested(
        tool_call_id="call-valid",
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="orch_dispatch_task",
        args_preview="{}",
    )
    _insert_approval_ticket_row(
        db_path,
        tool_call_id="None",
        run_id="run-1",
        session_id="session-1",
    )

    records = repository.list_open_by_session("session-1")

    assert [record.tool_call_id for record in records] == ["call-valid"]
    assert repository.get("None") is None


def test_approval_ticket_repo_get_recovers_invalid_timestamps(tmp_path: Path) -> None:
    db_path = tmp_path / "approval_ticket_dirty_timestamps.db"
    repository = ApprovalTicketRepository(db_path)
    valid_updated_at = datetime(2025, 1, 3, tzinfo=timezone.utc).isoformat()
    _insert_approval_ticket_row(
        db_path,
        tool_call_id="call-dirty",
        run_id="run-1",
        session_id="session-1",
        created_at="None",
        updated_at=valid_updated_at,
    )

    record = repository.get("call-dirty")

    assert record is not None
    assert record.tool_call_id == "call-dirty"
    assert record.created_at.isoformat() == valid_updated_at
    assert record.updated_at.isoformat() == valid_updated_at
    assert repository.list_open_by_session("session-1") == ()


def test_find_reusable_skips_newer_invalid_matching_ticket(tmp_path: Path) -> None:
    db_path = tmp_path / "approval_ticket_reusable_dirty_latest.db"
    repository = ApprovalTicketRepository(db_path)
    signature_key = approval_signature_key(
        run_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="orch_dispatch_task",
        args_preview="{}",
    )
    _insert_approval_ticket_row(
        db_path,
        tool_call_id="call-valid",
        run_id="run-1",
        session_id="session-1",
        signature_key=signature_key,
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-02T00:00:00+00:00",
    )
    _insert_approval_ticket_row(
        db_path,
        tool_call_id="None",
        run_id="run-1",
        session_id="session-1",
        signature_key=signature_key,
        created_at="2025-01-03T00:00:00+00:00",
        updated_at="2025-01-04T00:00:00+00:00",
    )

    record = repository.find_reusable(
        run_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="orch_dispatch_task",
        args_preview="{}",
    )

    assert record is not None
    assert record.tool_call_id == "call-valid"


@pytest.mark.asyncio
async def test_async_approval_ticket_repo_methods_share_persisted_state(
    tmp_path: Path,
) -> None:
    repository = ApprovalTicketRepository(tmp_path / "approval_ticket_async.db")

    try:
        requested = await repository.upsert_requested_async(
            tool_call_id="call-async",
            run_id="run-1",
            session_id="session-1",
            task_id="task-1",
            instance_id="inst-1",
            role_id="writer",
            tool_name="orch_dispatch_task",
            args_preview="{}",
        )
        open_by_run = await repository.list_open_by_run_async("run-1")
        reusable = await repository.find_reusable_async(
            run_id="run-1",
            task_id="task-1",
            instance_id="inst-1",
            role_id="writer",
            tool_name="orch_dispatch_task",
            args_preview="{}",
        )
        resolved = await repository.resolve_async(
            tool_call_id="call-async",
            status=ApprovalTicketStatus.APPROVED,
            feedback="ok",
            expected_status=ApprovalTicketStatus.REQUESTED,
        )
        completed = await repository.mark_completed_async("call-async")
    finally:
        await repository.close_async()

    assert requested.status == ApprovalTicketStatus.REQUESTED
    assert tuple(record.tool_call_id for record in open_by_run) == ("call-async",)
    assert reusable is not None
    assert reusable.tool_call_id == "call-async"
    assert resolved.status == ApprovalTicketStatus.APPROVED
    assert completed is not None
    assert completed.status == ApprovalTicketStatus.COMPLETED


@pytest.mark.asyncio
async def test_async_approval_ticket_hot_paths_do_not_reinitialize_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ApprovalTicketRepository(tmp_path / "approval_ticket_no_reinit.db")

    async def _fail_init() -> None:
        raise AssertionError("async schema init must not run on hot paths")

    monkeypatch.setattr(repository, "_init_tables_async", _fail_init)

    try:
        requested = await repository.upsert_requested_async(
            tool_call_id="call-async",
            run_id="run-1",
            session_id="session-1",
            task_id="task-1",
            instance_id="inst-1",
            role_id="writer",
            tool_name="orch_dispatch_task",
            args_preview="{}",
        )
        fetched = await repository.get_async("call-async")
        open_by_run = await repository.list_open_by_run_async("run-1")
        resolved = await repository.resolve_async(
            tool_call_id="call-async",
            status=ApprovalTicketStatus.APPROVED,
        )
        await repository.delete_by_run_async("run-1")
    finally:
        await repository.close_async()

    assert requested.tool_call_id == "call-async"
    assert fetched is not None
    assert tuple(record.tool_call_id for record in open_by_run) == ("call-async",)
    assert resolved.status == ApprovalTicketStatus.APPROVED


def test_approval_signature_key_prefers_cache_key_over_args_preview() -> None:
    cache_key = build_shell_cache_key(
        "bash -lc 'pwd'",
        workdir=".",
        tty=False,
        background=False,
    )

    wrapped = approval_signature_key(
        run_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="shell",
        args_preview='{"command": "bash -lc \\"pwd\\""}',
        cache_key=cache_key,
    )
    direct = approval_signature_key(
        run_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="shell",
        args_preview='{"command": "pwd"}',
        cache_key=cache_key,
    )

    assert wrapped == direct


def test_find_reusable_matches_approved_ticket_by_cache_key(tmp_path: Path) -> None:
    repository = ApprovalTicketRepository(tmp_path / "approval_ticket_cache_key.db")
    cache_key = build_shell_cache_key(
        "bash -lc 'pwd'",
        workdir=".",
        tty=False,
        background=False,
    )

    created = repository.upsert_requested(
        tool_call_id="call-approved",
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="shell",
        args_preview='{"command": "bash -lc \\"pwd\\""}',
        cache_key=cache_key,
    )
    repository.resolve(
        tool_call_id=created.tool_call_id,
        status=ApprovalTicketStatus.APPROVED,
    )

    record = repository.find_reusable(
        run_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="shell",
        args_preview='{"command": "pwd"}',
        cache_key=cache_key,
    )

    assert record is not None
    assert record.tool_call_id == "call-approved"


def test_approval_ticket_repo_persists_metadata_json(tmp_path: Path) -> None:
    repository = ApprovalTicketRepository(tmp_path / "approval_ticket_metadata.db")

    created = repository.upsert_requested(
        tool_call_id="call-shell",
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="shell",
        args_preview='{"command": "git status"}',
        metadata={
            "runtime_family": "git-bash",
            "normalized_command": "git status",
            "prefix_candidates": ["git status"],
        },
    )

    assert created.metadata["runtime_family"] == "git-bash"
    assert created.metadata["normalized_command"] == "git status"
    assert created.metadata["prefix_candidates"] == ["git status"]


def test_approval_ticket_repo_resolve_honors_expected_status(tmp_path: Path) -> None:
    repository = ApprovalTicketRepository(tmp_path / "approval_ticket_cas_ok.db")

    created = repository.upsert_requested(
        tool_call_id="call-approved",
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="orch_dispatch_task",
        args_preview="{}",
    )

    resolved = repository.resolve(
        tool_call_id=created.tool_call_id,
        status=ApprovalTicketStatus.APPROVED,
        expected_status=ApprovalTicketStatus.REQUESTED,
    )

    assert resolved.status == ApprovalTicketStatus.APPROVED


def test_approval_ticket_repo_resolve_raises_on_status_conflict(tmp_path: Path) -> None:
    repository = ApprovalTicketRepository(tmp_path / "approval_ticket_cas_conflict.db")

    created = repository.upsert_requested(
        tool_call_id="call-approved",
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="orch_dispatch_task",
        args_preview="{}",
    )
    _ = repository.resolve(
        tool_call_id=created.tool_call_id,
        status=ApprovalTicketStatus.APPROVED,
    )

    with pytest.raises(ApprovalTicketStatusConflictError) as exc_info:
        repository.resolve(
            tool_call_id=created.tool_call_id,
            status=ApprovalTicketStatus.TIMED_OUT,
            expected_status=ApprovalTicketStatus.REQUESTED,
        )

    assert exc_info.value.actual_status == ApprovalTicketStatus.APPROVED


def test_find_reusable_does_not_cross_exec_context_boundaries(tmp_path: Path) -> None:
    repository = ApprovalTicketRepository(tmp_path / "approval_ticket_exec_context.db")
    approved_cache_key = build_shell_cache_key(
        "bash -lc 'pwd'",
        workdir="one",
        tty=False,
        background=False,
    )
    mismatched_cache_key = build_shell_cache_key(
        "pwd",
        workdir="two",
        tty=True,
        background=False,
    )

    created = repository.upsert_requested(
        tool_call_id="call-approved",
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="shell",
        args_preview='{"command": "bash -lc \\"pwd\\""}',
        cache_key=approved_cache_key,
    )
    repository.resolve(
        tool_call_id=created.tool_call_id,
        status=ApprovalTicketStatus.APPROVED,
    )

    record = repository.find_reusable(
        run_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="shell",
        args_preview='{"command": "pwd"}',
        cache_key=mismatched_cache_key,
    )

    assert record is None


def test_find_reusable_does_not_collapse_multiline_command_whitespace(
    tmp_path: Path,
) -> None:
    repository = ApprovalTicketRepository(
        tmp_path / "approval_ticket_multiline_command.db"
    )
    approved_cache_key = build_shell_cache_key(
        "bash -lc \"\ncat <<'EOF'\nhello\n\nEOF\n\"",
        workdir="one",
        tty=False,
        background=False,
    )
    mismatched_cache_key = build_shell_cache_key(
        "bash -lc \"\ncat <<'EOF'\nhello\nEOF\n\"",
        workdir="one",
        tty=False,
        background=False,
    )

    created = repository.upsert_requested(
        tool_call_id="call-approved",
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="shell",
        args_preview='{"command": "bash -lc \\"cat <<EOF\\""}',
        cache_key=approved_cache_key,
    )
    repository.resolve(
        tool_call_id=created.tool_call_id,
        status=ApprovalTicketStatus.APPROVED,
    )

    record = repository.find_reusable(
        run_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="shell",
        args_preview='{"command": "bash -lc \\"cat <<EOF\\""}',
        cache_key=mismatched_cache_key,
    )

    assert record is None


def _insert_approval_ticket_row(
    db_path: Path,
    *,
    tool_call_id: str,
    run_id: str,
    session_id: str,
    signature_key: str = "sig-invalid",
    created_at: str | None = None,
    updated_at: str | None = None,
) -> None:
    now = datetime.now(tz=timezone.utc).isoformat()
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        INSERT INTO approval_tickets(
            tool_call_id,
            signature_key,
            run_id,
            session_id,
            task_id,
            instance_id,
            role_id,
            tool_name,
            args_preview,
            status,
            feedback,
            created_at,
            updated_at,
            resolved_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tool_call_id,
            signature_key,
            run_id,
            session_id,
            "task-2",
            "inst-2",
            "writer",
            "orch_dispatch_task",
            "{}",
            ApprovalTicketStatus.REQUESTED.value,
            "",
            created_at or now,
            updated_at or now,
            None,
        ),
    )
    connection.commit()
    connection.close()
