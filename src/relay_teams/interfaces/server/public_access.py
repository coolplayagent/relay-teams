# -*- coding: utf-8 -*-
from __future__ import annotations

import ipaddress
import os

from fastapi import Request

_UNSAFE_ALLOW_PUBLIC_ACCESS_ENV_KEY = "AGENT_TEAMS_UNSAFE_ALLOW_PUBLIC_ACCESS"
_LOCAL_HOSTNAMES = frozenset(
    {
        "localhost",
        "testserver",
    }
)


def is_public_access_guard_enabled() -> bool:
    return not _is_truthy_env_value(
        os.getenv(_UNSAFE_ALLOW_PUBLIC_ACCESS_ENV_KEY),
    )


def request_uses_public_host(request: Request) -> bool:
    hostname = _normalize_hostname(request.url.hostname)
    if hostname is None:
        return False
    return not is_local_hostname(hostname)


def is_local_hostname(hostname: str) -> bool:
    normalized_host = _normalize_hostname(hostname)
    if normalized_host is None:
        return True
    if normalized_host in _LOCAL_HOSTNAMES:
        return True
    if normalized_host.endswith(".localhost") or normalized_host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def is_public_host_allowed_request(request: Request) -> bool:
    path = request.url.path
    method = request.method.upper()
    if path == "/api/system/health":
        return method in {"GET", "HEAD"}
    if path == "/api/triggers/github/deliveries":
        return method == "POST"
    return False


def public_access_denied_detail() -> str:
    return (
        "Public-host access is disabled for this route. Only /api/system/health "
        "and /api/triggers/github/deliveries are reachable through a public "
        "hostname by default."
    )


def _is_truthy_env_value(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_hostname(hostname: str | None) -> str | None:
    if hostname is None:
        return None
    normalized = hostname.strip().lower().strip("[]")
    return normalized or None
