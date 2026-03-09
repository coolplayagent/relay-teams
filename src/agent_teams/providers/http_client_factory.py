# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from functools import cache
import ipaddress

import httpx
from pydantic import BaseModel, ConfigDict
from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT, get_user_agent

from agent_teams.env import extract_proxy_env_vars, load_merged_env_vars

_CONNECT_TIMEOUT_SECONDS = 5
_SSL_VERIFY_KEYS = ("AGENT_TEAMS_LLM_SSL_VERIFY",)
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


class ProxyEnvConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    http_proxy: str | None = None
    https_proxy: str | None = None
    all_proxy: str | None = None
    no_proxy: str | None = None
    verify_ssl: bool = True


def build_llm_http_client(
    *,
    merged_env: Mapping[str, str] | None = None,
) -> httpx.AsyncClient | None:
    resolved_env = load_merged_env_vars() if merged_env is None else merged_env
    proxy_config = _resolve_proxy_env_config(resolved_env)
    if proxy_config is None:
        return None

    client = _cached_llm_http_client(
        http_proxy=proxy_config.http_proxy or "",
        https_proxy=proxy_config.https_proxy or "",
        all_proxy=proxy_config.all_proxy or "",
        no_proxy=proxy_config.no_proxy or "",
        verify_ssl=proxy_config.verify_ssl,
    )
    if client.is_closed:
        _cached_llm_http_client.cache_clear()
        client = _cached_llm_http_client(
            http_proxy=proxy_config.http_proxy or "",
            https_proxy=proxy_config.https_proxy or "",
            all_proxy=proxy_config.all_proxy or "",
            no_proxy=proxy_config.no_proxy or "",
            verify_ssl=proxy_config.verify_ssl,
        )
    return client


def _resolve_proxy_env_config(
    env_values: Mapping[str, str],
) -> ProxyEnvConfig | None:
    proxy_env = extract_proxy_env_vars(env_values)
    http_proxy = proxy_env.get("HTTP_PROXY")
    https_proxy = proxy_env.get("HTTPS_PROXY")
    all_proxy = proxy_env.get("ALL_PROXY")
    no_proxy = proxy_env.get("NO_PROXY")
    verify_ssl = _read_verify_ssl_env(env_values)

    if not any((http_proxy, https_proxy, all_proxy)) and verify_ssl:
        return None

    return ProxyEnvConfig(
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        all_proxy=all_proxy,
        no_proxy=no_proxy,
        verify_ssl=verify_ssl,
    )


@cache
def _cached_llm_http_client(
    *,
    http_proxy: str,
    https_proxy: str,
    all_proxy: str,
    no_proxy: str,
    verify_ssl: bool,
) -> httpx.AsyncClient:
    mounts = _build_proxy_mounts(
        ProxyEnvConfig(
            http_proxy=http_proxy or None,
            https_proxy=https_proxy or None,
            all_proxy=all_proxy or None,
            no_proxy=no_proxy or None,
            verify_ssl=verify_ssl,
        )
    )
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            timeout=DEFAULT_HTTP_TIMEOUT,
            connect=_CONNECT_TIMEOUT_SECONDS,
        ),
        headers={"User-Agent": get_user_agent()},
        trust_env=False,
        verify=verify_ssl,
        mounts=mounts,
    )


def _build_proxy_mounts(
    proxy_config: ProxyEnvConfig,
) -> dict[str, httpx.AsyncBaseTransport | None]:
    no_proxy_patterns = _build_no_proxy_patterns(proxy_config.no_proxy)
    if no_proxy_patterns is None:
        return {}

    mounts: dict[str, httpx.AsyncBaseTransport | None] = {}
    http_proxy = proxy_config.http_proxy or proxy_config.all_proxy
    https_proxy = (
        proxy_config.https_proxy or proxy_config.http_proxy or proxy_config.all_proxy
    )

    if http_proxy:
        mounts["http://"] = _build_proxy_transport(
            http_proxy,
            verify_ssl=proxy_config.verify_ssl,
        )
    if https_proxy:
        mounts["https://"] = _build_proxy_transport(
            https_proxy,
            verify_ssl=proxy_config.verify_ssl,
        )

    for pattern in no_proxy_patterns:
        mounts[pattern] = None

    return mounts


def _build_proxy_transport(
    proxy_url: str,
    *,
    verify_ssl: bool,
) -> httpx.AsyncHTTPTransport:
    normalized_proxy_url = proxy_url if "://" in proxy_url else f"http://{proxy_url}"
    return httpx.AsyncHTTPTransport(
        proxy=normalized_proxy_url,
        trust_env=False,
        verify=verify_ssl,
    )


def _build_no_proxy_patterns(no_proxy: str | None) -> tuple[str, ...] | None:
    if not no_proxy:
        return ()

    patterns: list[str] = []
    for raw_host in no_proxy.split(","):
        host = raw_host.strip()
        if not host:
            continue
        if host == "*":
            return None
        if "://" in host:
            patterns.append(host)
            continue
        if _is_ipv4_host(host):
            patterns.append(f"all://{host}")
            continue
        if _is_ipv6_host(host):
            patterns.append(f"all://[{host}]")
            continue
        if host.lower() == "localhost":
            patterns.append("all://localhost")
            continue
        patterns.append(f"all://*{host}")

    return tuple(patterns)


def _read_verify_ssl_env(env_values: Mapping[str, str]) -> bool:
    raw_value = env_values.get("AGENT_TEAMS_LLM_SSL_VERIFY")
    if raw_value is None:
        return True

    normalized = raw_value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(
        "Invalid AGENT_TEAMS_LLM_SSL_VERIFY value. "
        "Use one of: true/false, yes/no, on/off, 1/0."
    )


def _is_ipv4_host(host: str) -> bool:
    try:
        ipaddress.IPv4Address(host.split("/")[0])
    except ValueError:
        return False
    return True


def _is_ipv6_host(host: str) -> bool:
    try:
        ipaddress.IPv6Address(host.split("/")[0])
    except ValueError:
        return False
    return True
