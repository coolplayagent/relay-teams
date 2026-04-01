from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from agent_teams.tools.runtime.approval_ticket_repo import (
    ApprovalTicketRepository,
    ApprovalTicketStatus,
    approval_signature_key,
)
from agent_teams.tools.workspace_tools.shell import build_shell_cache_key


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
        tool_name="dispatch_task",
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
        tool_name="dispatch_task",
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
        tool_name="dispatch_task",
        args_preview="{}",
    )

    assert record is not None
    assert record.tool_call_id == "call-valid"


def test_approval_signature_key_prefers_cache_key_over_args_preview() -> None:
    cache_key = build_shell_cache_key(
        "bash -lc 'pwd'",
        cwd=Path("/workspace"),
        tty=False,
        background=False,
    )

    wrapped = approval_signature_key(
        run_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="exec_command",
        args_preview='{"command": "bash -lc \\"pwd\\""}',
        cache_key=cache_key,
    )
    direct = approval_signature_key(
        run_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="exec_command",
        args_preview='{"command": "pwd"}',
        cache_key=cache_key,
    )

    assert wrapped == direct


def test_find_reusable_matches_approved_ticket_by_cache_key(tmp_path: Path) -> None:
    repository = ApprovalTicketRepository(tmp_path / "approval_ticket_cache_key.db")
    cache_key = build_shell_cache_key(
        "bash -lc 'pwd'",
        cwd=Path("/workspace"),
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
        tool_name="exec_command",
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
        tool_name="exec_command",
        args_preview='{"command": "pwd"}',
        cache_key=cache_key,
    )

    assert record is not None
    assert record.tool_call_id == "call-approved"


def test_find_reusable_does_not_cross_exec_context_boundaries(tmp_path: Path) -> None:
    repository = ApprovalTicketRepository(tmp_path / "approval_ticket_exec_context.db")
    approved_cache_key = build_shell_cache_key(
        "bash -lc 'pwd'",
        cwd=Path("/workspace/one"),
        tty=False,
        background=False,
    )
    mismatched_cache_key = build_shell_cache_key(
        "pwd",
        cwd=Path("/workspace/two"),
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
        tool_name="exec_command",
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
        tool_name="exec_command",
        args_preview='{"command": "pwd"}',
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
            "dispatch_task",
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
