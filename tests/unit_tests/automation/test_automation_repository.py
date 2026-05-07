from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from relay_teams.automation.automation_event_repository import (
    AutomationEventRepository,
    AutomationExecutionEventRecord,
)
from relay_teams.automation import (
    AutomationBoundSessionQueueRecord,
    AutomationBoundSessionQueueRepository,
    AutomationBoundSessionQueueStatus,
    AutomationCleanupStatus,
    AutomationDeliveryEvent,
    AutomationDeliveryRepository,
    AutomationDeliveryStatus,
    AutomationFeishuBinding,
    AutomationIntervalUnit,
    AutomationProjectRecord,
    AutomationProjectRepository,
    AutomationProjectStatus,
    AutomationScheduleMode,
    AutomationRunConfig,
    AutomationRunDeliveryRecord,
    AutomationXiaolubanBinding,
)
from relay_teams.automation.errors import AutomationProjectNameConflictError


def test_automation_project_repo_normalizes_legacy_optional_identifiers(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "automation_optional_ids.db"
    repository = AutomationProjectRepository(db_path)
    record = _build_project_record(
        automation_project_id="aut-optional",
        name="optional-project",
    )
    _ = repository.create(record)
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        UPDATE automation_projects
        SET last_session_id=?,
            run_config_json=?,
            delivery_binding_json=?
        WHERE automation_project_id=?
        """,
        (
            "None",
            json.dumps(
                {
                    "normal_root_role_id": "None",
                    "orchestration_preset_id": "None",
                }
            ),
            json.dumps(
                {
                    "provider": "feishu",
                    "trigger_id": "trigger-optional",
                    "tenant_key": "tenant-1",
                    "chat_id": "chat-1",
                    "session_id": "None",
                    "chat_type": "group",
                    "source_label": "Ops",
                }
            ),
            record.automation_project_id,
        ),
    )
    connection.commit()
    connection.close()

    loaded = repository.get(record.automation_project_id)

    assert loaded.last_session_id is None
    assert loaded.run_config.normal_root_role_id is None
    assert loaded.run_config.orchestration_preset_id is None
    assert loaded.delivery_binding is not None
    assert isinstance(loaded.delivery_binding, AutomationFeishuBinding)
    assert loaded.delivery_binding.session_id is None


def test_automation_project_repo_reads_legacy_feishu_binding_without_provider(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "automation_legacy_binding.db"
    repository = AutomationProjectRepository(db_path)
    record = _build_project_record(
        automation_project_id="aut-legacy-binding",
        name="legacy-binding-project",
    )
    _ = repository.create(record)
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        UPDATE automation_projects
        SET delivery_binding_json=?
        WHERE automation_project_id=?
        """,
        (
            json.dumps(
                {
                    "trigger_id": "trigger-legacy",
                    "tenant_key": "tenant-1",
                    "chat_id": "chat-legacy",
                    "session_id": "session-legacy",
                    "chat_type": "group",
                    "source_label": "Legacy Chat",
                }
            ),
            record.automation_project_id,
        ),
    )
    connection.commit()
    connection.close()

    loaded = repository.get(record.automation_project_id)

    assert loaded.delivery_binding is not None
    assert isinstance(loaded.delivery_binding, AutomationFeishuBinding)
    assert loaded.delivery_binding.provider == "feishu"
    assert loaded.delivery_binding.chat_id == "chat-legacy"


def test_automation_project_repo_roundtrips_xiaoluban_binding(
    tmp_path: Path,
) -> None:
    repository = AutomationProjectRepository(tmp_path / "automation_xiaoluban.db")
    record = _build_project_record(
        automation_project_id="aut-xiaoluban",
        name="xiaoluban-project",
    ).model_copy(
        update={
            "delivery_binding": AutomationXiaolubanBinding(
                account_id="xlb_1",
                display_name="Self Notify",
                derived_uid="uidself",
                source_label="发送给自己（uidself）",
            )
        }
    )

    _ = repository.create(record)

    loaded = repository.get(record.automation_project_id)

    assert loaded.delivery_binding is not None
    assert isinstance(loaded.delivery_binding, AutomationXiaolubanBinding)
    assert loaded.delivery_binding.account_id == "xlb_1"
    assert loaded.delivery_binding.derived_uid == "uidself"


