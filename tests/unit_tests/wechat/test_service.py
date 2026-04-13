from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Mapping
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import cast

import pytest
from pydantic import ValidationError

from pydantic import JsonValue

from relay_teams.gateway.gateway_models import GatewayChannelType, GatewaySessionRecord
from relay_teams.gateway.gateway_session_service import GatewaySessionService
from relay_teams.gateway.im import ImSessionCommandService, ImToolService
from relay_teams.gateway.wechat.account_repository import WeChatAccountRepository
from relay_teams.gateway.wechat.client import WeChatClient
from relay_teams.gateway.wechat.inbound_queue_repository import (
    WeChatInboundQueueRepository,
)
from relay_teams.gateway.wechat.models import (
    WeChatAccountRecord,
    WeChatAccountUpdateInput,
    WeChatInboundMessage,
    WeChatInboundQueueRecord,
    WeChatInboundQueueStatus,
    WeChatMessageItem,
)
from relay_teams.gateway.wechat.secret_store import WeChatSecretStore
from relay_teams.gateway.wechat.service import WeChatGatewayService
from relay_teams.media import content_parts_from_text
from relay_teams.sessions import SessionService
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_manager import RunManager
from relay_teams.sessions.runs.run_models import RunEvent, RunResult
from relay_teams.sessions.session_models import SessionMode

_RECEIPT_CREATED = "\u6536\u5230\uff0c\u6b63\u5728\u5904\u7406\u3002"
_RECEIPT_QUEUED = "\u6536\u5230\uff0c\u5df2\u8fdb\u5165\u6392\u961f\u3002\u5f53\u524d\u4f1a\u8bdd\u524d\u9762\u8fd8\u6709 1 \u6761\u6d88\u606f\u3002"


def test_normalize_qr_code_url_keeps_image_url() -> None:
    value = "https://example.test/qr.png"

    result = WeChatGatewayService._normalize_qr_code_url(value)

    assert result == value


def test_normalize_qr_code_url_renders_non_image_url_as_svg_data_uri() -> None:
    value = "https://liteapp.weixin.qq.com/q/7GiQu1?qrcode=qr-token&bot_type=3"

    result = WeChatGatewayService._normalize_qr_code_url(value)

    assert result.startswith("data:image/svg+xml;base64,")
    encoded = result.removeprefix("data:image/svg+xml;base64,")
    decoded = base64.b64decode(encoded).decode("utf-8")
    assert decoded.startswith("<?xml")
    assert "<svg" in decoded


def test_normalize_qr_code_url_wraps_base64_png() -> None:
    result = WeChatGatewayService._normalize_qr_code_url("iVBORw0KGgoAAAANS")

    assert result == "data:image/png;base64,iVBORw0KGgoAAAANS"


def test_normalize_qr_code_url_wraps_base64_svg() -> None:
    result = WeChatGatewayService._normalize_qr_code_url(
        "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPjwvc3ZnPg=="
    )

    assert result == (
        "data:image/svg+xml;base64,"
        "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPjwvc3ZnPg=="
    )


@pytest.mark.asyncio
async def test_await_terminal_and_reply_records_success() -> None:
    service, gateway_session_service, _, im_tool_service, _ = _build_service(
        events=(
            _event(
                run_id="run-1",
                event_type=RunEventType.RUN_COMPLETED,
                payload={"output": "Reply sent"},
            ),
        )
    )
    service._watched_runs.add("run-1")

    await service._await_terminal_and_reply(
        account_id="wx-account-1",
        gateway_session_id="gws-1",
        run_id="run-1",
        peer_user_id="wx-peer-1",
        context_token="ctx-1",
    )

    assert im_tool_service.send_text_calls == [
        {
            "account_id": "wx-account-1",
            "peer_user_id": "wx-peer-1",
            "text": "Reply sent",
            "context_token": "ctx-1",
        }
    ]
    assert len(gateway_session_service.update_calls) == 1
    update_call = gateway_session_service.update_calls[0]
    assert update_call["gateway_session_id"] == "gws-1"
    assert update_call["peer_user_id"] == "wx-peer-1"
    assert update_call["peer_chat_id"] == "wx-peer-1"
    channel_state = cast(dict[str, object], update_call["channel_state"])
    assert channel_state["context_token"] == "ctx-1"
    assert isinstance(channel_state["last_outbound_at"], str)
    assert gateway_session_service.bind_calls == [("gws-1", None)]

    snapshot = service._status("wx-account-1")
    assert snapshot.last_error is None
    assert snapshot.last_outbound_at is not None
    assert snapshot.last_event_at == snapshot.last_outbound_at
    assert snapshot.last_outbound_at.isoformat() == channel_state["last_outbound_at"]
    assert "run-1" not in service._watched_runs


