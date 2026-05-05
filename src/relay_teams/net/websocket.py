# -*- coding: utf-8 -*-
from __future__ import annotations

import ssl

from relay_teams.env.proxy_env import (
    ProxyEnvConfig,
    load_proxy_env_config,
    proxy_applies_to_url,
    resolve_ssl_verify,
)


def build_websocket_ssl_context(
    url: str,
    *,
    proxy_config: ProxyEnvConfig | None = None,
) -> ssl.SSLContext | None:
    if not url.startswith("wss://"):
        return None
    resolved_proxy_config = _resolve_proxy_config(proxy_config)
    ssl_context = ssl.create_default_context()
    if resolve_ssl_verify(proxy_config=resolved_proxy_config):
        return ssl_context
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    return ssl_context


def resolve_websocket_proxy_url(
    url: str,
    *,
    proxy_config: ProxyEnvConfig | None = None,
) -> str | None:
    resolved_proxy_config = _resolve_proxy_config(proxy_config)
    if not proxy_applies_to_url(_httpish_url_for_websocket(url), resolved_proxy_config):
        return None
    if url.startswith("wss://"):
        return (
            resolved_proxy_config.https_proxy
            or resolved_proxy_config.http_proxy
            or resolved_proxy_config.all_proxy
        )
    if url.startswith("ws://"):
        return resolved_proxy_config.http_proxy or resolved_proxy_config.all_proxy
    return None


def _resolve_proxy_config(proxy_config: ProxyEnvConfig | None) -> ProxyEnvConfig:
    return load_proxy_env_config() if proxy_config is None else proxy_config


def _httpish_url_for_websocket(url: str) -> str:
    if url.startswith("wss://"):
        return f"https://{url.removeprefix('wss://')}"
    if url.startswith("ws://"):
        return f"http://{url.removeprefix('ws://')}"
    return url
