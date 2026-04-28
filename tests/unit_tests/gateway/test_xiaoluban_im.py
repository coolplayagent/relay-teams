from __future__ import annotations

import json
import time
from pathlib import Path
from typing import cast

from pydantic import BaseModel, JsonValue
import pytest

from relay_teams.gateway.gateway_models import GatewayChannelType, GatewaySessionRecord
from relay_teams.gateway.gateway_session_service import GatewaySessionService
from relay_teams.gateway.session_ingress_service import (
    GatewaySessionIngressRequest,
    GatewaySessionIngressResult,
    GatewaySessionIngressService,
    GatewaySessionIngressStatus,
)
from relay_teams.gateway.xiaoluban import (
    format_xiaoluban_notification_text,
    XiaolubanAccountCreateInput,
    XiaolubanAccountRecord,
    XiaolubanAccountRepository,
    XiaolubanAccountUpdateInput,
    XiaolubanGatewayService,
    XiaolubanImConfig,
    XiaolubanImConfigUpdateInput,
    XiaolubanInboundMessage,
    XiaolubanSecretStore,
)
from relay_teams.gateway.xiaoluban.client import XiaolubanClient
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.sessions.runs.run_service import SessionRunService
from relay_teams.sessions.session_models import SessionRecord


class _ResolvedGatewaySessionCall(BaseModel):
    external_session_id: str
    workspace_id: str
    internal_session_id: str


def test_xiaoluban_im_config_roundtrips_without_notification_changes(
    tmp_path: Path,
) -> None:
    service = _build_service(tmp_path)
    account = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="Xiaoluban",
            token="uidself_1234567890abcdef1234567890abcdef",
            notification_workspace_ids=("notify-workspace",),
            notification_receiver="group-1",
        )
    )

    updated = service.update_im_config(
        account.account_id,
        XiaolubanImConfigUpdateInput(
            workspace_id="im-workspace",
        ),
    )
    loaded = service.get_account(account.account_id)

    assert updated.im_config.workspace_id == "im-workspace"
    assert loaded.im_config.workspace_id == "im-workspace"
    assert loaded.notification_workspace_ids == ("notify-workspace",)
    assert loaded.notification_receiver == "group-1"


def test_xiaoluban_account_create_persists_im_config_in_one_request(
    tmp_path: Path,
) -> None:
    service = _build_service(tmp_path)

    created = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="Xiaoluban",
            token="uidself_1234567890abcdef1234567890abcdef",
            im_config=XiaolubanImConfig(workspace_id="im-workspace"),
        )
    )

    loaded = service.get_account(created.account_id)

    assert created.im_config.workspace_id == "im-workspace"
    assert loaded.im_config.workspace_id == "im-workspace"


def test_xiaoluban_account_update_persists_im_config_in_one_request(
    tmp_path: Path,
) -> None:
    service = _build_service(tmp_path)
    account = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="Xiaoluban",
            token="uidself_1234567890abcdef1234567890abcdef",
        )
    )

    updated = service.update_account(
        account.account_id,
        XiaolubanAccountUpdateInput(
            display_name="Xiaoluban Updated",
            im_config=XiaolubanImConfig(workspace_id="im-workspace"),
        ),
    )
    loaded = service.get_account(account.account_id)

    assert updated.im_config.workspace_id == "im-workspace"
    assert loaded.im_config.workspace_id == "im-workspace"


def test_handle_im_inbound_starts_run_and_replies_terminal_output(
    tmp_path: Path,
) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    fake_event_log = _FakeEventLog()
    service = _build_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
        event_log=fake_event_log,
    )
    account = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="Xiaoluban",
            token="uidself_1234567890abcdef1234567890abcdef",
        )
    )
    service.update_im_config(
        account.account_id,
        XiaolubanImConfigUpdateInput(workspace_id="im-workspace"),
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="inspect this repo",
            receiver="uidself",
            sender="uidself",
            session_id="session-1",
        ),
    )

    assert fake_gateway_sessions.last_external_session_id == (
        f"xiaoluban:{account.account_id}:im-workspace:session-1"
    )
    assert fake_gateway_sessions.resolved_calls[0].workspace_id == "im-workspace"
    assert fake_ingress.requests[0].intent.intent == "inspect this repo"
    assert fake_ingress.requests[0].intent.session_id == "session-1"
    assert fake_client.keep_alive_calls == [("uidself", "session-1")]
    service._drain_im_replies()
    assert fake_client.sent_messages[-1] == (
        _formatted_xiaoluban_text(
            session_id="session-1",
            body="done from run",
            status="completed",
        ),
        "uidself",
    )
    assert service.should_suppress_xiaoluban_terminal_notification("run-1") is True


