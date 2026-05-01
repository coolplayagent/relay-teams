from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import JsonValue, ValidationError

from relay_teams.audit import (
    AuditEventCreate,
    AuditEventFilter,
    AuditEventRepository,
    AuditEventType,
)
from relay_teams.audit.repository import _json_object, _json_value, _optional_int
from relay_teams.audit.service import AuditService


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


def test_audit_event_model_validates_event_specific_fields() -> None:
    with pytest.raises(ValidationError):
        _event(event_type=AuditEventType.FILE_WRITE, target="")
    with pytest.raises(ValidationError):
        _event(event_type=AuditEventType.SHELL_COMMAND, target="shell")
    with pytest.raises(ValidationError):
        AuditEventCreate(
            event_type=AuditEventType.COORDINATOR_DECISION,
            trace_id="trace-1",
            run_id="run-1",
            session_id="session-1",
            action="dispatch_task",
            target="task:task-1->role:Reviewer",
            outcome="completed",
        )


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


def test_audit_repository_filters_identifiers_and_reports_missing_rows(
    tmp_path: Path,
) -> None:
    repository = AuditEventRepository(tmp_path / "audit-filter.db")
    expected = repository.append(
        _event(
            event_type=AuditEventType.FILE_WRITE,
            target="src/filter.py",
            trace_id="trace-filter",
            run_id="run-filter",
            session_id="session-filter",
            task_id="task-filter",
            role_id="role-filter",
        )
    )
    repository.append(
        _event(
            event_type=AuditEventType.FILE_WRITE,
            target="src/other.py",
            trace_id="trace-other",
            run_id="run-other",
            session_id="session-other",
            task_id="task-other",
            role_id="role-other",
        )
    )

    page = repository.list_events(
        AuditEventFilter(
            trace_id="trace-filter",
            run_id="run-filter",
            session_id="session-filter",
            task_id="task-filter",
            role_id="role-filter",
        )
    )
    assert [item.audit_event_id for item in page.items] == [expected.audit_event_id]
    with pytest.raises(KeyError):
        repository.get_by_audit_event_id("missing")

    async def scenario() -> None:
        await repository._init_tables_async()
        async_page = await repository.list_events_async(
            AuditEventFilter(
                trace_id="trace-filter",
                session_id="session-filter",
                task_id="task-filter",
                role_id="role-filter",
            )
        )
        assert [item.audit_event_id for item in async_page.items] == [
            expected.audit_event_id
        ]
        with pytest.raises(KeyError):
            await repository.get_by_audit_event_id_async("missing")

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


def test_audit_repository_json_and_integer_helpers_cover_dirty_values() -> None:
    assert _optional_int("42") == 42
    with pytest.raises(ValueError):
        _optional_int(object())
    assert _json_object("[]") == {}
    converted = _json_value(["x", {"nested": object()}])
    assert isinstance(converted, list)
    nested = converted[1]
    assert isinstance(nested, dict)
    assert isinstance(nested["nested"], str)


def test_audit_service_records_and_lists_sync_and_async(tmp_path: Path) -> None:
    repository = AuditEventRepository(tmp_path / "audit-service.db")
    service = AuditService(repository)
    sync_record = service.record_event(
        _event(event_type=AuditEventType.FILE_WRITE, target="src/sync.py")
    )
    assert service.list_events(AuditEventFilter()).items[0].id == sync_record.id

    async def scenario() -> None:
        async_record = await service.record_event_async(
            _event(event_type=AuditEventType.FILE_WRITE, target="src/async.py")
        )
        page = await service.list_events_async(
            AuditEventFilter(after_id=sync_record.id)
        )
        assert [item.id for item in page.items] == [async_record.id]

    asyncio.run(scenario())


def _event(
    *,
    event_type: AuditEventType,
    target: str,
    command: str | None = None,
    occurred_at: datetime | None = None,
    metadata: dict[str, JsonValue] | None = None,
    trace_id: str = "trace-1",
    run_id: str = "run-1",
    session_id: str = "session-1",
    task_id: str = "task-1",
    role_id: str = "coder",
) -> AuditEventCreate:
    return AuditEventCreate(
        event_type=event_type,
        trace_id=trace_id,
        run_id=run_id,
        session_id=session_id,
        task_id=task_id,
        instance_id="instance-1",
        role_id=role_id,
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
