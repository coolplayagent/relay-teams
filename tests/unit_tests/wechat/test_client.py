from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import json

import httpx
import pytest

from agent_teams.wechat.client import WeChatClient
from agent_teams.wechat.models import WeChatAccountRecord, WeChatLoginSession


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
) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
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
        "agent_teams.wechat.client.create_sync_http_client",
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
        "agent_teams.wechat.client.create_sync_http_client",
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
        "agent_teams.wechat.client.create_sync_http_client",
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
        "agent_teams.wechat.client.create_sync_http_client",
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
        "agent_teams.wechat.client.create_sync_http_client",
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
        "agent_teams.wechat.client.create_sync_http_client",
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


def _account_record(*, base_url: str) -> WeChatAccountRecord:
    return WeChatAccountRecord(
        account_id="wx-account-1",
        display_name="WeChat Account",
        base_url=base_url,
        cdn_base_url="https://cdn.example.test",
    )
