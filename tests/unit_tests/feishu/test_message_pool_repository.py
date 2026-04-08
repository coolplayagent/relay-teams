# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from relay_teams.gateway.feishu.message_pool_repository import (
    FeishuMessagePoolRepository,
)
from relay_teams.gateway.feishu.models import (
    FeishuMessageDeliveryStatus,
    FeishuMessagePoolRecord,
    FeishuMessageProcessingStatus,
)


def test_message_pool_repo_normalizes_legacy_optional_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "feishu_message_pool_optional_ids.db"
    repo = FeishuMessagePoolRepository(db_path)
    created, _ = repo.create_or_get(
        _build_message_pool_record(
            message_pool_id="fmp-optional",
            message_key="msg-optional",
            processing_status=FeishuMessageProcessingStatus.WAITING_RESULT,
            message_id="om_1",
            session_id="session-1",
            run_id="run-1",
        )
    )
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        UPDATE feishu_message_pool
        SET message_id=?,
            session_id=?,
            run_id=?
        WHERE message_pool_id=?
        """,
        ("None", "null", "None", created.message_pool_id),
    )
    connection.commit()
    connection.close()

    loaded = repo.get(created.message_pool_id)
    waiting = repo.list_waiting_for_result(limit=10)

    assert loaded is not None
    assert loaded.message_id is None
    assert loaded.session_id is None
    assert loaded.run_id is None
    assert [record.message_pool_id for record in waiting] == [created.message_pool_id]


def test_message_pool_repo_skips_invalid_rows_in_polling_queries(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "feishu_message_pool_invalid_polling.db"
    repo = FeishuMessagePoolRepository(db_path)
    now = datetime.now(tz=timezone.utc)
    valid_ready, _ = repo.create_or_get(
        _build_message_pool_record(
            message_pool_id="fmp-valid-ready",
            message_key="msg-valid-ready",
            next_attempt_at=now - timedelta(seconds=1),
        )
    )
    invalid_ready, _ = repo.create_or_get(
        _build_message_pool_record(
            message_pool_id="fmp-invalid-ready",
            message_key="msg-invalid-ready",
            next_attempt_at=now - timedelta(seconds=1),
        )
    )
    valid_waiting, _ = repo.create_or_get(
        _build_message_pool_record(
            message_pool_id="fmp-valid-waiting",
            message_key="msg-valid-waiting",
            processing_status=FeishuMessageProcessingStatus.WAITING_RESULT,
        )
    )
    invalid_waiting, _ = repo.create_or_get(
        _build_message_pool_record(
            message_pool_id="fmp-invalid-waiting",
            message_key="msg-invalid-waiting",
            processing_status=FeishuMessageProcessingStatus.WAITING_RESULT,
        )
    )
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        UPDATE feishu_message_pool
        SET payload_json=?
        WHERE message_pool_id=?
        """,
        ("{", invalid_ready.message_pool_id),
    )
    connection.execute(
        """
        UPDATE feishu_message_pool
        SET created_at=?
        WHERE message_pool_id=?
        """,
        ("None", invalid_waiting.message_pool_id),
    )
    connection.commit()
    connection.close()

    ready = repo.list_ready_for_processing(ready_at=now, limit=10)
    waiting = repo.list_waiting_for_result(limit=10)

    assert [record.message_pool_id for record in ready] == [valid_ready.message_pool_id]
    assert [record.message_pool_id for record in waiting] == [
        valid_waiting.message_pool_id
    ]
    assert repo.get(invalid_ready.message_pool_id) is None
    assert repo.get(invalid_waiting.message_pool_id) is None


def _build_message_pool_record(
    *,
    message_pool_id: str,
    message_key: str,
    processing_status: FeishuMessageProcessingStatus = (
        FeishuMessageProcessingStatus.QUEUED
    ),
    next_attempt_at: datetime | None = None,
    message_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
) -> FeishuMessagePoolRecord:
    now = datetime.now(tz=timezone.utc)
    return FeishuMessagePoolRecord(
        message_pool_id=message_pool_id,
        trigger_id="trg_feishu",
        trigger_name="feishu_main",
        tenant_key="tenant-1",
        chat_id="oc_group_1",
        chat_type="group",
        event_id=f"evt-{message_key}",
        message_key=message_key,
        message_id=message_id,
        intent_text=f"intent for {message_key}",
        payload={"raw_text": "hello"},
        metadata={"provider": "feishu"},
        processing_status=processing_status,
        reaction_status=FeishuMessageDeliveryStatus.PENDING,
        ack_status=FeishuMessageDeliveryStatus.PENDING,
        final_reply_status=FeishuMessageDeliveryStatus.PENDING,
        session_id=session_id,
        run_id=run_id,
        next_attempt_at=next_attempt_at or now,
        created_at=now,
        updated_at=now,
    )