def test_handle_im_inbound_reuses_gateway_session_for_same_xiaoluban_session(
    tmp_path: Path,
) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    for text in ("first task", "second task"):
        service.handle_im_inbound(
            account_id=account.account_id,
            message=XiaolubanInboundMessage(
                content=text,
                receiver="uidself",
                sender="uidself",
                session_id="welink-session-1",
            ),
        )

    assert [
        call.external_session_id for call in fake_gateway_sessions.resolved_calls
    ] == [
        f"xiaoluban:{account.account_id}:im-workspace:welink-session-1",
        f"xiaoluban:{account.account_id}:im-workspace:welink-session-1",
    ]
    assert [call.workspace_id for call in fake_gateway_sessions.resolved_calls] == [
        "im-workspace",
        "im-workspace",
    ]
    assert [request.intent.session_id for request in fake_ingress.requests] == [
        "session-1",
        "session-1",
    ]


def test_handle_im_inbound_empty_input_sends_hint_without_run(tmp_path: Path) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="   ",
            receiver="uidself",
            sender="uidself",
            session_id="session-1",
        ),
    )

    assert fake_ingress.requests == []
    assert fake_client.sent_messages[-1] == (
        _formatted_xiaoluban_text(
            session_id="session-1",
            body="请输入任务内容，例如：帮我看一下这个项目",
            status="input_required",
        ),
        "uidself",
    )


def test_handle_im_inbound_busy_session_replies_without_second_run(
    tmp_path: Path,
) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService(active_run_id="run-active")
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="inspect this repo",
            receiver="uidself",
            sender="uidself",
            session_id="session-1",
        ),
    )

    assert fake_ingress.requests == []
    assert fake_client.sent_messages[-1] == (
        _formatted_xiaoluban_text(
            session_id="session-1",
            body="当前会话已有任务运行，请稍后再试。",
            status="busy",
        ),
        "uidself",
    )


def test_handle_im_inbound_rejected_submit_replies_busy(
    tmp_path: Path,
) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService(reject_submit=True)
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="inspect this repo",
            receiver="uidself",
            sender="uidself",
            session_id="session-1",
        ),
    )

    assert len(fake_ingress.requests) == 1
    assert fake_client.sent_messages[-1] == (
        _formatted_xiaoluban_text(
            session_id="session-1",
            body="当前会话已有任务运行，请稍后再试。",
            status="busy",
        ),
        "uidself",
    )


def _build_service(
    tmp_path: Path,
    *,
    client: _FakeXiaolubanClient | None = None,
    gateway_session_service: _FakeGatewaySessionService | None = None,
    session_ingress_service: _FakeIngressService | None = None,
    event_log: _FakeEventLog | None = None,
) -> XiaolubanGatewayService:
    return XiaolubanGatewayService(
        config_dir=tmp_path,
        repository=XiaolubanAccountRepository(tmp_path / "xiaoluban.db"),
        secret_store=cast(XiaolubanSecretStore, _FakeSecretStore()),
        client=cast(XiaolubanClient, client or _FakeXiaolubanClient()),
        workspace_lookup=_FakeWorkspaceLookup(),
        gateway_session_service=cast(
            GatewaySessionService | None,
            gateway_session_service,
        ),
        run_service=None,
        event_log=cast(EventLog | None, event_log),
        session_ingress_service=cast(
            GatewaySessionIngressService | None,
            session_ingress_service,
        ),
    )


def _build_ready_im_service(
    tmp_path: Path,
    *,
    client: _FakeXiaolubanClient,
    gateway_session_service: _FakeGatewaySessionService,
    session_ingress_service: _FakeIngressService,
) -> tuple[XiaolubanGatewayService, XiaolubanAccountRecord]:
    service = _build_service(
        tmp_path,
        client=client,
        gateway_session_service=gateway_session_service,
        session_ingress_service=session_ingress_service,
        event_log=_FakeEventLog(),
    )
    account = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="Xiaoluban",
            token="uidself_1234567890abcdef1234567890abcdef",
        )
    )
    service.update_im_config(
        account.account_id,
        XiaolubanImConfigUpdateInput(workspace_id="im-workspace"),
    )
    return service, account


def _formatted_xiaoluban_text(
    *,
    session_id: str,
    body: str,
    status: str,
) -> str:
    return format_xiaoluban_notification_text(
        workspace_id="im-workspace",
        session_id=session_id,
        status=status,
        body=body,
    )


class _FakeSecretStore:
    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}

    def get_token(self, config_dir: Path, account_id: str) -> str | None:
        _ = config_dir
        return self.tokens.get(account_id)

    def set_token(self, config_dir: Path, account_id: str, token: str | None) -> None:
        _ = config_dir
        if token is None:
            self.tokens.pop(account_id, None)
            return
        self.tokens[account_id] = token

    def delete_token(self, config_dir: Path, account_id: str) -> None:
        _ = config_dir
        self.tokens.pop(account_id, None)


class _FakeWorkspaceLookup:
    def get_workspace(self, workspace_id: str) -> object:
        if workspace_id not in {"notify-workspace", "im-workspace"}:
            raise KeyError(workspace_id)
        return object()


