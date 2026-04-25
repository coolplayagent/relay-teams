from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from types import TracebackType

import httpx
import pytest

import relay_teams.gateway.xiaoluban.client as xiaoluban_client_module
from relay_teams.gateway.xiaoluban import (
    DEFAULT_XIAOLUBAN_BASE_URL,
    XiaolubanAccountCreateInput,
    XiaolubanAccountRecord,
    XiaolubanAccountRepository,
    XiaolubanAccountStatus,
    XiaolubanClient,
    XiaolubanSecretStore,
    XiaolubanAccountUpdateInput,
    XiaolubanGatewayService,
    XiaolubanSendTextResponse,
    derive_uid_from_token,
)
from relay_teams.secrets import AppSecretStore


class _FakeXiaolubanSecretStore(XiaolubanSecretStore):
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


class _FakeXiaolubanClient(XiaolubanClient):
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def send_text_message(
        self,
        *,
        text: str,
        receiver_uid: str,
        auth_token: str,
        base_url: str = "",
        sender: str | None = None,
    ) -> XiaolubanSendTextResponse:
        self.calls.append(
            {
                "text": text,
                "receiver_uid": receiver_uid,
                "auth_token": auth_token,
                "base_url": base_url,
                "sender": str(sender or ""),
            }
        )
        return XiaolubanSendTextResponse(message_id="xlbmsg_1")


class _FakeWorkspaceLookup:
    def __init__(self, workspace_ids: set[str]) -> None:
        self.workspace_ids = workspace_ids

    def get_workspace(self, workspace_id: str) -> object:
        if workspace_id not in self.workspace_ids:
            raise KeyError(workspace_id)
        return object()


class _FakeHttpClient:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def __enter__(self) -> _FakeHttpClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        _ = (exc_type, exc, traceback)
        return False

    def post(
        self,
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
    ) -> httpx.Response:
        self.calls.append(
            {
                "url": url,
                "content": content,
                "headers": headers,
            }
        )
        return self.response


class _FailingHttpClient:
    def __enter__(self) -> _FailingHttpClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        _ = (exc_type, exc, traceback)
        return False

    def post(
        self,
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
    ) -> httpx.Response:
        _ = (content, headers)
        request = httpx.Request("POST", url)
        raise httpx.ConnectError("connection reset", request=request)


class _FakeAppSecretStore(AppSecretStore):
    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str], str] = {}
        self.deleted: list[tuple[str, str, str]] = []

    def get_secret(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
        field_name: str,
    ) -> str | None:
        _ = config_dir
        return self.values.get((namespace, owner_id, field_name))

    def set_secret(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
        field_name: str,
        value: str | None,
    ) -> None:
        _ = config_dir
        key = (namespace, owner_id, field_name)
        if value is None:
            self.values.pop(key, None)
            return
        self.values[key] = value

    def delete_secret(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
        field_name: str,
    ) -> None:
        _ = config_dir
        self.deleted.append((namespace, owner_id, field_name))
        self.values.pop((namespace, owner_id, field_name), None)


def test_create_account_persists_token_and_derived_uid(tmp_path: Path) -> None:
    service, secret_store, _client = _build_service(tmp_path)

    record = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="小鲁班主账号",
            token="uid_1234567890abcdef1234567890abcdef",
            base_url="http://xlb.test/send",
        )
    )

    assert record.account_id.startswith("xlb_")
    assert record.status == XiaolubanAccountStatus.ENABLED
    assert record.derived_uid == "uid"
    assert record.notification_workspace_ids == ()
    assert record.notification_receiver is None
    assert record.secret_status.token_configured is True
    assert (
        secret_store.tokens[record.account_id] == "uid_1234567890abcdef1234567890abcdef"
    )


