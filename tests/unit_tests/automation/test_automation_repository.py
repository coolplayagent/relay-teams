from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_teams.automation import (
    AutomationProjectRecord,
    AutomationProjectRepository,
    AutomationProjectStatus,
    AutomationScheduleMode,
)


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
            json.dumps({"orchestration_preset_id": "None"}),
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
    assert loaded.run_config.orchestration_preset_id is None
    assert loaded.delivery_binding is not None
    assert loaded.delivery_binding.session_id is None


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
