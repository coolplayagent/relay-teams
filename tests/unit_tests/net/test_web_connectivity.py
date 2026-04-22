# -*- coding: utf-8 -*-
from __future__ import annotations

import httpx

from relay_teams.env.proxy_env import ProxyEnvInput, resolve_proxy_env_config
from relay_teams.net.web_connectivity import (
    WebConnectivityProbeRequest,
    WebConnectivityProbeService,
)


class _FakeProbeClient:
    def __init__(
        self,
        *,
        head_response: httpx.Response | None = None,
        get_response: httpx.Response | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._head_response = head_response
        self._get_response = get_response
        self._error = error
        self.calls: list[tuple[str, str]] = []

    def __enter__(self) -> _FakeProbeClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def head(self, url: str) -> httpx.Response:
        self.calls.append(("HEAD", url))
        if self._error is not None:
            raise self._error
        assert self._head_response is not None
        return self._head_response

    def get(self, url: str) -> httpx.Response:
        self.calls.append(("GET", url))
        assert self._get_response is not None
        return self._get_response


def test_probe_web_connectivity_uses_head_by_default(monkeypatch) -> None:
    fake_client = _FakeProbeClient(
        head_response=httpx.Response(
            200, request=httpx.Request("HEAD", "https://example.com")
        ),
    )
    monkeypatch.setattr(
        "relay_teams.net.web_connectivity.create_sync_http_client",
        lambda **_kwargs: fake_client,
    )
    service = WebConnectivityProbeService(
        get_proxy_config=lambda: resolve_proxy_env_config(
            {"HTTP_PROXY": "http://proxy.example:8080"}
        )
    )

    result = service.probe(WebConnectivityProbeRequest(url="https://example.com"))

    assert result.ok is True
    assert result.used_method == "HEAD"
    assert result.diagnostics.used_proxy is True
    assert fake_client.calls == [("HEAD", "https://example.com")]


def test_probe_web_connectivity_falls_back_to_get(monkeypatch) -> None:
    fake_client = _FakeProbeClient(
        head_response=httpx.Response(
            405, request=httpx.Request("HEAD", "https://example.com")
        ),
        get_response=httpx.Response(
            200, request=httpx.Request("GET", "https://example.com")
        ),
    )
    monkeypatch.setattr(
        "relay_teams.net.web_connectivity.create_sync_http_client",
        lambda **_kwargs: fake_client,
    )
    service = WebConnectivityProbeService(
        get_proxy_config=lambda: resolve_proxy_env_config({"NO_PROXY": "example.com"})
    )

    result = service.probe(WebConnectivityProbeRequest(url="https://example.com"))

    assert result.ok is True
    assert result.used_method == "GET"
    assert result.diagnostics.used_proxy is False
    assert fake_client.calls == [
        ("HEAD", "https://example.com"),
        ("GET", "https://example.com"),
    ]


def test_probe_web_connectivity_respects_semicolon_no_proxy_wildcards(
    monkeypatch,
) -> None:
    fake_client = _FakeProbeClient(
        head_response=httpx.Response(
            200, request=httpx.Request("HEAD", "https://127.0.0.1")
        ),
    )
    monkeypatch.setattr(
        "relay_teams.net.web_connectivity.create_sync_http_client",
        lambda **_kwargs: fake_client,
    )
    service = WebConnectivityProbeService(
        get_proxy_config=lambda: resolve_proxy_env_config(
            {
                "HTTPS_PROXY": "http://proxy.example:8443",
                "NO_PROXY": "localhost;127.*;<local>",
            }
        )
    )

    result = service.probe(WebConnectivityProbeRequest(url="https://127.0.0.1"))

    assert result.ok is True
    assert result.diagnostics.used_proxy is False


def test_probe_web_connectivity_returns_timeout_error(monkeypatch) -> None:
    fake_client = _FakeProbeClient(error=httpx.ReadTimeout("timed out"))
    monkeypatch.setattr(
        "relay_teams.net.web_connectivity.create_sync_http_client",
        lambda **_kwargs: fake_client,
    )
    service = WebConnectivityProbeService(
        get_proxy_config=lambda: resolve_proxy_env_config(
            {"HTTP_PROXY": "http://proxy.example:8080"}
        )
    )

    result = service.probe(
        WebConnectivityProbeRequest(url="https://example.com", timeout_ms=2000)
    )

    assert result.ok is False
    assert result.error_code == "network_timeout"
    assert result.retryable is True


def test_probe_web_connectivity_treats_http_404_as_reachable(monkeypatch) -> None:
    fake_client = _FakeProbeClient(
        head_response=httpx.Response(
            404, request=httpx.Request("HEAD", "https://example.com")
        ),
    )
    monkeypatch.setattr(
        "relay_teams.net.web_connectivity.create_sync_http_client",
        lambda **_kwargs: fake_client,
    )
    service = WebConnectivityProbeService(
        get_proxy_config=lambda: resolve_proxy_env_config(
            {"HTTP_PROXY": "http://proxy.example:8080"}
        )
    )

    result = service.probe(WebConnectivityProbeRequest(url="https://example.com"))

    assert result.ok is True
    assert result.status_code == 404
    assert result.diagnostics.endpoint_reachable is True
    assert result.error_code is None
    assert result.error_message is None
    assert result.retryable is False


def test_probe_web_connectivity_uses_override_proxy_config(monkeypatch) -> None:
    captured_kwargs: dict[str, object] = {}
    fake_client = _FakeProbeClient(
        head_response=httpx.Response(
            200, request=httpx.Request("HEAD", "https://example.com")
        ),
    )

    def fake_create_sync_http_client(**kwargs: object) -> _FakeProbeClient:
        captured_kwargs.update(kwargs)
        return fake_client

    monkeypatch.setattr(
        "relay_teams.net.web_connectivity.create_sync_http_client",
        fake_create_sync_http_client,
    )
    service = WebConnectivityProbeService(
        get_proxy_config=lambda: resolve_proxy_env_config({})
    )

    result = service.probe(
        WebConnectivityProbeRequest(
            url="https://example.com",
            proxy_override=ProxyEnvInput(
                https_proxy="http://override.example:8443",
                no_proxy="",
                proxy_username="alice",
                proxy_password="secret",
            ),
        )
    )

    assert result.ok is True
    assert result.diagnostics.used_proxy is True
    assert captured_kwargs["proxy_config"] == resolve_proxy_env_config(
        {"HTTPS_PROXY": "http://alice:secret@override.example:8443"}
    )
