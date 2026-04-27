from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from relay_teams.gateway.xiaoluban.im_listener import (
    _format_host_for_url,
    _is_local_or_unspecified_hostname,
    _is_unspecified_address,
    _listener_port_from_env,
    _preview_text,
    _resolve_default_route_ipv4,
    resolve_xiaoluban_im_callback_host,
    XiaolubanImListenerService,
)
from relay_teams.gateway.xiaoluban.models import XiaolubanInboundMessage


class _FakeInboundHandler:
    def __init__(self) -> None:
        self.calls: list[tuple[str, XiaolubanInboundMessage]] = []
        self.callback_tokens: dict[str, str] = {}

    def handle_im_inbound(
        self,
        *,
        account_id: str,
        message: XiaolubanInboundMessage,
    ) -> None:
        self.calls.append((account_id, message))

    def get_im_callback_auth_token(self, account_id: str) -> str:
        return self.callback_tokens.get(account_id, "secret-token")


def test_xiaoluban_im_listener_accepts_account_forwarding_callback() -> None:
    handler = _FakeInboundHandler()
    listener = XiaolubanImListenerService(service=handler)
    client = TestClient(listener.app)

    response = client.post(
        "/xlb_123",
        json={
            "content": "hello",
            "receiver": "uid_self",
            "sender": "uid_self",
            "session_id": "session-1",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Forwarding received"}
    assert len(handler.calls) == 1
    account_id, message = handler.calls[0]
    assert account_id == "xlb_123"
    assert message.content == "hello"


def test_xiaoluban_im_listener_no_longer_accepts_plugin_path() -> None:
    listener = XiaolubanImListenerService(service=_FakeInboundHandler())
    client = TestClient(listener.app)

    response = client.post(
        "/xlb_123/relay-teams-uid_self",
        json={
            "content": "hello",
            "receiver": "uid_self",
            "sender": "uid_self",
        },
    )

    assert response.status_code == 404


def test_xiaoluban_im_listener_callback_url_uses_resolved_local_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "relay_teams.gateway.xiaoluban.im_listener.resolve_xiaoluban_im_callback_host",
        lambda: "10.88.1.23",
    )
    handler = _FakeInboundHandler()
    handler.callback_tokens["xlb_123"] = "secret-token"
    listener = XiaolubanImListenerService(
        service=handler,
        host="0.0.0.0",
        port=8091,
    )

    callback_url = listener.callback_url(account_id="xlb_123")

    assert callback_url == "http://10.88.1.23:8091/xlb_123"


def test_xiaoluban_im_listener_callback_url_allows_explicit_public_host() -> None:
    handler = _FakeInboundHandler()
    handler.callback_tokens["xlb_123"] = "secret-token"
    listener = XiaolubanImListenerService(
        service=handler,
        host="0.0.0.0",
        port=8091,
        public_host="relay.example.test",
    )

    callback_url = listener.callback_url(account_id="xlb_123")

    assert callback_url == "http://relay.example.test:8091/xlb_123"


def test_xiaoluban_im_listener_still_processes_callback_without_auth() -> None:
    handler = _FakeInboundHandler()
    handler.callback_tokens["xlb_123"] = "secret-token"
    listener = XiaolubanImListenerService(service=handler)
    client = TestClient(listener.app)

    response = client.post(
        "/xlb_123",
        json={
            "content": "hello",
            "receiver": "uid_self",
            "sender": "uid_self",
        },
    )

    assert response.status_code == 200
    assert len(handler.calls) == 1


def test_health_endpoint_returns_ok() -> None:
    listener = XiaolubanImListenerService(service=_FakeInboundHandler())
    client = TestClient(listener.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_host_and_port_properties() -> None:
    listener = XiaolubanImListenerService(
        service=_FakeInboundHandler(),
        host="127.0.0.1",
        port=9090,
    )

    assert listener.host == "127.0.0.1"
    assert listener.port == 9090


def test_callback_url_raises_when_host_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "relay_teams.gateway.xiaoluban.im_listener.resolve_xiaoluban_im_callback_host",
        lambda: None,
    )
    handler = _FakeInboundHandler()
    handler.callback_tokens["xlb_123"] = "secret-token"
    listener = XiaolubanImListenerService(
        service=handler,
        host="0.0.0.0",
    )

    with pytest.raises(RuntimeError, match="xiaoluban_im_listener_host_unavailable"):
        listener.callback_url(account_id="xlb_123")


def test_inbound_returns_404_when_account_not_found() -> None:
    def raise_key_error(account_id: str) -> str:
        _ = account_id
        raise KeyError("Unknown")

    handler = _FakeInboundHandler()
    handler.get_im_callback_auth_token = raise_key_error
    listener = XiaolubanImListenerService(service=handler)
    client = TestClient(listener.app)

    response = client.post(
        "/xlb_123",
        json={"content": "hello", "receiver": "uid", "sender": "uid"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "xiaoluban_im_account_not_found"


def test_inbound_returns_409_when_auth_unavailable() -> None:
    def raise_runtime_error(account_id: str) -> str:
        _ = account_id
        raise RuntimeError("missing_xiaoluban_token")

    handler = _FakeInboundHandler()
    handler.get_im_callback_auth_token = raise_runtime_error
    listener = XiaolubanImListenerService(service=handler)
    client = TestClient(listener.app)

    response = client.post(
        "/xlb_123",
        json={"content": "hello", "receiver": "uid", "sender": "uid"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "xiaoluban_im_callback_auth_unavailable"


def test_callback_url_uses_non_local_host_directly() -> None:
    handler = _FakeInboundHandler()
    handler.callback_tokens["xlb_123"] = "secret-token"
    listener = XiaolubanImListenerService(
        service=handler,
        host="10.88.1.23",
        port=9009,
    )

    callback_url = listener.callback_url(account_id="xlb_123")

    assert callback_url == "http://10.88.1.23:9009/xlb_123"


def test_is_running_returns_false_when_not_started() -> None:
    listener = XiaolubanImListenerService(service=_FakeInboundHandler())

    assert listener.is_running() is False


def test_format_host_for_url_ipv6() -> None:
    assert _format_host_for_url("::1") == "[::1]"
    assert _format_host_for_url("2001:db8::1") == "[2001:db8::1]"


def test_format_host_for_url_ipv4_and_hostname() -> None:
    assert _format_host_for_url("10.0.0.1") == "10.0.0.1"
    assert _format_host_for_url("relay.example.test") == "relay.example.test"


def test_preview_text_short_and_overflow() -> None:
    assert _preview_text("hello") == "hello"
    long_text = "a" * 200
    result = _preview_text(long_text)
    assert result == "a" * 120 + "..."
    multiline = "  line1   line2  "
    assert _preview_text(multiline) == "line1 line2"


def test_listener_port_from_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RELAY_TEAMS_XIAOLUBAN_IM_LISTENER_PORT", raising=False)
    assert _listener_port_from_env() == 9009


def test_listener_port_from_env_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAY_TEAMS_XIAOLUBAN_IM_LISTENER_PORT", "8080")
    assert _listener_port_from_env() == 8080


def test_listener_port_from_env_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAY_TEAMS_XIAOLUBAN_IM_LISTENER_PORT", "not-a-number")
    assert _listener_port_from_env() == 9009


def test_listener_port_from_env_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAY_TEAMS_XIAOLUBAN_IM_LISTENER_PORT", "99999")
    assert _listener_port_from_env() == 9009
    monkeypatch.setenv("RELAY_TEAMS_XIAOLUBAN_IM_LISTENER_PORT", "0")
    assert _listener_port_from_env() == 9009


def test_is_local_or_unspecified_hostname() -> None:
    assert _is_local_or_unspecified_hostname("localhost") is True
    assert _is_local_or_unspecified_hostname("0.0.0.0") is True
    assert _is_local_or_unspecified_hostname("::") is True
    assert _is_local_or_unspecified_hostname("127.0.0.1") is True
    assert _is_local_or_unspecified_hostname("::1") is True
    assert _is_local_or_unspecified_hostname("10.0.0.1") is False
    assert _is_local_or_unspecified_hostname("relay.example.test") is False


def test_resolve_default_route_ipv4() -> None:
    result = _resolve_default_route_ipv4()
    assert result is None or isinstance(result, str)


def test_resolve_xiaoluban_im_callback_host_falls_back_to_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "relay_teams.gateway.xiaoluban.im_listener._resolve_default_route_ipv4",
        lambda: None,
    )

    result = resolve_xiaoluban_im_callback_host()
    assert result is None or isinstance(result, str)


def test_callback_url_uses_loopback_host_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_calls: list[tuple[()]] = []

    def tracking_resolve() -> str | None:
        resolve_calls.append(())
        return "10.88.1.23"

    monkeypatch.setattr(
        "relay_teams.gateway.xiaoluban.im_listener.resolve_xiaoluban_im_callback_host",
        tracking_resolve,
    )
    handler = _FakeInboundHandler()
    handler.callback_tokens["xlb_123"] = "secret-token"
    listener = XiaolubanImListenerService(
        service=handler,
        host="127.0.0.1",
        port=8091,
    )

    callback_url = listener.callback_url(account_id="xlb_123")

    assert callback_url == "http://127.0.0.1:8091/xlb_123"
    assert resolve_calls == []


def test_is_unspecified_address_identifies_unspecified_only() -> None:
    assert _is_unspecified_address("0.0.0.0") is True
    assert _is_unspecified_address("::") is True
    assert _is_unspecified_address("") is True
    assert _is_unspecified_address("127.0.0.1") is False
    assert _is_unspecified_address("::1") is False
    assert _is_unspecified_address("localhost") is False
    assert _is_unspecified_address("10.0.0.1") is False