@pytest.mark.asyncio
async def test_await_terminal_and_reply_records_failure_when_send_fails() -> None:
    service, gateway_session_service, _, _, _ = _build_service(
        events=(
            _event(
                run_id="run-1",
                event_type=RunEventType.RUN_COMPLETED,
                payload={"output": "Reply sent"},
            ),
        ),
        send_text_error=RuntimeError("WeChat send_text_message failed: ret=4001"),
    )
    service._watched_runs.add("run-1")

    with pytest.raises(RuntimeError, match="WeChat send_text_message failed: ret=4001"):
        await service._await_terminal_and_reply(
            account_id="wx-account-1",
            gateway_session_id="gws-1",
            run_id="run-1",
            peer_user_id="wx-peer-1",
            context_token="ctx-1",
        )

    assert gateway_session_service.update_calls == []
    assert gateway_session_service.bind_calls == [("gws-1", None)]

    snapshot = service._status("wx-account-1")
    assert snapshot.last_error == "WeChat send_text_message failed: ret=4001"
    assert snapshot.last_outbound_at is None
    assert snapshot.last_event_at is not None
    assert "run-1" not in service._watched_runs


@pytest.mark.asyncio
async def test_await_terminal_and_reply_uses_structured_completed_output() -> None:
    service, _, _, im_tool_service, _ = _build_service(
        events=(
            RunEvent(
                session_id="session-1",
                run_id="run-1",
                trace_id="run-1",
                event_type=RunEventType.RUN_COMPLETED,
                payload_json=RunResult(
                    trace_id="run-1",
                    root_task_id="task-root-1",
                    status="completed",
                    output=content_parts_from_text("Structured reply"),
                ).model_dump_json(),
                occurred_at=datetime.now(tz=timezone.utc),
            ),
        )
    )
    service._watched_runs.add("run-1")

    await service._await_terminal_and_reply(
        account_id="wx-account-1",
        gateway_session_id="gws-1",
        run_id="run-1",
        peer_user_id="wx-peer-1",
        context_token="ctx-1",
    )

    assert im_tool_service.send_text_calls == [
        {
            "account_id": "wx-account-1",
            "peer_user_id": "wx-peer-1",
            "text": "Structured reply",
            "context_token": "ctx-1",
        }
    ]


@pytest.mark.asyncio
async def test_await_terminal_and_reply_sends_pause_notice_without_clearing_binding() -> (
    None
):
    service, gateway_session_service, _, im_tool_service, _ = _build_service(
        events=(
            _event(
                run_id="run-1",
                event_type=RunEventType.RUN_PAUSED,
                payload={"error_message": "stream interrupted"},
            ),
        )
    )
    service._watched_runs.add("run-1")

    await service._await_terminal_and_reply(
        account_id="wx-account-1",
        gateway_session_id="gws-1",
        run_id="run-1",
        peer_user_id="wx-peer-1",
        context_token="ctx-1",
    )

    assert im_tool_service.send_text_calls == [
        {
            "account_id": "wx-account-1",
            "peer_user_id": "wx-peer-1",
            "text": "Run paused: stream interrupted\nSend resume to continue.",
            "context_token": "ctx-1",
        }
    ]
    assert gateway_session_service.bind_calls == []
    snapshot = service._status("wx-account-1")
    assert snapshot.last_error is None
    assert snapshot.last_outbound_at is not None
    assert snapshot.last_event_at == snapshot.last_outbound_at
    assert "run-1" not in service._watched_runs


@pytest.mark.asyncio
async def test_await_run_completion_for_queue_drain_stops_on_terminal_event() -> None:
    service, _gateway_session_service, _run_service, _im_tool_service, _ = (
        _build_service(
            events=(
                _event(
                    run_id="external-run-1",
                    event_type=RunEventType.RUN_COMPLETED,
                    payload={"output": "done"},
                ),
            ),
            has_active_run=False,
        )
    )

    await service._await_run_completion_for_queue_drain(
        session_id="session-1",
        run_id="external-run-1",
    )