class _FakeXiaolubanClient:
    def __init__(self) -> None:
        self.keep_alive_calls: list[tuple[str, str]] = []
        self.sent_messages: list[tuple[str, str]] = []

    def keep_alive(
        self,
        *,
        uid: str,
        session_id: str,
        auth_token: str,
        base_url: str,
        timeout_minutes: int,
        save_info: str = "",
    ) -> None:
        _ = (auth_token, base_url, timeout_minutes, save_info)
        self.keep_alive_calls.append((uid, session_id))

    def send_text_message(
        self,
        *,
        text: str,
        receiver_uid: str,
        auth_token: str,
        base_url: str,
        sender: str | None = None,
    ) -> object:
        _ = (auth_token, base_url, sender)
        self.sent_messages.append((text, receiver_uid))
        return _FakeSendResponse()


class _FakeSendResponse:
    message_id = "msg-1"


class _FakeGatewaySessionService:
    def __init__(self) -> None:
        self.last_external_session_id = ""
        self.resolved_calls: list[_ResolvedGatewaySessionCall] = []
        self.bound_internal_calls: list[_ResolvedGatewaySessionCall] = []
        self._internal_session_ids: dict[str, str] = {}
        self.bound_runs: list[tuple[str, str | None]] = []
        self._records: dict[str, GatewaySessionRecord] = {}
        self.internal_sessions: list[SessionRecord] = []

    def resolve_or_create_session(
        self,
        *,
        channel_type: GatewayChannelType,
        external_session_id: str,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
        cwd: str | None = None,
        capabilities: dict[str, JsonValue] | None = None,
        channel_state: dict[str, JsonValue] | None = None,
        peer_user_id: str | None = None,
        peer_chat_id: str | None = None,
        **kwargs: object,
    ) -> GatewaySessionRecord:
        _ = (
            metadata,
            cwd,
            capabilities,
            channel_state,
            peer_user_id,
            peer_chat_id,
            kwargs,
        )
        self.last_external_session_id = external_session_id
        for record in self._records.values():
            if (
                record.channel_type == channel_type
                and record.external_session_id == external_session_id
            ):
                self.resolved_calls.append(
                    _ResolvedGatewaySessionCall(
                        external_session_id=external_session_id,
                        workspace_id=workspace_id,
                        internal_session_id=record.internal_session_id,
                    )
                )
                return record
        internal_session_id = self._internal_session_ids.setdefault(
            external_session_id,
            f"session-{len(self._internal_session_ids) + 1}",
        )
        self.resolved_calls.append(
            _ResolvedGatewaySessionCall(
                external_session_id=external_session_id,
                workspace_id=workspace_id,
                internal_session_id=internal_session_id,
            )
        )
        record = GatewaySessionRecord(
            gateway_session_id=f"gws-{internal_session_id}",
            channel_type=channel_type,
            external_session_id=external_session_id,
            internal_session_id=internal_session_id,
            cwd=None,
        )
        self._records[record.gateway_session_id] = record
        if not any(
            session.session_id == internal_session_id
            for session in self.internal_sessions
        ):
            self.internal_sessions.append(
                SessionRecord(
                    session_id=internal_session_id,
                    workspace_id=workspace_id,
                )
            )
        return record

    def resolve_or_bind_internal_session(
        self,
        *,
        channel_type: GatewayChannelType,
        external_session_id: str,
        internal_session_id: str,
        workspace_id: str,
        capabilities: dict[str, JsonValue] | None = None,
        channel_state: dict[str, JsonValue] | None = None,
        peer_user_id: str | None = None,
        peer_chat_id: str | None = None,
    ) -> GatewaySessionRecord:
        _ = (capabilities, channel_state, peer_user_id, peer_chat_id)
        session = next(
            (
                item
                for item in self.internal_sessions
                if item.session_id == internal_session_id
            ),
            None,
        )
        if session is None:
            raise KeyError(f"Unknown session_id: {internal_session_id}")
        if session.workspace_id != workspace_id:
            raise ValueError("internal session does not belong to workspace")
        for record in self._records.values():
            if (
                record.channel_type == channel_type
                and record.external_session_id == external_session_id
            ):
                return record
        self.bound_internal_calls.append(
            _ResolvedGatewaySessionCall(
                external_session_id=external_session_id,
                workspace_id=workspace_id,
                internal_session_id=internal_session_id,
            )
        )
        record = GatewaySessionRecord(
            gateway_session_id=f"gws-bound-{internal_session_id}",
            channel_type=channel_type,
            external_session_id=external_session_id,
            internal_session_id=internal_session_id,
            cwd=None,
        )
        self._records[record.gateway_session_id] = record
        return record

    def bind_active_run(
        self,
        gateway_session_id: str,
        run_id: str | None,
    ) -> GatewaySessionRecord:
        self.bound_runs.append((gateway_session_id, run_id))
        return GatewaySessionRecord(
            gateway_session_id=gateway_session_id,
            channel_type=GatewayChannelType.XIAOLUBAN,
            external_session_id="external-1",
            internal_session_id="session-1",
        )

    def get_session(self, gateway_session_id: str) -> GatewaySessionRecord:
        record = self._records.get(gateway_session_id)
        if record is None:
            raise KeyError(f"Unknown gateway_session_id: {gateway_session_id}")
        return record

    def get_by_internal_session_id(
        self, internal_session_id: str
    ) -> GatewaySessionRecord | None:
        for record in self._records.values():
            if record.internal_session_id == internal_session_id:
                return record
        return None

    def list_all(self) -> tuple[GatewaySessionRecord, ...]:
        return tuple(self._records.values())

    def list_internal_by_workspace(
        self, workspace_id: str
    ) -> tuple[SessionRecord, ...]:
        return tuple(
            session
            for session in self.internal_sessions
            if session.workspace_id == workspace_id
        )


