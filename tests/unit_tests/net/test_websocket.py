# -*- coding: utf-8 -*-
from __future__ import annotations

import ssl

from relay_teams.env.proxy_env import ProxyEnvConfig, resolve_proxy_env_config
from relay_teams.net.websocket import (
    build_websocket_ssl_context,
    resolve_websocket_proxy_url,
)


def test_resolve_websocket_proxy_url_uses_https_proxy_for_wss() -> None:
    proxy_config = resolve_proxy_env_config(
        {
            "HTTP_PROXY": "http://http-proxy.example:8080",
            "HTTPS_PROXY": "http://https-proxy.example:8443",
        }
    )

    proxy_url = resolve_websocket_proxy_url(
        "wss://events.example.com/ws",
        proxy_config=proxy_config,
    )

    assert proxy_url == "http://https-proxy.example:8443"


def test_resolve_websocket_proxy_url_uses_http_proxy_for_ws() -> None:
    proxy_config = resolve_proxy_env_config(
        {"HTTP_PROXY": "http://http-proxy.example:8080"}
    )

    proxy_url = resolve_websocket_proxy_url(
        "ws://events.example.com/ws",
        proxy_config=proxy_config,
    )

    assert proxy_url == "http://http-proxy.example:8080"


def test_resolve_websocket_proxy_url_respects_no_proxy() -> None:
    proxy_config = resolve_proxy_env_config(
        {
            "HTTPS_PROXY": "http://https-proxy.example:8443",
            "NO_PROXY": "events.example.com",
        }
    )

    proxy_url = resolve_websocket_proxy_url(
        "wss://events.example.com/ws",
        proxy_config=proxy_config,
    )

    assert proxy_url is None


def test_build_websocket_ssl_context_uses_runtime_ssl_policy() -> None:
    disabled_context = build_websocket_ssl_context(
        "wss://events.example.com/ws",
        proxy_config=ProxyEnvConfig(ssl_verify=False),
    )
    enabled_context = build_websocket_ssl_context(
        "wss://events.example.com/ws",
        proxy_config=ProxyEnvConfig(ssl_verify=True),
    )

    assert disabled_context is not None
    assert disabled_context.verify_mode == ssl.CERT_NONE
    assert disabled_context.check_hostname is False
    assert enabled_context is not None
    assert enabled_context.verify_mode == ssl.CERT_REQUIRED
    assert enabled_context.check_hostname is True


def test_build_websocket_ssl_context_skips_plain_ws() -> None:
    ssl_context = build_websocket_ssl_context(
        "ws://events.example.com/ws",
        proxy_config=ProxyEnvConfig(ssl_verify=True),
    )

    assert ssl_context is None