def test_handle_reply_future_records_cancelled_future() -> None:
    service, gateway_session_service, _, _, _ = _build_service(events=())
    future: Future[None] = Future()
    _ = future.cancel()

    service._handle_reply_future(
        account_id="wx-account-1",
        gateway_session_id="gws-1",
        run_id="run-1",
        peer_user_id="wx-peer-1",
        future=future,
    )

    assert gateway_session_service.bind_calls == [("gws-1", None)]
    snapshot = service._status("wx-account-1")
    assert snapshot.last_error == "WeChat reply task was cancelled for run run-1."
    assert snapshot.last_event_at is not None
    assert snapshot.last_outbound_at is None


def test_handle_message_intercepts_session_command() -> None:
    service, gateway_session_service, run_service, im_tool_service, command_service = (
        _build_service(events=(), command_response="[Session Commands]")
    )

    service._handle_message(
        _account(),
        "bot-token",
        WeChatInboundMessage(
            from_user_id="wx-peer-1",
            item_list=(_text_item("help"),),
        ),
    )

    assert command_service.calls == [
        {
            "session_id": "session-1",
            "gateway_session_id": "gws-1",
            "text": "help",
        }
    ]
    assert im_tool_service.send_text_calls == [
        {
            "account_id": "wx-account-1",
            "peer_user_id": "wx-peer-1",
            "text": "[Session Commands]",
            "context_token": None,
        }
    ]
    assert run_service.created_intents == []
    assert gateway_session_service.bind_calls == []
    assert len(gateway_session_service.resolved_sessions) == 1


def test_handle_message_sends_receipt_before_starting_run() -> None:
    service, gateway_session_service, run_service, im_tool_service, _ = _build_service(
        events=(),
        has_active_run=False,
    )
    service._watched_runs.add("run-created")

    service._handle_message(
        _account(),
        "bot-token",
        WeChatInboundMessage(
            from_user_id="wx-peer-1",
            context_token="ctx-1",
            item_list=(_text_item("hello"),),
        ),
    )

    assert im_tool_service.send_text_calls == [
        {
            "account_id": "wx-account-1",
            "peer_user_id": "wx-peer-1",
            "text": _RECEIPT_CREATED,
            "context_token": "ctx-1",
        }
    ]
    assert len(run_service.created_intents) == 1
    assert run_service.created_intents[0]["intent"] == "hello"
    assert run_service.ensured_run_ids == ["run-created"]
    assert gateway_session_service.bind_calls == [("gws-1", "run-created")]


def test_handle_message_queues_when_run_is_already_active() -> None:
    service, _, run_service, im_tool_service, _ = _build_service(
        events=(),
        has_active_run=True,
    )

    service._handle_message(
        _account(),
        "bot-token",
        WeChatInboundMessage(
            from_user_id="wx-peer-1",
            item_list=(_text_item("follow up"),),
        ),
    )

    assert im_tool_service.send_text_calls[0]["text"] == _RECEIPT_QUEUED
    assert len(run_service.created_intents) == 0


def test_drain_inbound_queue_starts_queue_drain_watcher_for_external_blocker() -> None:
    service, _gateway_session_service, run_service, _im_tool_service, _ = (
        _build_service(
            events=(),
            has_active_run=True,
        )
    )
    repo = cast(_FakeInboundQueueRepo, service._inbound_queue_repo)
    watcher_calls: list[tuple[str, str]] = []
    repo.records["inq-external"] = WeChatInboundQueueRecord(
        inbound_queue_id="inq-external",
        account_id="wx-account-1",
        message_key="mid:external",
        gateway_session_id="gws-1",
        session_id="session-1",
        peer_user_id="wx-peer-1",
        text="hello",
    )
    setattr(
        service,
        "_start_queue_drain_watcher",
        lambda *, session_id, run_id: watcher_calls.append((session_id, run_id)),
    )

    service._drain_inbound_queue()

    assert watcher_calls == [("session-1", "run-1")]
    assert run_service.created_intents == []
    queued = repo.get("inq-external")
    assert queued is not None
    assert queued.status == WeChatInboundQueueStatus.QUEUED


