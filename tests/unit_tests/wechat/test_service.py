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

from pydantic import JsonValue

from agent_teams.gateway.gateway_models import GatewayChannelType, GatewaySessionRecord
from agent_teams.gateway.gateway_session_service import GatewaySessionService
from agent_teams.gateway.im import ImSessionCommandService, ImToolService
from agent_teams.gateway.wechat.account_repository import WeChatAccountRepository
from agent_teams.gateway.wechat.client import WeChatClient
from agent_teams.gateway.wechat.models import (
    WeChatAccountRecord,
    WeChatInboundMessage,
    WeChatMessageItem,
)
from agent_teams.gateway.wechat.secret_store import WeChatSecretStore
from agent_teams.gateway.wechat.service import WeChatGatewayService
from agent_teams.media import content_parts_from_text
from agent_teams.sessions import SessionService
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.run_manager import RunManager
from agent_teams.sessions.runs.run_models import RunEvent, RunResult
from agent_teams.sessions.session_models import SessionMode

_RECEIPT_CREATED = "\u6536\u5230\uff0c\u6b63\u5728\u5904\u7406\u3002"
_RECEIPT_JOINED = (
    "\u6536\u5230\uff0c\u5df2\u52a0\u5165\u5f53\u524d\u4f1a\u8bdd\u5904\u7406\u3002"
)


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


def test_handle_message_uses_joined_receipt_when_run_already_active() -> None:
    service, _, run_service, im_tool_service, _ = _build_service(
        events=(),
        has_active_run=True,
    )
    service._watched_runs.add("run-created")

    service._handle_message(
        _account(),
        "bot-token",
        WeChatInboundMessage(
            from_user_id="wx-peer-1",
            item_list=(_text_item("follow up"),),
        ),
    )

    assert im_tool_service.send_text_calls[0]["text"] == _RECEIPT_JOINED
    assert len(run_service.created_intents) == 1


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
    service._status_lock = Lock()
    service._status_by_account = {}
    service._monitor_stop_events = {}
    service._monitor_threads = {}
    service._login_sessions = {}
    service._watched_runs = set()
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
