from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import httpx
import pytest

from agent_teams.feishu.client import FeishuClient
from agent_teams.feishu.models import FeishuEnvironment


class _FakeSyncHttpClient:
    def __init__(
        self, responses: Mapping[tuple[str, str], list[httpx.Response]]
    ) -> None:
        self._responses = {key: list(value) for key, value in responses.items()}
        self.requests: list[
            tuple[
                str,
                str,
                dict[str, str],
                dict[str, str] | None,
                dict[str, object] | None,
            ]
        ] = []
        self.posts: list[
            tuple[
                str,
                dict[str, str],
                dict[str, str],
                tuple[str, str],
            ]
        ] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: dict[str, str] | None = None,
        json: dict[str, object] | None = None,
    ) -> httpx.Response:
        self.requests.append((method, url, dict(headers.items()), params, json))
        key = (method, url)
        return self._responses[key].pop(0)

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        data: Mapping[str, str],
        files: Mapping[str, tuple[str, object]],
    ) -> httpx.Response:
        file_entries = list(files.items())
        if len(file_entries) != 1:
            raise AssertionError("expected a single file entry")
        file_field_name, file_tuple = file_entries[0]
        uploaded_name = str(file_tuple[0])
        self.posts.append(
            (
                url,
                dict(headers.items()),
                dict(data.items()),
                (file_field_name, uploaded_name),
            )
        )
        key = ("POST", url)
        return self._responses[key].pop(0)


def _response(
    status_code: int,
    payload: dict[str, object],
    *,
    method: str,
    url: str,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request(method, url),
    )