def test_drain_inbound_queue_starts_queue_drain_watcher_for_waiting_result_blocker() -> (
    None
):
    service, _gateway_session_service, run_service, _im_tool_service, _ = (
        _build_service(
            events=(),
            has_active_run=True,
        )
    )
    repo = cast(_FakeInboundQueueRepo, service._inbound_queue_repo)
    repo.records["inq-active"] = WeChatInboundQueueRecord(
        inbound_queue_id="inq-active",
        account_id="wx-account-1",
        message_key="mid:active",
        gateway_session_id="gws-1",
        session_id="session-1",
        peer_user_id="wx-peer-1",
        text="first",
        status=WeChatInboundQueueStatus.WAITING_RESULT,
        run_id="run-1",
    )
    repo.records["inq-queued"] = WeChatInboundQueueRecord(
        inbound_queue_id="inq-queued",
        account_id="wx-account-1",
        message_key="mid:queued",
        gateway_session_id="gws-1",
        session_id="session-1",
        peer_user_id="wx-peer-1",
        text="second",
    )
    watcher_calls: list[tuple[str, str]] = []
    setattr(
        service,
        "_start_queue_drain_watcher",
        lambda *, session_id, run_id: watcher_calls.append((session_id, run_id)),
    )

    service._drain_inbound_queue()

    assert watcher_calls == [("session-1", "run-1")]
    assert run_service.created_intents == []
    queued = repo.get("inq-queued")
    assert queued is not None
    assert queued.status == WeChatInboundQueueStatus.QUEUED


def test_start_queued_record_busy_retry_does_not_clobber_waiting_result() -> None:
    service, _gateway_session_service, _run_service, _im_tool_service, _ = (
        _build_service(
            events=(),
            has_active_run=False,
        )
    )
    repo = cast(_FakeInboundQueueRepo, service._inbound_queue_repo)
    record = WeChatInboundQueueRecord(
        inbound_queue_id="inq-1",
        account_id="wx-account-1",
        message_key="mid:1",
        gateway_session_id="gws-1",
        session_id="session-1",
        peer_user_id="wx-peer-1",
        context_token="ctx-1",
        text="hello",
        status=WeChatInboundQueueStatus.STARTING,
    )
    repo.records[record.inbound_queue_id] = record

    def _simulate_concurrent_start(intent: object) -> str:
        _ = intent
        current = repo.get("inq-1")
        assert current is not None
        repo.update(
            current.model_copy(
                update={
                    "status": WeChatInboundQueueStatus.WAITING_RESULT,
                    "run_id": "run-existing",
                    "updated_at": datetime.now(tz=timezone.utc),
                }
            )
        )
        raise RuntimeError("session_busy")

    setattr(service, "_start_session_ingress_run", _simulate_concurrent_start)

    started = service._start_queued_record(record)
    updated = repo.get("inq-1")

    assert started is False
    assert updated is not None
    assert updated.status == WeChatInboundQueueStatus.WAITING_RESULT
    assert updated.run_id == "run-existing"


def test_handle_queue_drain_future_redrains_when_blocker_clears() -> None:
    service, _gateway_session_service, _run_service, _im_tool_service, _ = (
        _build_service(
            events=(),
            has_active_run=False,
        )
    )
    future: Future[None] = Future()
    future.set_result(None)
    drain_calls: list[str] = []
    service._drain_watched_runs.add("external-run-1")
    setattr(service, "_drain_inbound_queue", lambda: drain_calls.append("drain"))

    service._handle_queue_drain_future(
        session_id="session-1",
        run_id="external-run-1",
        future=future,
    )

    assert drain_calls == ["drain"]
    assert "external-run-1" not in service._drain_watched_runs


def test_start_queued_record_requeues_non_busy_start_failure() -> None:
    service, _gateway_session_service, _run_service, _im_tool_service, _ = (
        _build_service(
            events=(),
            has_active_run=False,
        )
    )
    repo = cast(_FakeInboundQueueRepo, service._inbound_queue_repo)
    record = WeChatInboundQueueRecord(
        inbound_queue_id="inq-2",
        account_id="wx-account-1",
        message_key="mid:2",
        gateway_session_id="gws-1",
        session_id="session-1",
        peer_user_id="wx-peer-1",
        context_token="ctx-2",
        text="hello",
        status=WeChatInboundQueueStatus.STARTING,
    )
    repo.records[record.inbound_queue_id] = record

    def _fail_start(intent: object) -> str:
        _ = intent
        raise RuntimeError("temporary_start_failure")

    setattr(service, "_start_session_ingress_run", _fail_start)

    started = service._start_queued_record(record)
    updated = repo.get("inq-2")

    assert started is False
    assert updated is not None
    assert updated.status == WeChatInboundQueueStatus.QUEUED
    assert updated.last_error == "temporary_start_failure"
    assert updated.completed_at is None