class _FakeIngressService:
    def __init__(
        self,
        active_run_id: str | None = None,
        *,
        reject_submit: bool = False,
    ) -> None:
        self.requests: list[GatewaySessionIngressRequest] = []
        self._active_run_id = active_run_id
        self._reject_submit = reject_submit

    def active_run_id(self, session_id: str) -> str | None:
        _ = session_id
        return self._active_run_id

    def submit(
        self,
        request: GatewaySessionIngressRequest,
    ) -> GatewaySessionIngressResult:
        self.requests.append(request)
        if self._reject_submit:
            return GatewaySessionIngressResult(
                status=GatewaySessionIngressStatus.REJECTED,
                session_id=request.intent.session_id,
                blocking_run_id="run-active",
            )
        return GatewaySessionIngressResult(
            status=GatewaySessionIngressStatus.STARTED,
            session_id=request.intent.session_id,
            run_id="run-1",
        )


def test_handle_im_inbound_fallback_session_id_without_session_id_param(
    tmp_path: Path,
) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="inspect this repo",
            receiver="recv_uid",
            sender="send_uid",
            session_id="",
        ),
    )

    assert fake_gateway_sessions.last_external_session_id == (
        f"xiaoluban:{account.account_id}:im-workspace:send_uid:recv_uid"
    )


def test_update_im_config_requires_workspace_id(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    account = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="Xiaoluban",
            token="uidself_1234567890abcdef1234567890abcdef",
        )
    )

    with pytest.raises(ValueError, match="workspace_id is required for Xiaoluban IM"):
        service.update_im_config(
            account.account_id,
            XiaolubanImConfigUpdateInput(workspace_id=None),
        )


def test_validate_im_workspace_rejects_unknown(tmp_path: Path) -> None:
    service = _build_service(tmp_path)

    with pytest.raises(ValueError, match="Unknown IM workspace"):
        service._validate_im_workspace("nonexistent-workspace")


def test_get_im_callback_auth_token_success(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    account = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="Xiaoluban",
            token="uidself_1234567890abcdef1234567890abcdef",
        )
    )

    token = service.get_im_callback_auth_token(account.account_id)
    assert token == "uidself_1234567890abcdef1234567890abcdef"


def test_get_im_callback_auth_token_missing_token(tmp_path: Path) -> None:
    fake_store = _FakeSecretStore()
    service = XiaolubanGatewayService(
        config_dir=tmp_path,
        repository=XiaolubanAccountRepository(tmp_path / "xiaoluban.db"),
        secret_store=cast(XiaolubanSecretStore, fake_store),
        client=cast(XiaolubanClient, _FakeXiaolubanClient()),
    )
    account = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="Xiaoluban",
            token="uidself_1234567890abcdef1234567890abcdef",
        )
    )
    fake_store.tokens.clear()

    with pytest.raises(RuntimeError, match="missing_xiaoluban_token"):
        service.get_im_callback_auth_token(account.account_id)


def test_get_im_callback_auth_token_disabled_account(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    account = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="Xiaoluban",
            token="uidself_1234567890abcdef1234567890abcdef",
        )
    )
    service.set_account_enabled(account.account_id, False)

    with pytest.raises(RuntimeError, match="xiaoluban_account_disabled"):
        service.get_im_callback_auth_token(account.account_id)


def test_should_suppress_empty_run_id(tmp_path: Path) -> None:
    service = _build_service(tmp_path)

    assert service.should_suppress_xiaoluban_terminal_notification("") is False
    assert service.should_suppress_xiaoluban_terminal_notification(None) is False


def test_handle_im_inbound_missing_gateway_session_service(tmp_path: Path) -> None:
    service = XiaolubanGatewayService(
        config_dir=tmp_path,
        repository=XiaolubanAccountRepository(tmp_path / "xiaoluban.db"),
        secret_store=cast(XiaolubanSecretStore, _FakeSecretStore()),
        client=cast(XiaolubanClient, _FakeXiaolubanClient()),
        gateway_session_service=None,
        session_ingress_service=cast(
            GatewaySessionIngressService, _FakeIngressService()
        ),
    )
    account = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="Xiaoluban",
            token="uidself_1234567890abcdef1234567890abcdef",
        )
    )
    service.update_im_config(
        account.account_id,
        XiaolubanImConfigUpdateInput(workspace_id="im-workspace"),
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="hello",
            receiver="uidself",
            sender="uidself",
            session_id="session-1",
        ),
    )


