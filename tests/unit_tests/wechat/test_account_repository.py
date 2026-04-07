from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest
from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.sessions.session_models import SessionMode
from relay_teams.gateway.wechat import WeChatAccountRecord, WeChatAccountRepository


def test_wechat_account_repository_round_trips_account_settings(
    tmp_path: Path,
) -> None:
    repository = WeChatAccountRepository(tmp_path / "wechat.db")
    created = repository.upsert_account(
        WeChatAccountRecord(
            account_id="wx_123",
            display_name="WeChat Main",
            base_url="https://wechat.example.test",
            cdn_base_url="https://cdn.example.test",
            route_tag="route-a",
            workspace_id="workspace-ops",
            session_mode=SessionMode.ORCHESTRATION,
            orchestration_preset_id="ops",
            yolo=False,
            thinking=RunThinkingConfig(enabled=True, effort="high"),
            sync_cursor="cursor-1",
        )
    )

    loaded = repository.get_account(created.account_id)

    assert loaded.account_id == "wx_123"
    assert loaded.display_name == "WeChat Main"
    assert loaded.route_tag == "route-a"
    assert loaded.workspace_id == "workspace-ops"
    assert loaded.session_mode == SessionMode.ORCHESTRATION
    assert loaded.orchestration_preset_id == "ops"
    assert loaded.yolo is False
    assert loaded.thinking.enabled is True
    assert loaded.thinking.effort == "high"
    assert loaded.sync_cursor == "cursor-1"


def test_wechat_account_repository_deletes_account(tmp_path: Path) -> None:
    repository = WeChatAccountRepository(tmp_path / "wechat.db")
    _ = repository.upsert_account(
        WeChatAccountRecord(
            account_id="wx_delete",
            display_name="Delete Me",
        )
    )

    repository.delete_account("wx_delete")

    assert repository.list_accounts() == ()


def test_wechat_account_repository_preserves_literal_route_tag_values(
    tmp_path: Path,
) -> None:
    repository = WeChatAccountRepository(tmp_path / "wechat_route_tag.db")
    created = repository.upsert_account(
        WeChatAccountRecord(
            account_id="wx_none_tag",
            display_name="Literal None Tag",
            route_tag="none",
        )
    )

    loaded = repository.get_account(created.account_id)

    assert loaded.route_tag == "none"


def test_wechat_account_repository_get_recovers_invalid_timestamps(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wechat_dirty_timestamps.db"
    repository = WeChatAccountRepository(db_path)
    valid_updated_at = datetime(2025, 1, 3, tzinfo=timezone.utc).isoformat()
    _insert_wechat_account_row(
        db_path,
        account_id="wx_dirty",
        created_at="None",
        updated_at=valid_updated_at,
    )

    loaded = repository.get_account("wx_dirty")

    assert loaded.account_id == "wx_dirty"
    assert loaded.created_at.isoformat() == valid_updated_at
    assert loaded.updated_at.isoformat() == valid_updated_at
    assert repository.list_accounts() == ()


def test_wechat_account_repository_upsert_recovers_existing_dirty_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wechat_dirty_upsert.db"
    repository = WeChatAccountRepository(db_path)
    _insert_wechat_account_row(
        db_path,
        account_id="wx_dirty",
        created_at="None",
    )

    updated = repository.upsert_account(
        WeChatAccountRecord(
            account_id="wx_dirty",
            display_name="Recovered Account",
            route_tag="none",
        )
    )

    assert updated.account_id == "wx_dirty"
    assert updated.display_name == "Recovered Account"
    assert updated.route_tag == "none"


def test_wechat_account_repository_skips_invalid_persisted_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wechat_invalid_rows.db"
    repository = WeChatAccountRepository(db_path)
    _ = repository.upsert_account(
        WeChatAccountRecord(
            account_id="wx_valid",
            display_name="Valid Account",
        )
    )
    _insert_wechat_account_row(
        db_path,
        account_id="None",
    )

    records = repository.list_accounts()

    assert [record.account_id for record in records] == ["wx_valid"]
    with pytest.raises(KeyError):
        repository.get_account("None")


def _insert_wechat_account_row(
    db_path: Path,
    *,
    account_id: str,
    route_tag: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> None:
    now = datetime.now(tz=timezone.utc).isoformat()
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        INSERT INTO wechat_accounts(
            account_id,
            display_name,
            base_url,
            cdn_base_url,
            route_tag,
            status,
            remote_user_id,
            sync_cursor,
            workspace_id,
            session_mode,
            normal_root_role_id,
            orchestration_preset_id,
            yolo,
            thinking_json,
            last_login_at,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            "Broken Account",
            "https://wechat.example.test",
            "https://cdn.example.test",
            route_tag,
            "enabled",
            None,
            "",
            "default",
            "normal",
            None,
            None,
            1,
            "{}",
            None,
            created_at or now,
            updated_at or now,
        ),
    )
    connection.commit()
    connection.close()
