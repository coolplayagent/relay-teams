from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import cast

import pytest

from agent_teams.gateway.gateway_session_service import GatewaySessionService
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.run_manager import RunManager
from agent_teams.sessions.runs.run_models import RunEvent
from agent_teams.wechat.account_repository import WeChatAccountRepository
from agent_teams.wechat.client import WeChatClient
from agent_teams.wechat.models import WeChatAccountRecord
from agent_teams.wechat.secret_store import WeChatSecretStore
from agent_teams.wechat.service import WeChatGatewayService


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
    result = WeChatGatewayService._normalize_qr_code_url("PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPjwvc3ZnPg==")

    assert result == (
        "data:image/svg+xml;base64,"
        "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPjwvc3ZnPg=="
    )


@pytest.mark.asyncio
async def test_await_terminal_and_reply_records_success() -> None:
    service, gateway_session_service, client = _build_service(
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

    assert client.send_text_calls == [
        {
            "account_id": "wx-account-1",
            "token": "bot-token",
            "to_user_id": "wx-peer-1",
            "text": "Reply sent",
            "context_token": "ctx-1",
        }
    ]
    assert len(gateway_session_service.update_calls) == 1
    update_call = gateway_session_service.update_calls[0]
    assert update_call["gateway_session_id"] == "gws-1"
    assert update_call["peer_user_id"] == "wx-peer-1"
    assert update_call["peer_chat_id"] == "wx-peer-1"
    channel_state = update_call["channel_state"]
    assert isinstance(channel_state, dict)
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
    service, gateway_session_service, _ = _build_service(
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


def test_handle_reply_future_records_cancelled_future() -> None:
    service, gateway_session_service, _ = _build_service(events=())
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
    def __init__(self, send_text_error: Exception | None = None) -> None:
        self._send_text_error = send_text_error
        self.send_text_calls: list[dict[str, str | None]] = []

    def send_text_message(
        self,
        *,
        account: WeChatAccountRecord,
        token: str,
        to_user_id: str,
        text: str,
        context_token: str | None,
    ) -> None:
        self.send_text_calls.append(
            {
                "account_id": account.account_id,
                "token": token,
                "to_user_id": to_user_id,
                "text": text,
                "context_token": context_token,
            }
        )
        if self._send_text_error is not None:
            raise self._send_text_error


class _FakeGatewaySessionService:
    def __init__(self) -> None:
        self.bind_calls: list[tuple[str, str | None]] = []
        self.update_calls: list[dict[str, object]] = []

    def bind_active_run(
        self,
        gateway_session_id: str,
        run_id: str | None,
    ) -> None:
        self.bind_calls.append((gateway_session_id, run_id))

    def update_channel_state(
        self,
        gateway_session_id: str,
        *,
        channel_state: dict[str, object],
        peer_user_id: str | None = None,
        peer_chat_id: str | None = None,
    ) -> None:
        self.update_calls.append(
            {
                "gateway_session_id": gateway_session_id,
                "channel_state": channel_state,
                "peer_user_id": peer_user_id,
                "peer_chat_id": peer_chat_id,
            }
        )


class _FakeRunService:
    def __init__(self, events: tuple[RunEvent, ...]) -> None:
        self._events = events

    async def stream_run_events(self, run_id: str) -> AsyncIterator[RunEvent]:
        for event in self._events:
            assert event.run_id == run_id
            yield event


def _build_service(
    *,
    events: tuple[RunEvent, ...],
    send_text_error: Exception | None = None,
) -> tuple[WeChatGatewayService, _FakeGatewaySessionService, _FakeWeChatClient]:
    account = WeChatAccountRecord(
        account_id="wx-account-1",
        display_name="WeChat Account",
        base_url="https://wechat.example.test",
        cdn_base_url="https://cdn.example.test",
    )
    gateway_session_service = _FakeGatewaySessionService()
    client = _FakeWeChatClient(send_text_error=send_text_error)

    service = object.__new__(WeChatGatewayService)
    service._config_dir = Path("C:/config")
    service._repository = cast(WeChatAccountRepository, _FakeRepository(account))
    service._secret_store = cast(WeChatSecretStore, _FakeSecretStore("bot-token"))
    service._client = cast(WeChatClient, client)
    service._gateway_session_service = cast(
        GatewaySessionService,
        gateway_session_service,
    )
    service._run_service = cast(RunManager, _FakeRunService(events))
    service._status_lock = Lock()
    service._status_by_account = {}
    service._watched_runs = set()
    return service, gateway_session_service, client


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