def test_start_queued_record_marks_missing_account_failed() -> None:
    service, _gateway_session_service, _run_service, _im_tool_service, _ = (
        _build_service(
            events=(),
            has_active_run=False,
        )
    )
    repo = cast(_FakeInboundQueueRepo, service._inbound_queue_repo)
    record = WeChatInboundQueueRecord(
        inbound_queue_id="inq-missing-account",
        account_id="missing-account",
        message_key="mid:missing-account",
        gateway_session_id="gws-1",
        session_id="session-1",
        peer_user_id="wx-peer-1",
        context_token="ctx-1",
        text="hello",
        status=WeChatInboundQueueStatus.STARTING,
    )
    repo.records[record.inbound_queue_id] = record

    started = service._start_queued_record(record)
    updated = repo.get("inq-missing-account")

    assert started is False
    assert updated is not None
    assert updated.status == WeChatInboundQueueStatus.FAILED
    assert updated.run_id is None
    assert updated.last_error == "WeChat account not found: missing-account"
    assert updated.completed_at is not None


def test_drain_inbound_queue_skips_missing_account_and_starts_later_record() -> None:
    service, _gateway_session_service, run_service, _im_tool_service, _ = (
        _build_service(
            events=(),
            has_active_run=False,
        )
    )
    repo = cast(_FakeInboundQueueRepo, service._inbound_queue_repo)
    setattr(service, "_start_run_watcher", lambda **_: None)
    repo.records["inq-bad"] = WeChatInboundQueueRecord(
        inbound_queue_id="inq-bad",
        account_id="missing-account",
        message_key="mid:bad",
        gateway_session_id="gws-bad",
        session_id="session-bad",
        peer_user_id="wx-peer-1",
        context_token="ctx-bad",
        text="bad",
    )
    repo.records["inq-good"] = WeChatInboundQueueRecord(
        inbound_queue_id="inq-good",
        account_id="wx-account-1",
        message_key="mid:good",
        gateway_session_id="gws-1",
        session_id="session-1",
        peer_user_id="wx-peer-1",
        context_token="ctx-1",
        text="good",
    )

    service._drain_inbound_queue()

    bad = repo.get("inq-bad")
    good = repo.get("inq-good")
    assert bad is not None
    assert bad.status == WeChatInboundQueueStatus.FAILED
    assert bad.last_error == "WeChat account not found: missing-account"
    assert good is not None
    assert good.status == WeChatInboundQueueStatus.WAITING_RESULT
    assert len(run_service.created_intents) == 1
    assert run_service.created_intents[0]["session_id"] == "session-1"


def test_build_receipt_text_returns_failure_receipt_for_failed_record() -> None:
    service, _gateway_session_service, _run_service, _im_tool_service, _ = (
        _build_service(
            events=(),
            has_active_run=False,
        )
    )
    record = WeChatInboundQueueRecord(
        inbound_queue_id="inq-failed",
        account_id="wx-account-1",
        message_key="mid:failed",
        gateway_session_id="gws-1",
        session_id="session-1",
        peer_user_id="wx-peer-1",
        text="hello",
        status=WeChatInboundQueueStatus.FAILED,
        last_error="temporary_start_failure",
    )

    receipt_text = service._build_receipt_text(record)

    assert receipt_text == "收到，但处理失败：temporary_start_failure"


class _FakeRepository:
    def __init__(self, account: WeChatAccountRecord) -> None:
        self._account = account

    def get_account(self, account_id: str) -> WeChatAccountRecord:
        if account_id != self._account.account_id:
            raise KeyError(account_id)
        return self._account


class _FakeSecretStore:
    def __init__(self, token: str | None) -> None:
        self._token = token

    def get_bot_token(self, config_dir: Path, account_id: str) -> str | None:
        _ = (config_dir, account_id)
        return self._token


class _FakeWeChatClient:
    def __init__(self) -> None:
        self.typing_calls: list[dict[str, object]] = []

    def get_typing_ticket(
        self,
        *,
        account: WeChatAccountRecord,
        token: str,
        peer_user_id: str,
        context_token: str | None,
    ) -> str | None:
        _ = (account, token, peer_user_id, context_token)
        return "typing-ticket"

    def send_typing(
        self,
        *,
        account: WeChatAccountRecord,
        token: str,
        peer_user_id: str,
        typing_ticket: str,
        status: int,
    ) -> None:
        self.typing_calls.append(
            {
                "account_id": account.account_id,
                "token": token,
                "peer_user_id": peer_user_id,
                "typing_ticket": typing_ticket,
                "status": status,
            }
        )