def test_handle_im_inbound_missing_run_and_ingress(tmp_path: Path) -> None:
    fake_gateway_sessions = _FakeGatewaySessionService()
    service = XiaolubanGatewayService(
        config_dir=tmp_path,
        repository=XiaolubanAccountRepository(tmp_path / "xiaoluban.db"),
        secret_store=cast(XiaolubanSecretStore, _FakeSecretStore()),
        client=cast(XiaolubanClient, _FakeXiaolubanClient()),
        workspace_lookup=_FakeWorkspaceLookup(),
        gateway_session_service=cast(GatewaySessionService, fake_gateway_sessions),
        run_service=None,
        session_ingress_service=None,
    )
    account = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="Xiaoluban",
            token="uidself_1234567890abcdef1234567890abcdef",
        )
    )
    service.update_im_config(
        account.account_id,
        XiaolubanImConfigUpdateInput(workspace_id="im-workspace"),
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="hello",
            receiver="uidself",
            sender="uidself",
            session_id="session-1",
        ),
    )


def test_handle_im_inbound_missing_workspace_id(tmp_path: Path) -> None:
    fake_gateway_sessions = _FakeGatewaySessionService()
    service = XiaolubanGatewayService(
        config_dir=tmp_path,
        repository=XiaolubanAccountRepository(tmp_path / "xiaoluban.db"),
        secret_store=cast(XiaolubanSecretStore, _FakeSecretStore()),
        client=cast(XiaolubanClient, _FakeXiaolubanClient()),
        workspace_lookup=_FakeWorkspaceLookup(),
        gateway_session_service=cast(GatewaySessionService, fake_gateway_sessions),
        session_ingress_service=cast(
            GatewaySessionIngressService, _FakeIngressService()
        ),
    )
    account = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="Xiaoluban",
            token="uidself_1234567890abcdef1234567890abcdef",
        )
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="hello",
            receiver="uidself",
            sender="uidself",
            session_id="session-1",
        ),
    )


def test_handle_im_inbound_missing_token(tmp_path: Path) -> None:
    fake_store = _FakeSecretStore()
    fake_gateway_sessions = _FakeGatewaySessionService()
    service = XiaolubanGatewayService(
        config_dir=tmp_path,
        repository=XiaolubanAccountRepository(tmp_path / "xiaoluban.db"),
        secret_store=cast(XiaolubanSecretStore, fake_store),
        client=cast(XiaolubanClient, _FakeXiaolubanClient()),
        workspace_lookup=_FakeWorkspaceLookup(),
        gateway_session_service=cast(GatewaySessionService, fake_gateway_sessions),
        session_ingress_service=cast(
            GatewaySessionIngressService, _FakeIngressService()
        ),
    )
    account = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="Xiaoluban",
            token="uidself_1234567890abcdef1234567890abcdef",
        )
    )
    service.update_im_config(
        account.account_id,
        XiaolubanImConfigUpdateInput(workspace_id="im-workspace"),
    )
    fake_store.tokens.clear()

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="hello",
            receiver="uidself",
            sender="uidself",
            session_id="session-1",
        ),
    )


def test_start_im_run_via_run_service(tmp_path: Path) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    event_log = _FakeEventLog()
    run_service = _FakeRunService()
    service = XiaolubanGatewayService(
        config_dir=tmp_path,
        repository=XiaolubanAccountRepository(tmp_path / "xiaoluban.db"),
        secret_store=cast(XiaolubanSecretStore, _FakeSecretStore()),
        client=cast(XiaolubanClient, fake_client),
        workspace_lookup=_FakeWorkspaceLookup(),
        gateway_session_service=cast(GatewaySessionService, fake_gateway_sessions),
        run_service=cast(SessionRunService, run_service),
        event_log=cast(EventLog, event_log),
        session_ingress_service=None,
    )
    account = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="Xiaoluban",
            token="uidself_1234567890abcdef1234567890abcdef",
        )
    )
    service.update_im_config(
        account.account_id,
        XiaolubanImConfigUpdateInput(workspace_id="im-workspace"),
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="hello",
            receiver="uidself",
            sender="uidself",
            session_id="session-1",
        ),
    )

    assert run_service.create_run_calls == 1
    assert run_service.ensure_started_calls == 1


def test_active_run_id_no_ingress_service(tmp_path: Path) -> None:
    service = _build_service(tmp_path)

    assert service._active_run_id("session-1") is None


def test_terminal_text_no_event_log(tmp_path: Path) -> None:
    service = _build_service(tmp_path)

    assert service._terminal_text_for_run("run-1") == ""


def test_terminal_text_invalid_event_type(tmp_path: Path) -> None:
    event_log = _FakeEventLog(event_type="invalid_type")
    service = _build_service(tmp_path, event_log=event_log)

    assert service._terminal_text_for_run("run-1") == ""


