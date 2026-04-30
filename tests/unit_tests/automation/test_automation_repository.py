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
    AutomationFeishuBinding,
    AutomationProjectRecord,
    AutomationProjectRepository,
    AutomationProjectStatus,
    AutomationScheduleMode,
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