def test_automation_project_repo_roundtrips_interval_schedule(
    tmp_path: Path,
) -> None:
    repository = AutomationProjectRepository(tmp_path / "automation_interval.db")
    record = _build_project_record(
        automation_project_id="aut-interval",
        name="interval-project",
    ).model_copy(
        update={
            "schedule_mode": AutomationScheduleMode.INTERVAL,
            "cron_expression": None,
            "interval_every": 15,
            "interval_unit": AutomationIntervalUnit.MINUTES,
        }
    )

    _ = repository.create(record)
    loaded = repository.get(record.automation_project_id)

    assert loaded.schedule_mode == AutomationScheduleMode.INTERVAL
    assert loaded.cron_expression is None
    assert loaded.interval_every == 15
    assert loaded.interval_unit == AutomationIntervalUnit.MINUTES


def test_automation_project_repo_skips_invalid_required_identifier_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "automation_invalid_workspace.db"
    repository = AutomationProjectRepository(db_path)
    now = datetime(2025, 1, 2, tzinfo=UTC)
    valid = _build_project_record(
        automation_project_id="aut-valid",
        name="valid-project",
        next_run_at=now,
    )
    invalid = _build_project_record(
        automation_project_id="aut-invalid",
        name="invalid-project",
        created_at=datetime(2025, 1, 3, tzinfo=UTC),
        updated_at=datetime(2025, 1, 3, tzinfo=UTC),
        next_run_at=now,
    )
    _ = repository.create(valid)
    _ = repository.create(invalid)
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        UPDATE automation_projects
        SET workspace_id=?
        WHERE automation_project_id=?
        """,
        ("None", invalid.automation_project_id),
    )
    connection.commit()
    connection.close()

    records = repository.list_all()
    due_records = repository.list_due(now)

    assert [record.automation_project_id for record in records] == ["aut-valid"]
    assert [record.automation_project_id for record in due_records] == ["aut-valid"]
    with pytest.raises(KeyError):
        repository.get(invalid.automation_project_id)


def test_automation_project_repo_skips_invalid_interval_every_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "automation_invalid_interval.db"
    repository = AutomationProjectRepository(db_path)
    record = _build_project_record(
        automation_project_id="aut-invalid-interval",
        name="invalid-interval-project",
    ).model_copy(
        update={
            "schedule_mode": AutomationScheduleMode.INTERVAL,
            "cron_expression": None,
            "interval_every": 1,
            "interval_unit": AutomationIntervalUnit.HOURS,
        }
    )
    _ = repository.create(record)
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        UPDATE automation_projects
        SET interval_every=?
        WHERE automation_project_id=?
        """,
        (0, record.automation_project_id),
    )
    connection.commit()
    connection.close()

    assert repository.list_all() == ()
    with pytest.raises(KeyError):
        repository.get(record.automation_project_id)