def test_get_chat_name_uses_net_client_and_cache(monkeypatch) -> None:
    base_url = "https://open.feishu.cn"
    fake_client = _FakeSyncHttpClient(
        {
            (
                "POST",
                f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
            ): [
                _response(
                    200,
                    {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                    method="POST",
                    url=f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
                )
            ],
            ("GET", f"{base_url}/open-apis/im/v1/chats/oc_group_1"): [
                _response(
                    200,
                    {"code": 0, "data": {"name": "Release Updates"}},
                    method="GET",
                    url=f"{base_url}/open-apis/im/v1/chats/oc_group_1",
                )
            ],
        }
    )
    created_with_env: list[Mapping[str, str] | None] = []

    def _fake_create_sync_http_client(
        *,
        merged_env: Mapping[str, str] | None = None,
        **_: object,
    ) -> _FakeSyncHttpClient:
        created_with_env.append(merged_env)
        return fake_client

    monkeypatch.setattr(
        "agent_teams.feishu.client.create_sync_http_client",
        _fake_create_sync_http_client,
    )
    client = FeishuClient(merged_env={"SSL_VERIFY": "false"})
    environment = FeishuEnvironment(app_id="cli_1", app_secret="secret", app_name="bot")

    first = client.get_chat_name(chat_id="oc_group_1", environment=environment)
    second = client.get_chat_name(chat_id="oc_group_1", environment=environment)

    assert first == "Release Updates"
    assert second == "Release Updates"
    assert created_with_env == [{"SSL_VERIFY": "false"}]
    assert [request[:2] for request in fake_client.requests] == [
        ("POST", f"{base_url}/open-apis/auth/v3/tenant_access_token/internal"),
        ("GET", f"{base_url}/open-apis/im/v1/chats/oc_group_1"),
    ]
    assert fake_client.requests[1][2]["Authorization"] == "Bearer token-1"


def test_get_user_name_uses_net_client_and_cache(monkeypatch) -> None:
    base_url = "https://open.feishu.cn"
    fake_client = _FakeSyncHttpClient(
        {
            (
                "POST",
                f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
            ): [
                _response(
                    200,
                    {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                    method="POST",
                    url=f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
                )
            ],
            ("GET", f"{base_url}/open-apis/contact/v3/users/ou_user_1"): [
                _response(
                    200,
                    {"code": 0, "data": {"user": {"name": "Alice"}}},
                    method="GET",
                    url=f"{base_url}/open-apis/contact/v3/users/ou_user_1",
                )
            ],
        }
    )

    monkeypatch.setattr(
        "agent_teams.feishu.client.create_sync_http_client",
        lambda **_: fake_client,
    )
    client = FeishuClient()
    environment = FeishuEnvironment(app_id="cli_1", app_secret="secret", app_name="bot")

    first = client.get_user_name(open_id="ou_user_1", environment=environment)
    second = client.get_user_name(open_id="ou_user_1", environment=environment)

    assert first == "Alice"
    assert second == "Alice"
    assert [request[:2] for request in fake_client.requests] == [
        ("POST", f"{base_url}/open-apis/auth/v3/tenant_access_token/internal"),
        ("GET", f"{base_url}/open-apis/contact/v3/users/ou_user_1"),
    ]
    assert fake_client.requests[1][3] == {"user_id_type": "open_id"}


def test_send_text_message_uses_net_client_request_chain(monkeypatch) -> None:
    base_url = "https://open.feishu.cn"
    fake_client = _FakeSyncHttpClient(
        {
            (
                "POST",
                f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
            ): [
                _response(
                    200,
                    {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                    method="POST",
                    url=f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
                )
            ],
            ("POST", f"{base_url}/open-apis/im/v1/messages"): [
                _response(
                    200,
                    {"code": 0, "data": {"message_id": "om_1"}},
                    method="POST",
                    url=f"{base_url}/open-apis/im/v1/messages",
                )
            ],
        }
    )

    monkeypatch.setattr(
        "agent_teams.feishu.client.create_sync_http_client",
        lambda **_: fake_client,
    )
    client = FeishuClient()
    environment = FeishuEnvironment(app_id="cli_1", app_secret="secret", app_name="bot")

    client.send_text_message(
        chat_id="oc_group_1",
        text="hello",
        environment=environment,
    )

    assert [request[:2] for request in fake_client.requests] == [
        ("POST", f"{base_url}/open-apis/auth/v3/tenant_access_token/internal"),
        ("POST", f"{base_url}/open-apis/im/v1/messages"),
    ]
    assert fake_client.requests[1][3] == {"receive_id_type": "chat_id"}
    assert fake_client.requests[1][4] == {
        "receive_id": "oc_group_1",
        "msg_type": "text",
        "content": '{"text": "hello"}',
    }


def test_get_chat_name_raises_runtime_error_for_failed_response(monkeypatch) -> None:
    base_url = "https://open.feishu.cn"
    fake_client = _FakeSyncHttpClient(
        {
            (
                "POST",
                f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
            ): [
                _response(
                    200,
                    {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                    method="POST",
                    url=f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
                )
            ],
            ("GET", f"{base_url}/open-apis/im/v1/chats/oc_group_2"): [
                _response(
                    200,
                    {"code": 99991663, "msg": "chat_error"},
                    method="GET",
                    url=f"{base_url}/open-apis/im/v1/chats/oc_group_2",
                )
            ],
        }
    )

    monkeypatch.setattr(
        "agent_teams.feishu.client.create_sync_http_client",
        lambda **_: fake_client,
    )
    client = FeishuClient()
    environment = FeishuEnvironment(app_id="cli_1", app_secret="secret", app_name="bot")

    try:
        client.get_chat_name(chat_id="oc_group_2", environment=environment)
    except RuntimeError as exc:
        assert "chat_error" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")


def test_send_file_uploads_image_and_sends_image_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"png")
    base_url = "https://open.feishu.cn"
    fake_client = _FakeSyncHttpClient(
        {
            (
                "POST",
                f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
            ): [
                _response(
                    200,
                    {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                    method="POST",
                    url=f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
                )
            ],
            ("POST", f"{base_url}/open-apis/im/v1/images"): [
                _response(
                    200,
                    {"code": 0, "data": {"image_key": "img-key-1"}},
                    method="POST",
                    url=f"{base_url}/open-apis/im/v1/images",
                )
            ],
            ("POST", f"{base_url}/open-apis/im/v1/messages"): [
                _response(
                    200,
                    {"code": 0, "data": {"message_id": "om_1"}},
                    method="POST",
                    url=f"{base_url}/open-apis/im/v1/messages",
                )
            ],
        }
    )

    monkeypatch.setattr(
        "agent_teams.feishu.client.create_sync_http_client",
        lambda **_: fake_client,
    )
    client = FeishuClient()
    environment = FeishuEnvironment(app_id="cli_1", app_secret="secret", app_name="bot")

    result = client.send_file(
        chat_id="oc_group_1",
        file_path=image_path,
        environment=environment,
    )

    assert result == "image sent (diagram.png)"
    assert fake_client.posts == [
        (
            f"{base_url}/open-apis/im/v1/images",
            {"Authorization": "Bearer token-1"},
            {"image_type": "message"},
            ("image", "diagram.png"),
        )
    ]
    assert fake_client.requests[-1][4] == {
        "receive_id": "oc_group_1",
        "msg_type": "image",
        "content": '{"image_key": "img-key-1"}',
    }


def test_send_file_uploads_regular_file_and_sends_file_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "report.pdf"
    file_path.write_bytes(b"pdf")
    base_url = "https://open.feishu.cn"
    fake_client = _FakeSyncHttpClient(
        {
            (
                "POST",
                f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
            ): [
                _response(
                    200,
                    {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                    method="POST",
                    url=f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
                )
            ],
            ("POST", f"{base_url}/open-apis/im/v1/files"): [
                _response(
                    200,
                    {"code": 0, "data": {"file_key": "file-key-1"}},
                    method="POST",
                    url=f"{base_url}/open-apis/im/v1/files",
                )
            ],
            ("POST", f"{base_url}/open-apis/im/v1/messages"): [
                _response(
                    200,
                    {"code": 0, "data": {"message_id": "om_1"}},
                    method="POST",
                    url=f"{base_url}/open-apis/im/v1/messages",
                )
            ],
        }
    )

    monkeypatch.setattr(
        "agent_teams.feishu.client.create_sync_http_client",
        lambda **_: fake_client,
    )
    client = FeishuClient()
    environment = FeishuEnvironment(app_id="cli_1", app_secret="secret", app_name="bot")

    result = client.send_file(
        chat_id="oc_group_1",
        file_path=file_path,
        environment=environment,
    )

    assert result == "file sent (report.pdf)"
    assert fake_client.posts == [
        (
            f"{base_url}/open-apis/im/v1/files",
            {"Authorization": "Bearer token-1"},
            {"file_type": "pdf", "file_name": "report.pdf"},
            ("file", "report.pdf"),
        )
    ]
    assert fake_client.requests[-1][4] == {
        "receive_id": "oc_group_1",
        "msg_type": "file",
        "content": '{"file_key": "file-key-1", "file_name": "report.pdf"}',
    }
