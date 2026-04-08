# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from time import perf_counter
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from relay_teams.env.proxy_env import (
    ProxyEnvConfig,
    ProxyEnvInput,
    proxy_applies_to_url,
)
from relay_teams.net.clients import create_sync_http_client

_MAX_WEB_PROBE_TIMEOUT_MS = 300_000
_HEAD_FALLBACK_STATUS_CODES = {405, 501}


class WebConnectivityProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)
    timeout_ms: int | None = Field(default=None, ge=1000, le=_MAX_WEB_PROBE_TIMEOUT_MS)
    proxy_override: ProxyEnvInput | None = None


class WebConnectivityProbeDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint_reachable: bool
    used_proxy: bool
    redirected: bool


class WebConnectivityProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    url: str = Field(min_length=1)
    final_url: str = Field(min_length=1)
    status_code: int | None = Field(default=None, ge=100, le=599)
    latency_ms: int = Field(ge=0)
    checked_at: datetime
    used_method: Literal["HEAD", "GET"]
    diagnostics: WebConnectivityProbeDiagnostics
    retryable: bool = False
    error_code: str | None = None
    error_message: str | None = None


class WebConnectivityProbeService:
    def __init__(
        self,
        *,
        get_proxy_config: Callable[[], ProxyEnvConfig],
    ) -> None:
        self._get_proxy_config: Callable[[], ProxyEnvConfig] = get_proxy_config

    def probe(self, request: WebConnectivityProbeRequest) -> WebConnectivityProbeResult:
        parsed = urlsplit(request.url)
        scheme = parsed.scheme.strip().lower()
        if scheme not in {"http", "https"}:
            raise ValueError("Web probe only supports http and https URLs.")

        timeout_seconds = (
            15.0 if request.timeout_ms is None else request.timeout_ms / 1000.0
        )
        proxy_config = (
            request.proxy_override.to_config()
            if request.proxy_override is not None
            else self._get_proxy_config()
        )
        used_proxy = proxy_applies_to_url(request.url, proxy_config)
        checked_at = datetime.now(timezone.utc)
        started = perf_counter()

        with create_sync_http_client(
            proxy_config=proxy_config,
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=timeout_seconds,
            follow_redirects=True,
        ) as client:
            try:
                response = client.head(request.url)
                method = "HEAD"
                if response.status_code in _HEAD_FALLBACK_STATUS_CODES:
                    response = client.get(request.url)
                    method = "GET"
            except httpx.TimeoutException as exc:
                return self._build_transport_error_result(
                    url=request.url,
                    checked_at=checked_at,
                    started=started,
                    used_proxy=used_proxy,
                    method="HEAD",
                    error_code="network_timeout",
                    error_message=str(exc) or "Connection timed out.",
                )
            except httpx.ProxyError as exc:
                return self._build_transport_error_result(
                    url=request.url,
                    checked_at=checked_at,
                    started=started,
                    used_proxy=used_proxy,
                    method="HEAD",
                    error_code="proxy_error",
                    error_message=str(exc) or "Proxy request failed.",
                )
            except httpx.ConnectError as exc:
                error_code = (
                    "dns_error"
                    if "name or service not known" in str(exc).lower()
                    else "network_error"
                )
                return self._build_transport_error_result(
                    url=request.url,
                    checked_at=checked_at,
                    started=started,
                    used_proxy=used_proxy,
                    method="HEAD",
                    error_code=error_code,
                    error_message=str(exc) or "Failed to reach target host.",
                )
            except httpx.RequestError as exc:
                return self._build_transport_error_result(
                    url=request.url,
                    checked_at=checked_at,
                    started=started,
                    used_proxy=used_proxy,
                    method="HEAD",
                    error_code="network_error",
                    error_message=str(exc) or "Failed to reach target URL.",
                )

        latency_ms = max(0, int((perf_counter() - started) * 1000))
        redirected = bool(response.history)
        ok = True
        return WebConnectivityProbeResult(
            ok=ok,
            url=request.url,
            final_url=str(response.url),
            status_code=response.status_code,
            latency_ms=latency_ms,
            checked_at=checked_at,
            used_method=method,
            diagnostics=WebConnectivityProbeDiagnostics(
                endpoint_reachable=True,
                used_proxy=used_proxy,
                redirected=redirected,
            ),
            retryable=False,
            error_code=None,
            error_message=None,
        )

    def _build_transport_error_result(
        self,
        *,
        url: str,
        checked_at: datetime,
        started: float,
        used_proxy: bool,
        method: Literal["HEAD", "GET"],
        error_code: str,
        error_message: str,
    ) -> WebConnectivityProbeResult:
        return WebConnectivityProbeResult(
            ok=False,
            url=url,
            final_url=url,
            status_code=None,
            latency_ms=max(0, int((perf_counter() - started) * 1000)),
            checked_at=checked_at,
            used_method=method,
            diagnostics=WebConnectivityProbeDiagnostics(
                endpoint_reachable=False,
                used_proxy=used_proxy,
                redirected=False,
            ),
            retryable=error_code in {"network_timeout", "network_error", "proxy_error"},
            error_code=error_code,
            error_message=error_message,
        )