def test_automation_project_repo_async_methods_use_async_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AutomationProjectRepository(tmp_path / "automation_async.db")
    record = _build_project_record(
        automation_project_id="aut-async",
        name="async-project",
    )

    async def fail_call_sync_async(
        function: object,
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        raise AssertionError("async repository methods must not call sync wrappers")

    monkeypatch.setattr(repository, "_call_sync_async", fail_call_sync_async)

    async def exercise() -> None:
        created = await repository.create_async(record)
        loaded = await repository.get_async(record.automation_project_id)
        assert created.automation_project_id == record.automation_project_id
        assert loaded.name == "async-project"

        updated = loaded.model_copy(update={"display_name": "Async Project"})
        _ = await repository.update_async(updated)
        records = await repository.list_all_async()
        due_records = await repository.list_due_async(datetime(2026, 1, 1, tzinfo=UTC))

        assert [item.display_name for item in records] == ["Async Project"]
        assert due_records == ()

        await repository.delete_async(record.automation_project_id)
        with pytest.raises(KeyError):
            await repository.get_async(record.automation_project_id)

    asyncio.run(exercise())


def test_automation_project_repo_async_create_reports_name_conflict(
    tmp_path: Path,
) -> None:
    repository = AutomationProjectRepository(tmp_path / "automation_async_conflict.db")
    first = _build_project_record(
        automation_project_id="aut-first",
        name="duplicate-project",
    )
    duplicate = _build_project_record(
        automation_project_id="aut-duplicate",
        name="duplicate-project",
    )

    async def exercise() -> None:
        _ = await repository.create_async(first)
        with pytest.raises(
            AutomationProjectNameConflictError,
            match="Automation project name already exists: duplicate-project",
        ):
            await repository.create_async(duplicate)

    asyncio.run(exercise())


def test_automation_project_repo_async_update_reports_name_conflict(
    tmp_path: Path,
) -> None:
    repository = AutomationProjectRepository(
        tmp_path / "automation_async_update_conflict.db"
    )
    first = _build_project_record(
        automation_project_id="aut-first",
        name="first-project",
    )
    second = _build_project_record(
        automation_project_id="aut-second",
        name="second-project",
    )

    async def exercise() -> None:
        _ = await repository.create_async(first)
        created_second = await repository.create_async(second)
        with pytest.raises(
            AutomationProjectNameConflictError,
            match="Automation project name already exists: first-project",
        ):
            await repository.update_async(
                created_second.model_copy(update={"name": "first-project"})
            )

    asyncio.run(exercise())


def test_automation_project_repo_async_get_skips_invalid_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "automation_async_invalid_row.db"
    repository = AutomationProjectRepository(db_path)
    record = _build_project_record(
        automation_project_id="aut-invalid",
        name="invalid-project",
    )
    _ = repository.create(record)
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        UPDATE automation_projects
        SET workspace_id=?
        WHERE automation_project_id=?
        """,
        ("None", record.automation_project_id),
    )
    connection.commit()
    connection.close()

    async def exercise() -> None:
        with pytest.raises(KeyError):
            await repository.get_async(record.automation_project_id)

    asyncio.run(exercise())


def test_automation_event_repo_async_create_uses_async_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AutomationEventRepository(tmp_path / "automation_event_async.db")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    record = AutomationExecutionEventRecord(
        event_id="aevt_async",
        automation_project_id="aut-async",
        reason="manual",
        payload={"automation_project_id": "aut-async"},
        metadata={"reason": "manual"},
        occurred_at=now,
        created_at=now,
    )

    async def fail_call_sync_async(
        function: object,
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        raise AssertionError("async repository methods must not call sync wrappers")

    monkeypatch.setattr(repository, "_call_sync_async", fail_call_sync_async)

    async def exercise() -> None:
        created = await repository.create_event_async(record)
        assert created.event_id == "aevt_async"

    asyncio.run(exercise())

    connection = sqlite3.connect(tmp_path / "automation_event_async.db")
    row = connection.execute(
        "SELECT event_id FROM automation_execution_events WHERE event_id=?",
        ("aevt_async",),
    ).fetchone()
    connection.close()
    assert row == ("aevt_async",)


def test_automation_delivery_repo_async_methods_use_async_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AutomationDeliveryRepository(tmp_path / "automation_delivery.db")
    record = _build_delivery_record()

    async def fail_call_sync_async(
        function: object,
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        raise AssertionError("async repository methods must not call sync wrappers")

    monkeypatch.setattr(repository, "_call_sync_async", fail_call_sync_async)

    async def exercise() -> None:
        created = await repository.create_async(record)
        loaded = await repository.get_by_run_id_async(record.run_id)

        assert created.automation_delivery_id == record.automation_delivery_id
        assert loaded.started_status is AutomationDeliveryStatus.PENDING
        assert [
            item.automation_delivery_id
            for item in await repository.list_pending_started_async()
        ] == [record.automation_delivery_id]

        claimed_started = await repository.claim_started_async(
            automation_delivery_id=record.automation_delivery_id,
            stale_before=datetime(2026, 1, 2, tzinfo=UTC),
        )
        assert claimed_started is not None
        assert claimed_started.started_status is AutomationDeliveryStatus.SENDING

        updated = claimed_started.model_copy(
            update={
                "started_status": AutomationDeliveryStatus.SENT,
                "started_cleanup_status": AutomationCleanupStatus.PENDING,
                "updated_at": datetime(2026, 1, 3, tzinfo=UTC),
            }
        )
        _ = await repository.update_async(updated)

        assert await repository.list_pending_started_async() == ()
        assert [
            item.automation_delivery_id
            for item in await repository.list_pending_terminal_async()
        ] == [record.automation_delivery_id]
        assert [
            item.automation_delivery_id
            for item in await repository.list_pending_started_cleanup_async()
        ] == [record.automation_delivery_id]

        claimed_terminal = await repository.claim_terminal_async(
            automation_delivery_id=record.automation_delivery_id,
            stale_before=datetime(2026, 1, 4, tzinfo=UTC),
        )
        claimed_cleanup = await repository.claim_started_cleanup_async(
            automation_delivery_id=record.automation_delivery_id,
            stale_before=datetime(2026, 1, 4, tzinfo=UTC),
        )
        assert claimed_terminal is not None
        assert claimed_terminal.terminal_status is AutomationDeliveryStatus.SENDING
        assert claimed_cleanup is not None
        assert (
            claimed_cleanup.started_cleanup_status is AutomationCleanupStatus.CLEANING
        )

        assert await repository.has_project_records_async(record.automation_project_id)
        await repository.delete_by_project_async(record.automation_project_id)
        assert not await repository.has_project_records_async(
            record.automation_project_id
        )
        with pytest.raises(KeyError):
            await repository.get_by_run_id_async(record.run_id)

    asyncio.run(exercise())


def test_automation_delivery_repo_async_error_and_stale_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AutomationDeliveryRepository(tmp_path / "automation_delivery_edges.db")
    record = _build_delivery_record()

    async def fail_call_sync_async(
        function: object,
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        raise AssertionError("async repository methods must not call sync wrappers")

    monkeypatch.setattr(repository, "_call_sync_async", fail_call_sync_async)

    async def exercise() -> None:
        _ = await repository.create_async(record)

        assert [
            item.automation_delivery_id
            for item in await repository.list_pending_started_async(
                stale_before=datetime(2026, 1, 2, tzinfo=UTC)
            )
        ] == [record.automation_delivery_id]
        assert [
            item.automation_delivery_id
            for item in await repository.list_pending_terminal_async(
                stale_before=datetime(2026, 1, 2, tzinfo=UTC)
            )
        ] == [record.automation_delivery_id]
        assert (
            await repository.list_pending_started_cleanup_async(
                stale_before=datetime(2026, 1, 2, tzinfo=UTC)
            )
            == ()
        )

        assert (
            await repository.claim_started_async(
                automation_delivery_id="missing-delivery",
                stale_before=datetime(2026, 1, 2, tzinfo=UTC),
            )
            is None
        )
        assert (
            await repository.claim_terminal_async(
                automation_delivery_id="missing-delivery",
                stale_before=datetime(2026, 1, 2, tzinfo=UTC),
            )
            is None
        )
        assert (
            await repository.claim_started_cleanup_async(
                automation_delivery_id="missing-delivery",
                stale_before=datetime(2026, 1, 2, tzinfo=UTC),
            )
            is None
        )

    asyncio.run(exercise())


def test_automation_bound_session_queue_repo_async_methods_use_async_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AutomationBoundSessionQueueRepository(tmp_path / "automation_queue.db")
    first = _build_queue_record("queue-1")
    second = _build_queue_record("queue-2")

    async def fail_call_sync_async(
        function: object,
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        raise AssertionError("async repository methods must not call sync wrappers")

    monkeypatch.setattr(repository, "_call_sync_async", fail_call_sync_async)

    async def exercise() -> None:
        _ = await repository.create_async(first)
        _ = await repository.create_async(second)

        loaded = await repository.get_async(first.automation_queue_id)
        assert loaded is not None
        assert loaded.status is AutomationBoundSessionQueueStatus.QUEUED
        assert not await repository.has_non_terminal_item_for_run_async("")
        assert not await repository.has_non_terminal_item_for_run_async("run-1")
        assert await repository.count_non_terminal_by_session_async("session-1") == 2
        assert await repository.count_non_terminal_ahead_async("queue-2") == 1
        assert [
            item.automation_queue_id
            for item in await repository.list_ready_to_start_async(
                ready_at=datetime(2026, 1, 2, tzinfo=UTC)
            )
        ] == ["queue-1", "queue-2"]

        claimed = await repository.claim_starting_async(
            automation_queue_id=first.automation_queue_id,
            stale_before=datetime(2026, 1, 2, tzinfo=UTC),
        )
        assert claimed is not None
        assert claimed.status is AutomationBoundSessionQueueStatus.STARTING

        updated = claimed.model_copy(
            update={
                "run_id": "run-1",
                "status": AutomationBoundSessionQueueStatus.WAITING_RESULT,
                "queue_cleanup_status": AutomationCleanupStatus.PENDING,
                "updated_at": datetime(2026, 1, 3, tzinfo=UTC),
            }
        )
        _ = await repository.update_async(updated)

        assert await repository.has_non_terminal_item_for_run_async("run-1")
        assert [
            item.automation_queue_id
            for item in await repository.list_waiting_for_result_async()
        ] == [first.automation_queue_id]
        assert [
            item.automation_queue_id
            for item in await repository.list_pending_queue_cleanup_async()
        ] == [first.automation_queue_id]

        claimed_cleanup = await repository.claim_queue_cleanup_async(
            automation_queue_id=first.automation_queue_id,
            stale_before=datetime(2026, 1, 4, tzinfo=UTC),
        )
        assert claimed_cleanup is not None
        assert claimed_cleanup.queue_cleanup_status is AutomationCleanupStatus.CLEANING

        assert await repository.has_project_records_async(first.automation_project_id)
        await repository.delete_by_project_async(first.automation_project_id)
        assert await repository.get_async(first.automation_queue_id) is None
        assert not await repository.has_project_records_async(
            first.automation_project_id
        )

    asyncio.run(exercise())


def test_automation_bound_session_queue_repo_async_error_and_stale_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AutomationBoundSessionQueueRepository(
        tmp_path / "automation_queue_edges.db"
    )
    record = _build_queue_record("queue-edge")

    async def fail_call_sync_async(
        function: object,
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        raise AssertionError("async repository methods must not call sync wrappers")

    monkeypatch.setattr(repository, "_call_sync_async", fail_call_sync_async)

    async def exercise() -> None:
        created = await repository.create_async(record)
        updated = created.model_copy(
            update={
                "queue_cleanup_status": AutomationCleanupStatus.PENDING,
                "updated_at": datetime(2026, 1, 3, tzinfo=UTC),
            }
        )
        _ = await repository.update_async(updated)

        assert [
            item.automation_queue_id
            for item in await repository.list_pending_queue_cleanup_async(
                stale_before=datetime(2026, 1, 4, tzinfo=UTC)
            )
        ] == [record.automation_queue_id]
        assert (
            await repository.claim_starting_async(
                automation_queue_id="missing-queue",
                stale_before=datetime(2026, 1, 4, tzinfo=UTC),
            )
            is None
        )
        assert (
            await repository.claim_queue_cleanup_async(
                automation_queue_id="missing-queue",
                stale_before=datetime(2026, 1, 4, tzinfo=UTC),
            )
            is None
        )

    asyncio.run(exercise())


def test_automation_bound_session_queue_repo_async_reload_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AutomationBoundSessionQueueRepository(
        tmp_path / "automation_queue_reload.db"
    )
    record = _build_queue_record("queue-reload")

    async def missing_get(automation_queue_id: str) -> None:
        _ = automation_queue_id
        return None

    monkeypatch.setattr(repository, "get_async", missing_get)

    async def exercise() -> None:
        with pytest.raises(
            RuntimeError,
            match="Failed to persist automation bound session queue record",
        ):
            await repository.create_async(record)
        with pytest.raises(
            RuntimeError,
            match="Failed to reload automation bound session queue record",
        ):
            await repository.update_async(record)

    asyncio.run(exercise())


def _build_project_record(
    *,
    automation_project_id: str,
    name: str,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    next_run_at: datetime | None = None,
) -> AutomationProjectRecord:
    timestamp = created_at or datetime(2025, 1, 1, tzinfo=UTC)
    return AutomationProjectRecord(
        automation_project_id=automation_project_id,
        name=name,
        display_name=name,
        status=AutomationProjectStatus.ENABLED,
        workspace_id="default",
        prompt=f"Prompt for {name}",
        schedule_mode=AutomationScheduleMode.CRON,
        cron_expression="0 9 * * *",
        timezone="UTC",
        trigger_id=f"schedule-{automation_project_id}",
        created_at=timestamp,
        updated_at=updated_at or timestamp,
        next_run_at=next_run_at,
    )


def _build_delivery_record() -> AutomationRunDeliveryRecord:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    return AutomationRunDeliveryRecord(
        automation_delivery_id="delivery-1",
        automation_project_id="aut-delivery",
        automation_project_name="Delivery Project",
        run_id="run-delivery-1",
        session_id="session-1",
        reason="manual",
        binding=AutomationFeishuBinding(
            trigger_id="trigger-delivery",
            tenant_key="tenant-1",
            chat_id="oc_delivery",
            session_id="session-1",
            chat_type="group",
            source_label="Delivery Chat",
        ),
        delivery_events=(
            AutomationDeliveryEvent.STARTED,
            AutomationDeliveryEvent.COMPLETED,
        ),
        started_status=AutomationDeliveryStatus.PENDING,
        terminal_status=AutomationDeliveryStatus.PENDING,
        started_cleanup_status=AutomationCleanupStatus.SKIPPED,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _build_queue_record(automation_queue_id: str) -> AutomationBoundSessionQueueRecord:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    return AutomationBoundSessionQueueRecord(
        automation_queue_id=automation_queue_id,
        automation_project_id="aut-queue",
        automation_project_name="Queue Project",
        session_id="session-1",
        reason="schedule",
        binding=AutomationFeishuBinding(
            trigger_id="trigger-queue",
            tenant_key="tenant-1",
            chat_id="oc_queue",
            session_id="session-1",
            chat_type="group",
            source_label="Queue Chat",
        ),
        delivery_events=(
            AutomationDeliveryEvent.STARTED,
            AutomationDeliveryEvent.COMPLETED,
        ),
        run_config=AutomationRunConfig(),
        prompt="Summarize the queue.",
        queue_message="Queued automation run.",
        next_attempt_at=timestamp,
        resume_next_attempt_at=timestamp,
        created_at=timestamp,
        updated_at=timestamp,
    )
