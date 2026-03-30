from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from agent_teams.tools.runtime.approval_ticket_repo import (
    ApprovalTicketRepository,
    ApprovalTicketStatus,
)


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
        tool_call_id="call-invalid",
        run_id="run-1",
        session_id="session-1",
        updated_at="None",
    )

    records = repository.list_open_by_session("session-1")

    assert [record.tool_call_id for record in records] == ["call-valid"]
    assert repository.get("call-invalid") is None


def _insert_approval_ticket_row(
    db_path: Path,
    *,
    tool_call_id: str,
    run_id: str,
    session_id: str,
    updated_at: str,
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
            "sig-invalid",
            run_id,
            session_id,
            "task-2",
            "inst-2",
            "writer",
            "dispatch_task",
            "{}",
            ApprovalTicketStatus.REQUESTED.value,
            "",
            now,
            updated_at,
            None,
        ),
    )
    connection.commit()
    connection.close()