def test_update_account_replaces_token_and_derived_uid(tmp_path: Path) -> None:
    service, secret_store, _client = _build_service(tmp_path)
    created = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="小鲁班主账号",
            token="uid_1234567890abcdef1234567890abcdef",
        )
    )

    updated = service.update_account(
        created.account_id,
        XiaolubanAccountUpdateInput(
            display_name="小鲁班新账号",
            token="user2_abcdef1234567890abcdef1234567890",
            enabled=False,
            notification_workspace_ids=("workspace-1", "workspace-2"),
            notification_receiver="group-123",
        ),
    )

    assert updated.display_name == "小鲁班新账号"
    assert updated.status == XiaolubanAccountStatus.DISABLED
    assert updated.derived_uid == "user2"
    assert updated.notification_workspace_ids == ("workspace-1", "workspace-2")
    assert updated.notification_receiver == "group-123"
    assert (
        secret_store.tokens[created.account_id]
        == "user2_abcdef1234567890abcdef1234567890"
    )


def test_update_account_skips_stale_workspace_validation_for_unrelated_patch(
    tmp_path: Path,
) -> None:
    workspace_lookup = _FakeWorkspaceLookup({"workspace-1"})
    service, _secret_store, _client = _build_service(
        tmp_path,
        workspace_lookup=workspace_lookup,
    )
    created = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="小鲁班主账号",
            token="uid_1234567890abcdef1234567890abcdef",
            notification_workspace_ids=("workspace-1",),
        )
    )
    workspace_lookup.workspace_ids.clear()

    updated = service.update_account(
        created.account_id,
        XiaolubanAccountUpdateInput(display_name="小鲁班新账号"),
    )

    assert updated.display_name == "小鲁班新账号"
    assert updated.notification_workspace_ids == ("workspace-1",)


def test_update_account_rejects_unknown_workspace_on_explicit_patch(
    tmp_path: Path,
) -> None:
    service, _secret_store, _client = _build_service(
        tmp_path,
        workspace_lookup=_FakeWorkspaceLookup(set()),
    )
    created = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="小鲁班主账号",
            token="uid_1234567890abcdef1234567890abcdef",
        )
    )

    with pytest.raises(ValueError, match="Unknown notification workspace"):
        _ = service.update_account(
            created.account_id,
            XiaolubanAccountUpdateInput(
                notification_workspace_ids=("missing-workspace",)
            ),
        )


def test_list_and_get_accounts_include_secret_status(tmp_path: Path) -> None:
    service, _secret_store, _client = _build_service(tmp_path)
    created = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="小鲁班主账号",
            token="uid_1234567890abcdef1234567890abcdef",
        )
    )

    listed = service.list_accounts()
    loaded = service.get_account(created.account_id)

    assert [account.account_id for account in listed] == [created.account_id]
    assert loaded.secret_status.token_configured is True


def test_send_text_message_uses_persisted_token_and_default_uid(tmp_path: Path) -> None:
    service, _secret_store, client = _build_service(tmp_path)
    created = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="小鲁班主账号",
            token="uid_1234567890abcdef1234567890abcdef",
            base_url="http://xlb.test/send",
        )
    )

    message_id = service.send_text_message(
        account_id=created.account_id,
        text="started",
    )

    assert message_id == "xlbmsg_1"
    assert client.calls == [
        {
            "text": "started",
            "receiver_uid": "uid",
            "auth_token": "uid_1234567890abcdef1234567890abcdef",
            "base_url": "http://xlb.test/send",
            "sender": "",
        }
    ]


def test_send_text_message_uses_explicit_receiver_uid(tmp_path: Path) -> None:
    service, _secret_store, client = _build_service(tmp_path)
    created = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="小鲁班主账号",
            token="uid_1234567890abcdef1234567890abcdef",
        )
    )

    _ = service.send_text_message(
        account_id=created.account_id,
        text="started",
        receiver_uid="override_uid",
    )

    assert client.calls[0]["receiver_uid"] == "override_uid"


def test_send_text_message_uses_configured_notification_receiver(
    tmp_path: Path,
) -> None:
    service, _secret_store, client = _build_service(tmp_path)
    created = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="小鲁班主账号",
            token="uid_1234567890abcdef1234567890abcdef",
            notification_receiver="group-123",
        )
    )

    _ = service.send_text_message(
        account_id=created.account_id,
        text="started",
    )

    assert client.calls[0]["receiver_uid"] == "group-123"


