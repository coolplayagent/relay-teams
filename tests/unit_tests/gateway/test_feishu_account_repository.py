from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from relay_teams.gateway.feishu.account_repository import FeishuAccountRepository
from relay_teams.gateway.feishu.models import (
    FeishuGatewayAccountRecord,
    FeishuGatewayAccountStatus,
)


def test_feishu_account_repository_skips_invalid_persisted_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "feishu_accounts.db"
    repository = FeishuAccountRepository(db_path)
    now = datetime.now(tz=timezone.utc)
    _ = repository.create_account(
        FeishuGatewayAccountRecord(
            account_id="fsg_valid",
            name="feishu-valid",
            display_name="Feishu Valid",
            status=FeishuGatewayAccountStatus.ENABLED,
            source_config={
                "provider": "feishu",
                "trigger_rule": "mention_only",
                "app_id": "cli_demo",
                "app_name": "Agent Teams Bot",
            },
            target_config={"workspace_id": "default"},
            created_at=now,
            updated_at=now,
        )
    )
    _insert_feishu_account_row(
        db_path,
        account_id="None",
    )

    records = repository.list_accounts()

    assert [record.account_id for record in records] == ["fsg_valid"]
    with pytest.raises(KeyError):
        repository.get_account("None")


def _insert_feishu_account_row(
    db_path: Path,
    *,
    account_id: str,
) -> None:
    now = datetime.now(tz=timezone.utc).isoformat()
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        INSERT INTO feishu_gateway_accounts(
            account_id,
            name,
            display_name,
            status,
            source_config_json,
            target_config_json,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            "feishu-bad",
            "Broken Account",
            "enabled",
            '{"provider":"feishu","trigger_rule":"mention_only","app_id":"cli_demo","app_name":"Agent Teams Bot"}',
            '{"workspace_id":"default"}',
            now,
            now,
        ),
    )
    connection.commit()
    connection.close()