def test_terminal_text_non_terminal_event(tmp_path: Path) -> None:
    event_log = _FakeEventLog(event_type=RunEventType.RUN_STARTED.value)
    service = _build_service(tmp_path, event_log=event_log)

    assert service._terminal_text_for_run("run-1") == ""


def test_terminal_text_run_completed_empty_output(tmp_path: Path) -> None:
    event_log = _FakeEventLog(
        event_type=RunEventType.RUN_COMPLETED.value,
        payload={"output": ""},
    )
    service = _build_service(tmp_path, event_log=event_log)

    assert service._terminal_text_for_run("run-1") == "任务已完成。"


def test_terminal_text_run_failed_with_output(tmp_path: Path) -> None:
    event_log = _FakeEventLog(
        event_type=RunEventType.RUN_FAILED.value,
        payload={"output": "partial output"},
    )
    service = _build_service(tmp_path, event_log=event_log)

    assert service._terminal_text_for_run("run-1") == "partial output"


def test_terminal_text_run_failed_with_error(tmp_path: Path) -> None:
    event_log = _FakeEventLog(
        event_type=RunEventType.RUN_FAILED.value,
        payload={"error": "something went wrong"},
    )
    service = _build_service(tmp_path, event_log=event_log)

    assert service._terminal_text_for_run("run-1") == "任务失败：something went wrong"


def test_terminal_text_run_stopped_fallback(tmp_path: Path) -> None:
    event_log = _FakeEventLog(
        event_type=RunEventType.RUN_STOPPED.value,
        payload={},
    )
    service = _build_service(tmp_path, event_log=event_log)

    assert service._terminal_text_for_run("run-1") == "任务未完成。"


def test_mark_im_terminal_suppression_empty_run_id(tmp_path: Path) -> None:
    service = _build_service(tmp_path)

    service._mark_im_terminal_notification_suppressed("")
    assert service.should_suppress_xiaoluban_terminal_notification("") is False


def test_cleanup_im_terminal_suppression_removes_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _build_service(tmp_path)
    service._mark_im_terminal_notification_suppressed("run-1")

    assert service.should_suppress_xiaoluban_terminal_notification("run-1") is True

    future_time = time.monotonic() + 25 * 60 * 60
    monkeypatch.setattr(time, "monotonic", lambda: future_time)

    assert service.should_suppress_xiaoluban_terminal_notification("run-1") is False


def test_resolve_im_config_requires_workspace_id(tmp_path: Path) -> None:
    service = _build_service(tmp_path)

    with pytest.raises(ValueError, match="workspace_id is required for Xiaoluban IM"):
        service._resolve_im_config(None, requires_workspace_id=True)

    with pytest.raises(ValueError, match="workspace_id is required for Xiaoluban IM"):
        service._resolve_im_config(
            XiaolubanImConfig(workspace_id=None), requires_workspace_id=True
        )


def test_resolve_im_config_default_without_workspace_id(
    tmp_path: Path,
) -> None:
    service = _build_service(tmp_path)

    result = service._resolve_im_config(None, requires_workspace_id=False)

    assert result.workspace_id is None


def test_start_im_run_via_create_detached_run(tmp_path: Path) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    event_log = _FakeEventLog()
    run_service = _FakeRunServiceWithDetached()
    service = XiaolubanGatewayService(
        config_dir=tmp_path,
        repository=XiaolubanAccountRepository(tmp_path / "xiaoluban.db"),
        secret_store=cast(XiaolubanSecretStore, _FakeSecretStore()),
        client=cast(XiaolubanClient, fake_client),
        workspace_lookup=_FakeWorkspaceLookup(),
        gateway_session_service=cast(GatewaySessionService, fake_gateway_sessions),
        run_service=cast(SessionRunService, run_service),
        event_log=cast(EventLog, event_log),
        session_ingress_service=None,
    )
    account = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="Xiaoluban",
            token="uidself_1234567890abcdef1234567890abcdef",
        )
    )
    service.update_im_config(
        account.account_id,
        XiaolubanImConfigUpdateInput(workspace_id="im-workspace"),
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="hello",
            receiver="uidself",
            sender="uidself",
            session_id="session-1",
        ),
    )

    assert run_service.detached_run_calls == 1


# ---------------------------------------------------------------------------
# 命令处理 & 会话切换 测试
# ---------------------------------------------------------------------------


def test_command_new_creates_session_and_sends_reply(tmp_path: Path) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="/new",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    assert fake_ingress.requests == []
    assert fake_client.sent_messages[-1][1] == "uidself"
    assert "已创建新会话" in fake_client.sent_messages[-1][0]
    resolved_ids = [c.external_session_id for c in fake_gateway_sessions.resolved_calls]
    assert len(resolved_ids) == 1
    base_id = f"xiaoluban:{account.account_id}:im-workspace:welink-session-1"
    assert resolved_ids[0].startswith(base_id + ":")


def test_command_new_with_task_creates_session_and_starts_run(
    tmp_path: Path,
) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="/new 帮我看一下这个项目",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    assert len(fake_ingress.requests) == 1
    assert fake_ingress.requests[0].intent.intent == "帮我看一下这个项目"
    assert len(fake_client.sent_messages) >= 1
    assert "处理中" in fake_client.sent_messages[0][0]
    resolved_ids = [c.external_session_id for c in fake_gateway_sessions.resolved_calls]
    base_id = f"xiaoluban:{account.account_id}:im-workspace:welink-session-1"
    assert resolved_ids[0].startswith(base_id + ":")


def test_command_new_routes_subsequent_messages(tmp_path: Path) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="/new",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )
    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="继续做任务",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    resolved_ids = [c.external_session_id for c in fake_gateway_sessions.resolved_calls]
    assert len(resolved_ids) == 2
    base_id = f"xiaoluban:{account.account_id}:im-workspace:welink-session-1"
    new_session_id = resolved_ids[0]
    assert new_session_id.startswith(base_id + ":")
    assert resolved_ids[1] == new_session_id
    assert fake_ingress.requests[0].intent.intent == "继续做任务"


def test_command_resume_lists_sessions(tmp_path: Path) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="/new",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="/resume",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    list_message = fake_client.sent_messages[-1][0]
    assert "会话列表" in list_message
    assert "/resume" in list_message


def test_command_resume_by_session_id_switches_session(
    tmp_path: Path,
) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="/new",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )
    new_call = fake_gateway_sessions.resolved_calls[0]
    target_internal_id = new_call.internal_session_id

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content=f"/resume {target_internal_id}",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    switch_reply = fake_client.sent_messages[-1][0]
    assert "已切换到会话" in switch_reply

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="在旧会话继续",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )
    assert fake_ingress.requests[0].intent.session_id == target_internal_id


def test_command_resume_by_index_switches_session(tmp_path: Path) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="/new",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="/resume 1",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    assert "已切换到会话" in fake_client.sent_messages[-1][0]


def test_command_resume_binds_existing_internal_session(
    tmp_path: Path,
) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_gateway_sessions.internal_sessions.append(
        SessionRecord(
            session_id="session-existing",
            workspace_id="im-workspace",
            metadata={"title": "Existing session"},
        )
    )
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="/resume session-existing",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )
    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="继续这个会话",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    assert fake_gateway_sessions.bound_internal_calls == [
        _ResolvedGatewaySessionCall(
            external_session_id=(
                f"xiaoluban:{account.account_id}:im-workspace:internal:session-existing"
            ),
            workspace_id="im-workspace",
            internal_session_id="session-existing",
        )
    ]
    assert fake_ingress.requests[0].intent.session_id == "session-existing"


def test_command_resume_ignores_stale_gateway_session(
    tmp_path: Path,
) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )
    fake_gateway_sessions._records["gws-stale"] = GatewaySessionRecord(
        gateway_session_id="gws-stale",
        channel_type=GatewayChannelType.XIAOLUBAN,
        external_session_id=(
            f"xiaoluban:{account.account_id}:im-workspace:internal:session-stale"
        ),
        internal_session_id="session-stale",
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="/resume session-stale",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    assert "未找到会话" in fake_client.sent_messages[-1][0]
    assert fake_gateway_sessions.bound_internal_calls == []


def test_command_resume_rejects_stale_existing_gateway_binding(
    tmp_path: Path,
) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )
    fake_gateway_sessions._records["gws-stale"] = GatewaySessionRecord(
        gateway_session_id="gws-stale",
        channel_type=GatewayChannelType.XIAOLUBAN,
        external_session_id=(
            f"xiaoluban:{account.account_id}:im-workspace:internal:session-stale"
        ),
        internal_session_id="session-stale",
    )

    result = service._ensure_gateway_session_for_internal_id(
        "session-stale",
        account_id=account.account_id,
        workspace_id="im-workspace",
        message=XiaolubanInboundMessage(
            content="/resume session-stale",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    assert result is None


def test_command_resume_invalid_session_shows_error(
    tmp_path: Path,
) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="/resume nonexistent",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    assert "未找到会话" in fake_client.sent_messages[-1][0]


def test_command_help_shows_help_text(tmp_path: Path) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="/help",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    help_message = fake_client.sent_messages[-1][0]
    assert "/new" in help_message
    assert "/resume" in help_message
    assert "/help" in help_message


def test_normal_message_sends_ack_after_run_submit(tmp_path: Path) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="帮我分析一下",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    assert len(fake_client.sent_messages) >= 1
    ack_message = fake_client.sent_messages[0][0]
    assert "处理中" in ack_message
    assert len(fake_ingress.requests) == 1


def test_submit_rejection_sends_busy_without_processing_ack(tmp_path: Path) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService(reject_submit=True)
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="帮我分析一下",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    assert len(fake_ingress.requests) == 1
    assert len(fake_client.sent_messages) == 1
    assert "当前会话已有任务运行" in fake_client.sent_messages[-1][0]
    assert "处理中" not in fake_client.sent_messages[-1][0]


def test_non_slash_text_not_treated_as_command(tmp_path: Path) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="用 /new 语法 ...",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    assert len(fake_ingress.requests) == 1
    assert fake_ingress.requests[0].intent.intent == "用 /new 语法 ..."
    base_id = f"xiaoluban:{account.account_id}:im-workspace:welink-session-1"
    assert fake_gateway_sessions.resolved_calls[0].external_session_id == base_id


def test_command_new_without_platform_session_id_isolated_by_peer(
    tmp_path: Path,
) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="/new",
            receiver="recv_uid",
            sender="sender-a",
            session_id="",
        ),
    )
    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="sender b task",
            receiver="recv_uid",
            sender="sender-b",
            session_id="",
        ),
    )

    base_a = f"xiaoluban:{account.account_id}:im-workspace:sender-a:recv_uid"
    base_b = f"xiaoluban:{account.account_id}:im-workspace:sender-b:recv_uid"
    assert fake_gateway_sessions.resolved_calls[0].external_session_id.startswith(
        base_a + ":"
    )
    assert fake_gateway_sessions.resolved_calls[1].external_session_id == base_b
    assert fake_ingress.requests[0].intent.intent == "sender b task"


