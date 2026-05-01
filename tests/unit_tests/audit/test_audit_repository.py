from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from pydantic import JsonValue

from relay_teams.audit import (
    AuditEventCreate,
    AuditEventFilter,
    AuditEventRepository,
    AuditEventType,
)


def test_audit_repository_appends_and_filters_events(tmp_path: Path) -> None:
    repository = AuditEventRepository(tmp_path / "audit.db")
    file_event = repository.append(
        _event(
            event_type=AuditEventType.FILE_WRITE,
            target="src/app.py",
            metadata={"created": True},
        )
    )
    shell_event = repository.append(
        _event(
            event_type=AuditEventType.SHELL_COMMAND,
            target="uv run pytest",
            command="uv run pytest",
        )
    )

    page = repository.list_events(
        AuditEventFilter(
            event_type=AuditEventType.SHELL_COMMAND,
            run_id="run-1",
            after_id=file_event.id,
        )
    )

    assert tuple(item.audit_event_id for item in page.items) == (
        shell_event.audit_event_id,
    )
    assert page.items[0].command == "uv run pytest"
    assert page.next_after_id is None


def test_audit_repository_paginates_async(tmp_path: Path) -> None:
    repository = AuditEventRepository(tmp_path / "audit-async.db")
    started = datetime.now(UTC)
    for index in range(3):
        repository.append(
            _event(
                event_type=AuditEventType.FILE_WRITE,
                target=f"src/file_{index}.py",
                occurred_at=started + timedelta(seconds=index),
            )
        )

    async def scenario() -> None:
        page = await repository.list_events_async(
            AuditEventFilter(
                event_type=AuditEventType.FILE_WRITE,
                since=started,
                limit=2,
            )
        )
        assert [item.target for item in page.items] == [
            "src/file_0.py",
            "src/file_1.py",
        ]
        assert page.next_after_id == page.items[-1].id

        next_page = await repository.list_events_async(
            AuditEventFilter(after_id=page.next_after_id or 0)
        )
        assert [item.target for item in next_page.items] == ["src/file_2.py"]

    asyncio.run(scenario())


def test_audit_repository_normalizes_timestamp_filters_to_utc(tmp_path: Path) -> None:
    repository = AuditEventRepository(tmp_path / "audit-time.db")
    event = repository.append(
        _event(
            event_type=AuditEventType.FILE_WRITE,
            target="src/offset.py",
            occurred_at=datetime(
                2026,
                5,
                1,
                0,
                0,
                tzinfo=timezone(timedelta(hours=-5)),
            ),
        )
    )

    page = repository.list_events(
        AuditEventFilter(
            since=datetime(2026, 5, 1, 3, 0, tzinfo=UTC),
            until=datetime(
                2026,
                5,
                1,
                1,
                0,
                tzinfo=timezone(timedelta(hours=-4)),
            ),
        )
    )

    assert [item.audit_event_id for item in page.items] == [event.audit_event_id]
    assert page.items[0].occurred_at == datetime(2026, 5, 1, 5, 0, tzinfo=UTC)


def _event(
    *,
    event_type: AuditEventType,
    target: str,
    command: str | None = None,
    occurred_at: datetime | None = None,
    metadata: dict[str, JsonValue] | None = None,
) -> AuditEventCreate:
    return AuditEventCreate(
        event_type=event_type,
        trace_id="trace-1",
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
        instance_id="instance-1",
        role_id="coder",
        tool_call_id="toolcall-1",
        action="execute_shell_command"
        if event_type == AuditEventType.SHELL_COMMAND
        else "write_file",
        target=target,
        command=command,
        decision_reason="selected role for task"
        if event_type == AuditEventType.COORDINATOR_DECISION
        else None,
        outcome="completed",
        metadata=metadata or {},
        occurred_at=occurred_at or datetime.now(UTC),
    )