def test_send_text_message_rejects_disabled_or_missing_token(
    tmp_path: Path,
) -> None:
    service, secret_store, _client = _build_service(tmp_path)
    created = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="小鲁班主账号",
            token="uid_1234567890abcdef1234567890abcdef",
            enabled=False,
        )
    )

    with pytest.raises(RuntimeError, match="xiaoluban_account_disabled"):
        _ = service.send_text_message(account_id=created.account_id, text="started")

    _ = service.set_account_enabled(created.account_id, True)
    secret_store.delete_token(tmp_path, created.account_id)

    with pytest.raises(RuntimeError, match="missing_xiaoluban_token"):
        _ = service.send_text_message(account_id=created.account_id, text="started")


def test_delete_disabled_account_removes_record_and_token(tmp_path: Path) -> None:
    service, secret_store, _client = _build_service(tmp_path)
    created = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="小鲁班主账号",
            token="uid_1234567890abcdef1234567890abcdef",
            enabled=False,
        )
    )

    service.delete_account(created.account_id)

    assert created.account_id not in secret_store.tokens
    with pytest.raises(KeyError):
        _ = service.get_account(created.account_id)


def test_delete_enabled_account_requires_force(tmp_path: Path) -> None:
    service, _secret_store, _client = _build_service(tmp_path)
    created = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="小鲁班主账号",
            token="uid_1234567890abcdef1234567890abcdef",
        )
    )

    with pytest.raises(RuntimeError):
        service.delete_account(created.account_id, force=False)


def test_has_usable_credentials_handles_missing_disabled_and_tokenless_accounts(
    tmp_path: Path,
) -> None:
    service, secret_store, _client = _build_service(tmp_path)
    created = service.create_account(
        XiaolubanAccountCreateInput(
            display_name="小鲁班主账号",
            token="uid_1234567890abcdef1234567890abcdef",
        )
    )

    assert service.has_usable_credentials("missing") is False
    assert service.has_usable_credentials(created.account_id) is True

    _ = service.set_account_enabled(created.account_id, False)
    assert service.has_usable_credentials(created.account_id) is False

    _ = service.set_account_enabled(created.account_id, True)
    secret_store.delete_token(tmp_path, created.account_id)
    assert service.has_usable_credentials(created.account_id) is False


def test_derive_uid_from_token_rejects_plugin_style_token() -> None:
    with pytest.raises(ValueError):
        _ = derive_uid_from_token("p_badtoken")


def test_account_repository_skips_invalid_rows_and_raises_for_invalid_get(
    tmp_path: Path,
) -> None:
    repository = XiaolubanAccountRepository(tmp_path / "xiaoluban_dirty.db")
    valid = repository.upsert_account(
        XiaolubanAccountRecord(
            account_id="xlb_valid",
            display_name="Valid",
            status=XiaolubanAccountStatus.ENABLED,
            derived_uid="uid",
        )
    )
    repository._conn.execute(
        """
        INSERT INTO xiaoluban_accounts(
            account_id,
            display_name,
            base_url,
            status,
            derived_uid,
            created_at,
            updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "xlb_bad_status",
            "Bad",
            DEFAULT_XIAOLUBAN_BASE_URL,
            "unknown",
            "uid",
            valid.created_at.isoformat(),
            valid.updated_at.isoformat(),
        ),
    )
    repository._conn.execute(
        """
        INSERT INTO xiaoluban_accounts(
            account_id,
            display_name,
            base_url,
            status,
            derived_uid,
            created_at,
            updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "xlb_bad_time",
            "Bad Time",
            DEFAULT_XIAOLUBAN_BASE_URL,
            XiaolubanAccountStatus.ENABLED.value,
            "uid",
            "not-a-date",
            valid.updated_at.isoformat(),
        ),
    )

    assert [account.account_id for account in repository.list_accounts()] == [
        "xlb_valid"
    ]
    with pytest.raises(KeyError):
        _ = repository.get_account("xlb_bad_status")
    with pytest.raises(KeyError):
        _ = repository.get_account("missing")


