# -*- coding: utf-8 -*-
from __future__ import annotations

import ipaddress

import httpx


def normalize_public_base_url(value: str | None) -> str | None:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return None
    parsed = _parse_http_url(
        normalized,
        invalid_message="Webhook base URL must use http or https.",
    )
    host = _require_public_host(
        parsed,
        invalid_message="Webhook base URL must be publicly reachable.",
    )
    _ = host
    path = (parsed.path or "").rstrip("/")
    sanitized = parsed.copy_with(
        path=path,
        query=None,
        fragment=None,
        username=None,
        password=None,
    )
    return str(sanitized)


def is_public_http_url(value: str | None) -> bool:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return False
    try:
        parsed = _parse_http_url(
            normalized,
            invalid_message="URL must use http or https.",
        )
        _require_public_host(
            parsed,
            invalid_message="URL must be publicly reachable.",
        )
    except ValueError:
        return False
    return True


def build_public_base_url_path(base_url: str, absolute_path: str) -> str:
    normalized_base_url = normalize_public_base_url(base_url)
    if normalized_base_url is None:
        raise ValueError("Webhook base URL must be configured.")
    normalized_path = absolute_path.strip()
    if not normalized_path.startswith("/"):
        raise ValueError("absolute_path must start with '/'")
    parsed = httpx.URL(normalized_base_url)
    base_path = (parsed.path or "").rstrip("/")
    combined_path = f"{base_path}{normalized_path}"
    sanitized = parsed.copy_with(path=combined_path)
    return str(sanitized)


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _parse_http_url(value: str, *, invalid_message: str) -> httpx.URL:
    try:
        parsed = httpx.URL(value)
    except httpx.InvalidURL as exc:
        raise ValueError(invalid_message) from exc
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(invalid_message)
    if parsed.host is None:
        raise ValueError(invalid_message)
    if parsed.username or parsed.password:
        raise ValueError(invalid_message)
    return parsed


def _require_public_host(parsed: httpx.URL, *, invalid_message: str) -> str:
    host = parsed.host.rstrip(".").lower()
    if host == "localhost" or host.endswith(".local"):
        raise ValueError(invalid_message)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise ValueError(invalid_message) from None
        return host
    if _is_private_or_local_address(address):
        raise ValueError(invalid_message)
    return host


def _is_private_or_local_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )
