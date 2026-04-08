from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path

import httpx
import pytest

from relay_teams.gateway.wechat.client import WeChatClient
from relay_teams.gateway.wechat.models import (
    WeChatAccountRecord,
    WeChatLoginSession,
)


class _FakeSyncHttpClient:
    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self._responses = list(responses)
        self.requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        content: bytes | None = None,
        headers: Mapping[str, str],
    ) -> httpx.Response:
        self.requests.append((method, url, content, dict(headers.items())))
        next_item = self._responses.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item

    @contextmanager
    def session(self) -> Iterator[_FakeSyncHttpClient]:
        yield self

    def __enter__(self) -> _FakeSyncHttpClient:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        _ = (exc_type, exc, tb)
        return None


def _response(
    status_code: int,
    payload: dict[str, object],
    *,
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        headers=headers,
        request=httpx.Request(method, url),
    )


def test_start_qr_login_accepts_success_payload_with_ret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url = "https://ilinkai.weixin.qq.com"
    fake_client = _FakeSyncHttpClient(
        [
            _response(
                200,
                {
                    "ret": 0,
                    "qrcode": "qr-token",
                    "qrcode_img_content": "https://example.test/qr.png",
                    "unexpected_field": "ok",
                },
                method="GET",
                url=f"{base_url}/ilink/bot/get_bot_qrcode?bot_type=3",
            )
        ]
    )

    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.create_sync_http_client",
        lambda **_: fake_client,
    )

    response = WeChatClient().start_qr_login(base_url=base_url)

    assert response.ret == 0
    assert response.qrcode == "qr-token"
    assert response.qrcode_img_content == "https://example.test/qr.png"


def test_start_qr_login_raises_runtime_error_for_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url = "https://ilinkai.weixin.qq.com"
    fake_client = _FakeSyncHttpClient(
        [
            _response(
                200,
                {
                    "ret": 5001,
                    "errmsg": "bot type invalid",
                    "qrcode": "unused",
                    "qrcode_img_content": "https://example.test/qr.png",
                },
                method="GET",
                url=f"{base_url}/ilink/bot/get_bot_qrcode?bot_type=3",
            )
        ]
    )

    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.create_sync_http_client",
        lambda **_: fake_client,
    )

    with pytest.raises(RuntimeError, match="WeChat start_qr_login failed"):
        WeChatClient().start_qr_login(base_url=base_url)


def test_wait_qr_login_retries_read_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    base_url = "https://ilinkai.weixin.qq.com"
    request_url = f"{base_url}/ilink/bot/get_qrcode_status?qrcode=qr-token"
    fake_client = _FakeSyncHttpClient(
        [
            httpx.ReadTimeout("The read operation timed out"),
            _response(
                200,
                {
                    "ret": 0,
                    "status": "confirmed",
                    "bot_token": "bot-token",
                    "ilink_bot_id": "wx_123",
                },
                method="GET",
                url=request_url,
            ),
        ]
    )

    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.create_sync_http_client",
        lambda **_: fake_client,
    )

    result = WeChatClient().wait_qr_login(
        login_session=WeChatLoginSession(
            session_key="wechat-login-1",
            qrcode="qr-token",
            qr_code_url="https://example.test/qr.png",
            base_url=base_url,
            started_at=datetime.now(tz=timezone.utc),
        ),
        timeout_ms=5_000,
    )

    assert result.status == "confirmed"
    assert result.ilink_bot_id == "wx_123"
    assert len(fake_client.requests) == 2


def test_send_text_message_builds_wechat_bot_message_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url = "https://ilinkai.weixin.qq.com"
    request_url = f"{base_url}/ilink/bot/sendmessage"
    fake_client = _FakeSyncHttpClient(
        [
            _response(
                200,
                {"ret": 0},
                method="POST",
                url=request_url,
            )
        ]
    )

    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.create_sync_http_client",
        lambda **_: fake_client,
    )

    WeChatClient().send_text_message(
        account=_account_record(base_url=base_url),
        token="bot-token",
        to_user_id="wx-peer",
        text="hello",
        context_token="ctx-1",
    )

    assert len(fake_client.requests) == 1
    _, _, content, _ = fake_client.requests[0]
    assert content is not None
    payload = json.loads(content.decode("utf-8"))
    assert payload["msg"]["from_user_id"] == ""
    assert payload["msg"]["to_user_id"] == "wx-peer"
    assert payload["msg"]["context_token"] == "ctx-1"
    assert payload["msg"]["message_type"] == 2
    assert payload["msg"]["message_state"] == 2
    assert payload["msg"]["item_list"] == [
        {
            "type": 1,
            "text_item": {"text": "hello"},
        }
    ]
    client_id = payload["msg"]["client_id"]
    assert isinstance(client_id, str)
    assert client_id.startswith("agent-teams-wechat-")


def test_send_text_message_raises_runtime_error_for_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url = "https://ilinkai.weixin.qq.com"
    request_url = f"{base_url}/ilink/bot/sendmessage"
    fake_client = _FakeSyncHttpClient(
        [
            _response(
                200,
                {
                    "ret": 4001,
                    "errmsg": "bot token expired",
                },
                method="POST",
                url=request_url,
            )
        ]
    )

    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.create_sync_http_client",
        lambda **_: fake_client,
    )

    with pytest.raises(RuntimeError, match="WeChat send_text_message failed"):
        WeChatClient().send_text_message(
            account=_account_record(base_url=base_url),
            token="bot-token",
            to_user_id="wx-peer",
            text="hello",
            context_token="ctx-1",
        )


def test_send_file_uploads_to_cdn_and_sends_file_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_url = "https://ilinkai.weixin.qq.com"
    cdn_base_url = "https://cdn.example.test/c2c"
    request_url = f"{base_url}/ilink/bot/sendmessage"
    upload_url = f"{base_url}/ilink/bot/getuploadurl"
    file_path = tmp_path / "report.pdf"
    file_path.write_bytes(b"hello wechat file")
    fake_client = _FakeSyncHttpClient(
        [
            _response(
                200,
                {"ret": 0, "upload_param": "upload-token"},
                method="POST",
                url=upload_url,
            ),
            _response(
                200,
                {},
                method="POST",
                url=(
                    f"{cdn_base_url}/upload?encrypted_query_param=upload-token"
                    "&filekey=ignored-in-test"
                ),
                headers={"x-encrypted-param": "download-token"},
            ),
            _response(
                200,
                {"ret": 0},
                method="POST",
                url=request_url,
            ),
        ]
    )

    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.create_sync_http_client",
        lambda **_: fake_client,
    )
    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.secrets.token_hex",
        lambda _: "ignored-in-test",
    )
    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.secrets.token_bytes",
        lambda _: bytes.fromhex("00112233445566778899aabbccddeeff"),
    )

    result = WeChatClient().send_file(
        account=_account_record(base_url=base_url, cdn_base_url=cdn_base_url),
        token="bot-token",
        to_user_id="wx-peer",
        file_path=file_path,
        context_token="ctx-1",
    )

    assert result == "file sent (report.pdf)"
    assert len(fake_client.requests) == 3

    _, upload_request_url, upload_content, _ = fake_client.requests[0]
    assert upload_request_url == upload_url
    assert upload_content is not None
    upload_payload = json.loads(upload_content.decode("utf-8"))
    assert upload_payload["filekey"] == "ignored-in-test"
    assert upload_payload["media_type"] == 3
    assert upload_payload["to_user_id"] == "wx-peer"
    assert upload_payload["rawsize"] == len(b"hello wechat file")
    assert upload_payload["no_need_thumb"] is True
    assert upload_payload["aeskey"] == "00112233445566778899aabbccddeeff"

    _, cdn_request_url, cdn_content, cdn_headers = fake_client.requests[1]
    assert (
        cdn_request_url
        == f"{cdn_base_url}/upload?encrypted_query_param=upload-token&filekey=ignored-in-test"
    )
    assert cdn_content is not None
    assert len(cdn_content) % 16 == 0
    assert cdn_headers["Content-Type"] == "application/octet-stream"

    _, _, send_content, _ = fake_client.requests[2]
    assert send_content is not None
    send_payload = json.loads(send_content.decode("utf-8"))
    assert send_payload["msg"]["to_user_id"] == "wx-peer"
    assert send_payload["msg"]["context_token"] == "ctx-1"
    assert send_payload["msg"]["item_list"] == [
        {
            "type": 4,
            "file_item": {
                "media": {
                    "encrypt_query_param": "download-token",
                    "aes_key": "MDAxMTIyMzM0NDU1NjY3Nzg4OTlhYWJiY2NkZGVlZmY=",
                    "encrypt_type": 1,
                },
                "file_name": "report.pdf",
                "len": str(len(b"hello wechat file")),
            },
        }
    ]