class _FakeImToolService:
    def __init__(self, send_text_error: Exception | None = None) -> None:
        self._send_text_error = send_text_error
        self.send_text_calls: list[dict[str, str | None]] = []

    def send_text_to_wechat_peer(
        self,
        *,
        account_id: str,
        peer_user_id: str,
        text: str,
        context_token: str | None,
    ) -> None:
        self.send_text_calls.append(
            {
                "account_id": account_id,
                "peer_user_id": peer_user_id,
                "text": text,
                "context_token": context_token,
            }
        )
        if self._send_text_error is not None:
            raise self._send_text_error


class _FakeCommandService:
    def __init__(self, response: str | None = None) -> None:
        self._response = response
        self.calls: list[dict[str, str]] = []

    def handle_wechat_command(
        self,
        *,
        session_id: str,
        gateway_session_id: str,
        text: str,
    ) -> str | None:
        self.calls.append(
            {
                "session_id": session_id,
                "gateway_session_id": gateway_session_id,
                "text": text,
            }
        )
        return self._response


class _FakeGatewaySessionService:
    def __init__(self) -> None:
        self.bind_calls: list[tuple[str, str | None]] = []
        self.update_calls: list[dict[str, object]] = []
        self.resolved_sessions: list[GatewaySessionRecord] = []

    def resolve_or_create_session(
        self,
        *,
        channel_type: GatewayChannelType,
        external_session_id: str,
        workspace_id: str,
        metadata: Mapping[str, str] | None = None,
        session_mode: SessionMode | None = None,
        normal_root_role_id: str | None = None,
        orchestration_preset_id: str | None = None,
        cwd: str | None = None,
        capabilities: Mapping[str, object] | None = None,
        channel_state: Mapping[str, object] | None = None,
        peer_user_id: str | None = None,
        peer_chat_id: str | None = None,
    ) -> GatewaySessionRecord:
        _ = (
            workspace_id,
            metadata,
            session_mode,
            normal_root_role_id,
            orchestration_preset_id,
            cwd,
            capabilities,
        )
        record = GatewaySessionRecord(
            gateway_session_id="gws-1",
            channel_type=channel_type,
            external_session_id=external_session_id,
            internal_session_id="session-1",
            active_run_id=None,
            peer_user_id=peer_user_id,
            peer_chat_id=peer_chat_id,
            channel_state=cast(
                dict[str, JsonValue],
                {} if channel_state is None else dict(channel_state.items()),
            ),
        )
        self.resolved_sessions.append(record)
        return record

    def bind_active_run(self, gateway_session_id: str, run_id: str | None) -> None:
        self.bind_calls.append((gateway_session_id, run_id))

    def update_channel_state(
        self,
        gateway_session_id: str,
        *,
        channel_state: Mapping[str, object],
        peer_user_id: str | None = None,
        peer_chat_id: str | None = None,
    ) -> None:
        self.update_calls.append(
            {
                "gateway_session_id": gateway_session_id,
                "channel_state": dict(channel_state.items()),
                "peer_user_id": peer_user_id,
                "peer_chat_id": peer_chat_id,
            }
        )