def test_command_resume_without_existing_sessions(
    tmp_path: Path,
) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="/resume",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    assert "会话列表" in fake_client.sent_messages[-1][0]


def test_command_resume_by_prefix_match(tmp_path: Path) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="/new",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )
    target_internal_id = fake_gateway_sessions.resolved_calls[0].internal_session_id
    prefix = target_internal_id[:6]

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content=f"/resume {prefix}",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    assert "已切换到会话" in fake_client.sent_messages[-1][0]


def test_handle_im_inbound_unknown_command_routes_as_task(
    tmp_path: Path,
) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService()
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="/some-unknown-command 参数",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    assert len(fake_ingress.requests) == 1
    assert fake_ingress.requests[0].intent.intent == "/some-unknown-command 参数"


def test_handle_im_inbound_busy_does_not_send_ack(tmp_path: Path) -> None:
    fake_client = _FakeXiaolubanClient()
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService(active_run_id="run-active")
    service, account = _build_ready_im_service(
        tmp_path,
        client=fake_client,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
    )

    service.handle_im_inbound(
        account_id=account.account_id,
        message=XiaolubanInboundMessage(
            content="帮我分析一下",
            receiver="uidself",
            sender="uidself",
            session_id="welink-session-1",
        ),
    )

    assert len(fake_client.sent_messages) == 1
    assert (
        "繁忙" in fake_client.sent_messages[-1][0]
        or "任务" in fake_client.sent_messages[-1][0]
    )
    assert "处理中" not in fake_client.sent_messages[-1][0]


class _FakeRunService:
    def __init__(self) -> None:
        self.create_run_calls = 0
        self.ensure_started_calls = 0

    def create_run(self, intent: IntentInput) -> tuple[str, str]:
        _ = intent
        self.create_run_calls += 1
        return ("run-1", "session-1")

    def ensure_run_started(self, run_id: str) -> None:
        _ = run_id
        self.ensure_started_calls += 1


class _FakeRunServiceWithDetached(_FakeRunService):
    def __init__(self) -> None:
        super().__init__()
        self.detached_run_calls = 0

    def create_detached_run(self, intent: IntentInput) -> tuple[str, str]:
        _ = intent
        self.detached_run_calls += 1
        return ("run-detached", "session-1")


class _FakeEventLog:
    def __init__(
        self,
        event_type: str | None = None,
        payload: dict[str, str] | None = None,
    ) -> None:
        self._event_type = event_type
        self._payload = payload

    def list_by_trace_with_ids(self, trace_id: str) -> tuple[dict[str, JsonValue], ...]:
        _ = trace_id
        if self._event_type is None:
            return (
                {
                    "id": 1,
                    "event_type": RunEventType.RUN_COMPLETED.value,
                    "payload_json": json.dumps({"output": "done from run"}),
                },
            )
        return (
            {
                "id": 1,
                "event_type": self._event_type,
                "payload_json": json.dumps(self._payload or {}),
            },
        )

    def _terminal_text_result(self) -> str:
        from relay_teams.sessions.runs.terminal_payload import (
            extract_terminal_error,
            extract_terminal_output,
            parse_terminal_payload_json,
        )

        record = {
            "id": 1,
            "event_type": self._event_type,
            "payload_json": json.dumps(self._payload or {}),
        }
        event_type = RunEventType(str(record["event_type"]))
        payload = parse_terminal_payload_json(record["payload_json"])
        if event_type == RunEventType.RUN_COMPLETED:
            output = extract_terminal_output(payload).strip()
            return output or "任务已完成。"
        output = extract_terminal_output(payload).strip()
        if output:
            return output
        error = extract_terminal_error(payload).strip()
        if error:
            return f"任务失败：{error}"
        return "任务未完成。"