def test_send_file_routes_image_as_image_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_url = "https://ilinkai.weixin.qq.com"
    cdn_base_url = "https://cdn.example.test/c2c"
    file_path = tmp_path / "photo.png"
    file_path.write_bytes(b"png-bytes")
    fake_client = _FakeSyncHttpClient(
        [
            _response(
                200,
                {"ret": 0, "upload_param": "upload-token"},
                method="POST",
                url=f"{base_url}/ilink/bot/getuploadurl",
            ),
            _response(
                200,
                {},
                method="POST",
                url=(
                    f"{cdn_base_url}/upload?encrypted_query_param=upload-token"
                    "&filekey=image-key"
                ),
                headers={"x-encrypted-param": "download-token"},
            ),
            _response(
                200,
                {"ret": 0},
                method="POST",
                url=f"{base_url}/ilink/bot/sendmessage",
            ),
        ]
    )

    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.create_sync_http_client",
        lambda **_: fake_client,
    )
    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.secrets.token_hex",
        lambda _: "image-key",
    )
    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.secrets.token_bytes",
        lambda _: bytes.fromhex("00112233445566778899aabbccddeeff"),
    )

    result = WeChatClient().send_file(
        account=_account_record(base_url=base_url, cdn_base_url=cdn_base_url),
        token="bot-token",
        to_user_id="wx-peer",
        file_path=file_path,
        context_token=None,
    )

    assert result == "image sent (photo.png)"
    upload_request_content = fake_client.requests[0][2]
    assert upload_request_content is not None
    upload_payload = json.loads(upload_request_content.decode("utf-8"))
    assert upload_payload["media_type"] == 1
    send_request_content = fake_client.requests[2][2]
    assert send_request_content is not None
    send_payload = json.loads(send_request_content.decode("utf-8"))
    cdn_request_content = fake_client.requests[1][2]
    assert cdn_request_content is not None
    assert send_payload["msg"]["item_list"] == [
        {
            "type": 2,
            "image_item": {
                "media": {
                    "encrypt_query_param": "download-token",
                    "aes_key": "MDAxMTIyMzM0NDU1NjY3Nzg4OTlhYWJiY2NkZGVlZmY=",
                    "encrypt_type": 1,
                },
                "mid_size": len(cdn_request_content),
            },
        }
    ]


def test_send_file_extracts_nested_upload_param(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_url = "https://ilinkai.weixin.qq.com"
    cdn_base_url = "https://cdn.example.test/c2c"
    file_path = tmp_path / "report.pdf"
    file_path.write_bytes(b"hello wechat file")
    fake_client = _FakeSyncHttpClient(
        [
            _response(
                200,
                {"ret": 0, "data": {"upload_param": "upload-token"}},
                method="POST",
                url=f"{base_url}/ilink/bot/getuploadurl",
            ),
            _response(
                200,
                {},
                method="POST",
                url=(
                    f"{cdn_base_url}/upload?encrypted_query_param=upload-token"
                    "&filekey=nested-key"
                ),
                headers={"x-encrypted-param": "download-token"},
            ),
            _response(
                200,
                {"ret": 0},
                method="POST",
                url=f"{base_url}/ilink/bot/sendmessage",
            ),
        ]
    )

    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.create_sync_http_client",
        lambda **_: fake_client,
    )
    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.secrets.token_hex",
        lambda _: "nested-key",
    )
    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.secrets.token_bytes",
        lambda _: bytes.fromhex("00112233445566778899aabbccddeeff"),
    )

    result = WeChatClient().send_file(
        account=_account_record(base_url=base_url, cdn_base_url=cdn_base_url),
        token="bot-token",
        to_user_id="wx-peer",
        file_path=file_path,
        context_token=None,
    )

    assert result == "file sent (report.pdf)"
    assert len(fake_client.requests) == 3


def test_send_file_extracts_nested_camel_case_upload_param(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_url = "https://ilinkai.weixin.qq.com"
    cdn_base_url = "https://cdn.example.test/c2c"
    file_path = tmp_path / "report.pdf"
    file_path.write_bytes(b"hello wechat file")
    fake_client = _FakeSyncHttpClient(
        [
            _response(
                200,
                {"ret": 0, "data": [{"uploadParam": "upload-token"}]},
                method="POST",
                url=f"{base_url}/ilink/bot/getuploadurl",
            ),
            _response(
                200,
                {},
                method="POST",
                url=(
                    f"{cdn_base_url}/upload?encrypted_query_param=upload-token"
                    "&filekey=camel-key"
                ),
                headers={"x-encrypted-param": "download-token"},
            ),
            _response(
                200,
                {"ret": 0},
                method="POST",
                url=f"{base_url}/ilink/bot/sendmessage",
            ),
        ]
    )

    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.create_sync_http_client",
        lambda **_: fake_client,
    )
    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.secrets.token_hex",
        lambda _: "camel-key",
    )
    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.secrets.token_bytes",
        lambda _: bytes.fromhex("00112233445566778899aabbccddeeff"),
    )

    result = WeChatClient().send_file(
        account=_account_record(base_url=base_url, cdn_base_url=cdn_base_url),
        token="bot-token",
        to_user_id="wx-peer",
        file_path=file_path,
        context_token=None,
    )

    assert result == "file sent (report.pdf)"
    assert len(fake_client.requests) == 3