class _FakeInboundQueueRepo:
    def __init__(self) -> None:
        self.records: dict[str, WeChatInboundQueueRecord] = {}

    def create_or_get(
        self,
        record: WeChatInboundQueueRecord,
    ) -> tuple[WeChatInboundQueueRecord, bool]:
        for existing in self.records.values():
            if (
                existing.account_id == record.account_id
                and existing.peer_user_id == record.peer_user_id
                and existing.message_key == record.message_key
            ):
                return existing, False
        self.records[record.inbound_queue_id] = record
        return record, True

    def get(self, inbound_queue_id: str) -> WeChatInboundQueueRecord | None:
        return self.records.get(inbound_queue_id)

    def get_latest_by_run_id(self, run_id: str) -> WeChatInboundQueueRecord | None:
        matches = [
            record
            for record in self.records.values()
            if str(record.run_id or "") == run_id
        ]
        if not matches:
            return None
        matches.sort(key=lambda item: item.updated_at, reverse=True)
        return matches[0]

    def update(self, record: WeChatInboundQueueRecord) -> WeChatInboundQueueRecord:
        self.records[record.inbound_queue_id] = record
        return record

    def list_ready_to_start(
        self,
        *,
        stale_before: datetime | None = None,
    ) -> tuple[WeChatInboundQueueRecord, ...]:
        ready = [
            record
            for record in self.records.values()
            if record.status == WeChatInboundQueueStatus.QUEUED
            or (
                stale_before is not None
                and record.status == WeChatInboundQueueStatus.STARTING
                and record.updated_at <= stale_before
            )
        ]
        ready.sort(key=lambda item: item.created_at)
        return tuple(ready)

    def claim_starting(
        self,
        *,
        inbound_queue_id: str,
        stale_before: datetime,
    ) -> WeChatInboundQueueRecord | None:
        record = self.records.get(inbound_queue_id)
        if record is None:
            return None
        if record.status not in {
            WeChatInboundQueueStatus.QUEUED,
            WeChatInboundQueueStatus.STARTING,
        }:
            return None
        if (
            record.status == WeChatInboundQueueStatus.STARTING
            and record.updated_at > stale_before
        ):
            return None
        claimed = record.model_copy(
            update={
                "status": WeChatInboundQueueStatus.STARTING,
                "last_error": None,
                "updated_at": datetime.now(tz=timezone.utc),
            }
        )
        self.records[inbound_queue_id] = claimed
        return claimed

    def requeue_if_starting(
        self,
        *,
        inbound_queue_id: str,
        last_error: str | None = None,
    ) -> WeChatInboundQueueRecord | None:
        record = self.records.get(inbound_queue_id)
        if record is None or record.status != WeChatInboundQueueStatus.STARTING:
            return None
        updated = record.model_copy(
            update={
                "status": WeChatInboundQueueStatus.QUEUED,
                "run_id": None,
                "last_error": last_error,
                "updated_at": datetime.now(tz=timezone.utc),
                "completed_at": None,
            }
        )
        self.records[inbound_queue_id] = updated
        return updated

    def count_non_terminal_ahead(self, inbound_queue_id: str) -> int:
        current = self.records[inbound_queue_id]
        ordered = list(self.records.values())
        current_index = ordered.index(current)
        return sum(
            1
            for record in ordered[:current_index]
            if record.session_id == current.session_id
            and record.status
            in {
                WeChatInboundQueueStatus.QUEUED,
                WeChatInboundQueueStatus.STARTING,
                WeChatInboundQueueStatus.WAITING_RESULT,
            }
        )

    def has_non_terminal_item_for_run(self, run_id: str) -> bool:
        return any(
            str(record.run_id or "") == run_id
            and record.status
            in {
                WeChatInboundQueueStatus.QUEUED,
                WeChatInboundQueueStatus.STARTING,
                WeChatInboundQueueStatus.WAITING_RESULT,
            }
            for record in self.records.values()
        )


class _FakeRunService:
    def __init__(self, events: tuple[RunEvent, ...]) -> None:
        self._events = events
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self.created_intents: list[dict[str, str | bool]] = []
        self.ensured_run_ids: list[str] = []

    async def stream_run_events(self, run_id: str) -> AsyncIterator[RunEvent]:
        for event in self._events:
            assert event.run_id == run_id
            yield event

    def create_run(self, intent: object) -> tuple[str, str]:
        session_id = cast(str, getattr(intent, "session_id"))
        message = cast(str, getattr(intent, "intent"))
        yolo = cast(bool, getattr(intent, "yolo"))
        self.created_intents.append(
            {
                "session_id": session_id,
                "intent": message,
                "yolo": yolo,
            }
        )
        return "run-created", "session-1"

    def ensure_run_started(self, run_id: str) -> None:
        self.ensured_run_ids.append(run_id)


class _FakeSessionService:
    def __init__(self, *, has_active_run: bool) -> None:
        self._has_active_run = has_active_run

    def get_recovery_snapshot(self, session_id: str) -> dict[str, object]:
        _ = session_id
        if not self._has_active_run:
            return {"active_run": None}
        return {
            "active_run": {
                "run_id": "run-1",
                "status": "running",
                "phase": "running",
            }
        }