def test_account_repository_adds_missing_notification_columns(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "xiaoluban_legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE xiaoluban_accounts (
                account_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                status TEXT NOT NULL,
                derived_uid TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    repository = XiaolubanAccountRepository(db_path)

    columns = {
        str(row["name"])
        for row in repository._conn.execute(
            "PRAGMA table_info(xiaoluban_accounts)"
        ).fetchall()
    }
    assert "notification_workspace_ids_json" in columns
    assert "notification_receiver" in columns


def test_secret_store_normalizes_tokens_and_deletes_values(tmp_path: Path) -> None:
    app_secret_store = _FakeAppSecretStore()
    secret_store = XiaolubanSecretStore(secret_store=app_secret_store)

    secret_store.set_token(tmp_path, " xlb_1 ", " token ")
    assert secret_store.get_token(tmp_path, "xlb_1") == "token"

    secret_store.set_token(tmp_path, "xlb_1", "   ")
    assert secret_store.get_token(tmp_path, "xlb_1") is None

    secret_store.set_token(tmp_path, "xlb_1", "token")
    secret_store.delete_token(tmp_path, " xlb_1 ")
    assert secret_store.get_token(tmp_path, "xlb_1") is None
    assert app_secret_store.deleted == [("xiaoluban_account", "xlb_1", "token")]
    assert secret_store.can_persist_token() is True


def test_client_sends_json_request_through_configured_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", DEFAULT_XIAOLUBAN_BASE_URL)
    response = httpx.Response(200, text='{"message_id":"msg-1"}', request=request)
    fake_http_client = _FakeHttpClient(response)

    def fake_create_sync_http_client(*, timeout_seconds: float) -> _FakeHttpClient:
        assert timeout_seconds == 30.0
        return fake_http_client

    monkeypatch.setattr(
        xiaoluban_client_module,
        "create_sync_http_client",
        fake_create_sync_http_client,
    )

    result = XiaolubanClient().send_text_message(
        text="hello",
        receiver_uid="uid",
        auth_token="token",
        base_url=" http://xlb.test/send ",
        sender="sender",
    )

    assert result.message_id == "msg-1"
    assert fake_http_client.calls[0]["url"] == "http://xlb.test/send"
    assert fake_http_client.calls[0]["headers"] == {"Content-Type": "application/json"}
    content = fake_http_client.calls[0]["content"]
    assert isinstance(content, bytes)
    assert json.loads(content.decode("utf-8")) == {
        "content": "hello",
        "receiver": "uid",
        "auth": "token",
        "sender": "sender",
    }


def test_client_wraps_transport_failures_as_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_create_sync_http_client(*, timeout_seconds: float) -> _FailingHttpClient:
        assert timeout_seconds == 30.0
        return _FailingHttpClient()

    monkeypatch.setattr(
        xiaoluban_client_module,
        "create_sync_http_client",
        fake_create_sync_http_client,
    )

    with pytest.raises(RuntimeError, match="Xiaoluban API request failed"):
        _ = XiaolubanClient().send_text_message(
            text="hello",
            receiver_uid="uid",
            auth_token="token",
        )


def test_client_response_parsing_handles_fallback_shapes() -> None:
    request = httpx.Request("POST", DEFAULT_XIAOLUBAN_BASE_URL)
    empty = xiaoluban_client_module._parse_send_response(
        httpx.Response(200, text="", request=request)
    )
    raw = xiaoluban_client_module._parse_send_response(
        httpx.Response(200, text="ok", request=request)
    )
    error_response = httpx.Response(500, text="failed", request=request)

    with pytest.raises(RuntimeError, match="failed"):
        _ = xiaoluban_client_module._parse_send_response(error_response)
    assert empty.message_id.startswith("xlbmsg_")
    assert raw.raw_response == "ok"
    assert raw.message_id.startswith("xlbmsg_")


def _build_service(
    tmp_path: Path,
    workspace_lookup: _FakeWorkspaceLookup | None = None,
) -> tuple[XiaolubanGatewayService, _FakeXiaolubanSecretStore, _FakeXiaolubanClient]:
    secret_store = _FakeXiaolubanSecretStore()
    client = _FakeXiaolubanClient()
    service = XiaolubanGatewayService(
        config_dir=tmp_path,
        repository=XiaolubanAccountRepository(tmp_path / "xiaoluban.db"),
        secret_store=secret_store,
        client=client,
        workspace_lookup=workspace_lookup,
    )
    return service, secret_store, client