def test_send_file_uses_upload_full_url_when_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_url = "https://ilinkai.weixin.qq.com"
    cdn_base_url = "https://cdn.example.test/c2c"
    upload_full_url = (
        "https://upload.example.test/c2c/upload?"
        "encrypted_query_param=upload-token&filekey=full-url-key"
    )
    file_path = tmp_path / "report.pdf"
    file_path.write_bytes(b"hello wechat file")
    fake_client = _FakeSyncHttpClient(
        [
            _response(
                200,
                {"upload_full_url": upload_full_url},
                method="POST",
                url=f"{base_url}/ilink/bot/getuploadurl",
            ),
            _response(
                200,
                {},
                method="POST",
                url=upload_full_url,
                headers={"x-encrypted-param": "download-token"},
            ),
            _response(
                200,
                {"ret": 0},
                method="POST",
                url=f"{base_url}/ilink/bot/sendmessage",
            ),
        ]
    )

    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.create_sync_http_client",
        lambda **_: fake_client,
    )
    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.secrets.token_hex",
        lambda _: "full-url-key",
    )
    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.secrets.token_bytes",
        lambda _: bytes.fromhex("00112233445566778899aabbccddeeff"),
    )

    result = WeChatClient().send_file(
        account=_account_record(base_url=base_url, cdn_base_url=cdn_base_url),
        token="bot-token",
        to_user_id="wx-peer",
        file_path=file_path,
        context_token=None,
    )

    assert result == "file sent (report.pdf)"
    assert len(fake_client.requests) == 3
    assert fake_client.requests[1][1] == upload_full_url


def test_send_file_raises_diagnostic_error_when_upload_param_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_url = "https://ilinkai.weixin.qq.com"
    file_path = tmp_path / "report.pdf"
    file_path.write_bytes(b"hello wechat file")
    fake_client = _FakeSyncHttpClient(
        [
            _response(
                200,
                {"ret": 0, "data": {"message": "ok"}},
                method="POST",
                url=f"{base_url}/ilink/bot/getuploadurl",
            )
        ]
    )

    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.create_sync_http_client",
        lambda **_: fake_client,
    )
    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.secrets.token_hex",
        lambda _: "missing-key",
    )
    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.secrets.token_bytes",
        lambda _: bytes.fromhex("00112233445566778899aabbccddeeff"),
    )

    with pytest.raises(RuntimeError, match="top_level_keys"):
        WeChatClient().send_file(
            account=_account_record(base_url=base_url),
            token="bot-token",
            to_user_id="wx-peer",
            file_path=file_path,
            context_token=None,
        )


def test_send_typing_raises_runtime_error_for_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url = "https://ilinkai.weixin.qq.com"
    request_url = f"{base_url}/ilink/bot/sendtyping"
    fake_client = _FakeSyncHttpClient(
        [
            _response(
                200,
                {
                    "ret": 4002,
                    "errmsg": "typing ticket invalid",
                },
                method="POST",
                url=request_url,
            )
        ]
    )

    monkeypatch.setattr(
        "relay_teams.gateway.wechat.client.create_sync_http_client",
        lambda **_: fake_client,
    )

    with pytest.raises(RuntimeError, match="WeChat send_typing failed"):
        WeChatClient().send_typing(
            account=_account_record(base_url=base_url),
            token="bot-token",
            peer_user_id="wx-peer",
            typing_ticket="typing-ticket",
            status=1,
        )


def _account_record(
    *, base_url: str, cdn_base_url: str = "https://cdn.example.test"
) -> WeChatAccountRecord:
    return WeChatAccountRecord(
        account_id="wx-account-1",
        display_name="WeChat Account",
        base_url=base_url,
        cdn_base_url=cdn_base_url,
    )