def _build_service(
    *,
    events: tuple[RunEvent, ...],
    send_text_error: Exception | None = None,
    command_response: str | None = None,
    has_active_run: bool = False,
) -> tuple[
    WeChatGatewayService,
    _FakeGatewaySessionService,
    _FakeRunService,
    _FakeImToolService,
    _FakeCommandService,
]:
    account = _account()
    gateway_session_service = _FakeGatewaySessionService()
    run_service = _FakeRunService(events)
    im_tool_service = _FakeImToolService(send_text_error=send_text_error)
    command_service = _FakeCommandService(command_response)

    service = object.__new__(WeChatGatewayService)
    service._config_dir = Path("C:/config")
    service._repository = cast(WeChatAccountRepository, _FakeRepository(account))
    service._secret_store = cast(WeChatSecretStore, _FakeSecretStore("bot-token"))
    service._client = cast(WeChatClient, _FakeWeChatClient())
    service._gateway_session_service = cast(
        GatewaySessionService,
        gateway_session_service,
    )
    service._run_service = cast(RunManager, run_service)
    service._session_service = cast(
        SessionService,
        _FakeSessionService(has_active_run=has_active_run),
    )
    service._im_tool_service = cast(ImToolService, im_tool_service)
    service._im_session_command_service = cast(
        ImSessionCommandService,
        command_service,
    )
    service._inbound_queue_repo = cast(
        WeChatInboundQueueRepository,
        _FakeInboundQueueRepo(),
    )
    service._session_ingress_service = None
    service._status_lock = Lock()
    service._status_by_account = {}
    service._monitor_stop_events = {}
    service._monitor_threads = {}
    service._login_sessions = {}
    service._watched_runs = set()
    service._drain_watched_runs = set()
    return (
        service,
        gateway_session_service,
        run_service,
        im_tool_service,
        command_service,
    )


def _account() -> WeChatAccountRecord:
    return WeChatAccountRecord(
        account_id="wx-account-1",
        display_name="WeChat Account",
        base_url="https://wechat.example.test",
        cdn_base_url="https://cdn.example.test",
    )


def _text_item(text: str) -> WeChatMessageItem:
    return WeChatMessageItem.model_validate({"type": 1, "text_item": {"text": text}})


def _event(
    *,
    run_id: str,
    event_type: RunEventType,
    payload: dict[str, object],
) -> RunEvent:
    return RunEvent(
        session_id="session-1",
        run_id=run_id,
        trace_id=run_id,
        event_type=event_type,
        payload_json=json.dumps(payload, ensure_ascii=False),
        occurred_at=datetime.now(tz=timezone.utc),
    )


def test_wechat_account_update_input_rejects_empty_patch() -> None:
    with pytest.raises(ValidationError, match="update must include at least one field"):
        WeChatAccountUpdateInput()


def test_wechat_account_update_input_trims_route_tag() -> None:
    req = WeChatAccountUpdateInput(route_tag="  route-a  ")

    assert req.route_tag == "route-a"


def test_wechat_account_update_input_allows_blank_route_tag_to_clear_value() -> None:
    req = WeChatAccountUpdateInput(route_tag="   ")

    assert req.route_tag is None
    assert "route_tag" in req.model_fields_set


class _DeleteAccountRepository:
    def __init__(self, account: WeChatAccountRecord) -> None:
        self.account = account
        self.deleted_account_ids: list[str] = []

    def get_account(self, account_id: str) -> WeChatAccountRecord:
        if account_id != self.account.account_id:
            raise KeyError(account_id)
        return self.account

    def delete_account(self, account_id: str) -> None:
        self.deleted_account_ids.append(account_id)


class _DeleteSecretStore:
    def __init__(self) -> None:
        self.deleted_account_ids: list[str] = []

    def delete_bot_token(self, config_dir: Path, account_id: str) -> None:
        _ = config_dir
        self.deleted_account_ids.append(account_id)


def test_delete_account_rejects_enabled_account_without_force() -> None:
    repository = _DeleteAccountRepository(_account())
    secret_store = _DeleteSecretStore()
    service = object.__new__(WeChatGatewayService)
    service._repository = cast(WeChatAccountRepository, repository)
    service._secret_store = cast(WeChatSecretStore, secret_store)
    service._config_dir = Path("C:/config")
    service._stop_account_worker = lambda account_id: None

    with pytest.raises(
        RuntimeError,
        match="Cannot delete enabled WeChat account without force",
    ):
        service.delete_account("wx-account-1")

    service.delete_account("wx-account-1", force=True)

    assert repository.deleted_account_ids == ["wx-account-1"]
    assert secret_store.deleted_account_ids == ["wx-account-1"]
