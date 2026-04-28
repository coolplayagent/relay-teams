# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncGenerator, Generator, Mapping
from datetime import UTC, datetime, timedelta
from threading import Lock

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from relay_teams.net.clients import create_async_http_client, create_sync_http_client
from relay_teams.providers.model_config import (
    DEFAULT_MAAS_APP_ID,
    DEFAULT_MAAS_LOGIN_URL,
    MaaSAuthConfig,
    ProviderType,
)

__all__ = [
    "MAAS_PASSWORD_SECRET_FIELD",
    "MaaSAuthConfig",
    "build_maas_openai_client",
    "clear_maas_token_service_cache",
    "get_maas_token_service",
    "MaaSAuthContext",
    "MaaSLoginError",
    "is_maas_provider",
    "maas_password_secret_field_name",
    "maas_reserved_header_names",
]

MAAS_PASSWORD_SECRET_FIELD = "maas_password"
_MAAS_TOKEN_TTL = timedelta(hours=24)
_MAAS_REFRESH_SKEW = timedelta(hours=1)


class MaaSAuthContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token: str = Field(min_length=1)
    department: str | None = Field(default=None, min_length=1)


class _MaaSTokenRecord:
    def __init__(
        self,
        *,
        auth_context: MaaSAuthContext,
        expires_at: datetime,
    ) -> None:
        self.auth_context = auth_context
        self.expires_at = expires_at


class MaaSLoginError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MaaSTokenService:
    def __init__(self) -> None:
        self._tokens: dict[str, _MaaSTokenRecord] = {}
        self._sync_locks: dict[str, Lock] = {}
        self._async_locks: dict[str, asyncio.Lock] = {}

    def clear(self) -> None:
        self._tokens.clear()
        self._sync_locks.clear()
        self._async_locks.clear()

    def get_token_sync(
        self,
        *,
        auth_config: MaaSAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool = False,
    ) -> str:
        return self.get_auth_context_sync(
            auth_config=auth_config,
            ssl_verify=ssl_verify,
            connect_timeout_seconds=connect_timeout_seconds,
            force_refresh=force_refresh,
        ).token

    def get_auth_context_sync(
        self,
        *,
        auth_config: MaaSAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool = False,
    ) -> MaaSAuthContext:
        cache_key = self._cache_key(auth_config)
        cached = self._tokens.get(cache_key)
        if (
            not force_refresh
            and cached is not None
            and not self._should_refresh(cached)
        ):
            return cached.auth_context
        lock = self._sync_locks.setdefault(cache_key, Lock())
        with lock:
            cached = self._tokens.get(cache_key)
            if (
                not force_refresh
                and cached is not None
                and not self._should_refresh(cached)
            ):
                return cached.auth_context
            record = self._login_sync(
                auth_config=auth_config,
                ssl_verify=ssl_verify,
                connect_timeout_seconds=connect_timeout_seconds,
            )
            self._tokens[cache_key] = record
            return record.auth_context

    async def get_token(
        self,
        *,
        auth_config: MaaSAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool = False,
    ) -> str:
        return (
            await self.get_auth_context(
                auth_config=auth_config,
                ssl_verify=ssl_verify,
                connect_timeout_seconds=connect_timeout_seconds,
                force_refresh=force_refresh,
            )
        ).token

    async def get_auth_context(
        self,
        *,
        auth_config: MaaSAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool = False,
    ) -> MaaSAuthContext:
        cache_key = self._cache_key(auth_config)
        cached = self._tokens.get(cache_key)
        if (
            not force_refresh
            and cached is not None
            and not self._should_refresh(cached)
        ):
            return cached.auth_context
        lock = self._async_locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = self._tokens.get(cache_key)
            if (
                not force_refresh
                and cached is not None
                and not self._should_refresh(cached)
            ):
                return cached.auth_context
            record = await self._login_async(
                auth_config=auth_config,
                ssl_verify=ssl_verify,
                connect_timeout_seconds=connect_timeout_seconds,
            )
            self._tokens[cache_key] = record
            return record.auth_context

    def _cache_key(self, auth_config: MaaSAuthConfig) -> str:
        password = auth_config.password or ""
        raw = "\0".join((auth_config.username, password))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _should_refresh(self, record: _MaaSTokenRecord) -> bool:
        return datetime.now(UTC) + _MAAS_REFRESH_SKEW >= record.expires_at

    def _login_sync(
        self,
        *,
        auth_config: MaaSAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
    ) -> _MaaSTokenRecord:
        with create_sync_http_client(
            ssl_verify=ssl_verify,
            timeout_seconds=connect_timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
        ) as client:
            response = client.post(
                DEFAULT_MAAS_LOGIN_URL,
                headers={"Content-Type": "application/json"},
                json=_maas_login_payload(auth_config),
            )
        return _build_token_record(response)

    async def _login_async(
        self,
        *,
        auth_config: MaaSAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
    ) -> _MaaSTokenRecord:
        async with create_async_http_client(
            ssl_verify=ssl_verify,
            timeout_seconds=connect_timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
        ) as client:
            response = await client.post(
                DEFAULT_MAAS_LOGIN_URL,
                headers={"Content-Type": "application/json"},
                json=_maas_login_payload(auth_config),
            )
        return _build_token_record(response)


class MaaSRequestAuth(httpx.Auth):
    requires_request_body = True

    def __init__(
        self,
        *,
        auth_config: MaaSAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        token_service: MaaSTokenService,
    ) -> None:
        self._auth_config = auth_config
        self._ssl_verify = ssl_verify
        self._connect_timeout_seconds = connect_timeout_seconds
        self._token_service = token_service

    def sync_auth_flow(
        self,
        request: httpx.Request,
    ) -> Generator[httpx.Request, httpx.Response, None]:
        token = self._token_service.get_token_sync(
            auth_config=self._auth_config,
            ssl_verify=self._ssl_verify,
            connect_timeout_seconds=self._connect_timeout_seconds,
        )
        response = yield _clone_request_with_maas_headers(
            request,
            token=token,
            app_id=DEFAULT_MAAS_APP_ID,
        )
        if response.status_code not in {401, 403}:
            return
        response.close()
        retry_token = self._token_service.get_token_sync(
            auth_config=self._auth_config,
            ssl_verify=self._ssl_verify,
            connect_timeout_seconds=self._connect_timeout_seconds,
            force_refresh=True,
        )
        yield _clone_request_with_maas_headers(
            response.request,
            token=retry_token,
            app_id=DEFAULT_MAAS_APP_ID,
        )

    async def async_auth_flow(
        self,
        request: httpx.Request,
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = await self._token_service.get_token(
            auth_config=self._auth_config,
            ssl_verify=self._ssl_verify,
            connect_timeout_seconds=self._connect_timeout_seconds,
        )
        response = yield _clone_request_with_maas_headers(
            request,
            token=token,
            app_id=DEFAULT_MAAS_APP_ID,
        )
        if response.status_code not in {401, 403}:
            return
        await response.aclose()
        retry_token = await self._token_service.get_token(
            auth_config=self._auth_config,
            ssl_verify=self._ssl_verify,
            connect_timeout_seconds=self._connect_timeout_seconds,
            force_refresh=True,
        )
        yield _clone_request_with_maas_headers(
            response.request,
            token=retry_token,
            app_id=DEFAULT_MAAS_APP_ID,
        )


class MaaSAsyncOpenAI(AsyncOpenAI):
    def __init__(
        self,
        *,
        base_url: str,
        auth_config: MaaSAuthConfig,
        default_headers: Mapping[str, str] | None,
        http_client: httpx.AsyncClient,
        connect_timeout_seconds: float,
        ssl_verify: bool | None,
        token_service: MaaSTokenService,
    ) -> None:
        self._maas_request_auth = MaaSRequestAuth(
            auth_config=auth_config,
            ssl_verify=ssl_verify,
            connect_timeout_seconds=connect_timeout_seconds,
            token_service=token_service,
        )
        super().__init__(
            api_key="maas-auth-not-used",
            base_url=base_url,
            default_headers=default_headers,
            http_client=http_client,
            max_retries=0,
        )

    @property
    def auth_headers(self) -> dict[str, str]:
        return {}

    @property
    def custom_auth(self) -> httpx.Auth | None:
        return self._maas_request_auth


def build_maas_openai_client(
    *,
    base_url: str,
    auth_config: MaaSAuthConfig,
    default_headers: Mapping[str, str] | None,
    http_client: httpx.AsyncClient,
    connect_timeout_seconds: float,
    ssl_verify: bool | None,
    token_service: MaaSTokenService | None = None,
) -> AsyncOpenAI:
    return MaaSAsyncOpenAI(
        base_url=base_url,
        auth_config=auth_config,
        default_headers=default_headers,
        http_client=http_client,
        connect_timeout_seconds=connect_timeout_seconds,
        ssl_verify=ssl_verify,
        token_service=get_maas_token_service()
        if token_service is None
        else token_service,
    )


def maas_password_secret_field_name() -> str:
    return MAAS_PASSWORD_SECRET_FIELD


def maas_reserved_header_names() -> frozenset[str]:
    return frozenset({"authorization", "x-auth-token", "app-id"})


def is_maas_provider(provider: ProviderType) -> bool:
    return provider == ProviderType.MAAS


_DEFAULT_MAAS_TOKEN_SERVICE = MaaSTokenService()


def get_maas_token_service() -> MaaSTokenService:
    return _DEFAULT_MAAS_TOKEN_SERVICE


def clear_maas_token_service_cache() -> None:
    _DEFAULT_MAAS_TOKEN_SERVICE.clear()


def _maas_login_payload(auth_config: MaaSAuthConfig) -> dict[str, str]:
    return {
        "requireUserInfo": "true",
        "user": auth_config.username,
        "password": auth_config.password or "",
    }


def _build_token_record(response: httpx.Response) -> _MaaSTokenRecord:
    payload = _response_json(response)
    if response.status_code >= 400:
        raise MaaSLoginError(
            _extract_error_message(payload) or response.text or "MAAS login failed.",
            status_code=response.status_code,
        )
    token = _extract_token(payload)
    if token is None:
        raise MaaSLoginError(
            "MAAS login response did not include cloudDragonTokens.authToken.",
            status_code=response.status_code,
        )
    return _MaaSTokenRecord(
        auth_context=MaaSAuthContext(
            token=token,
            department=_extract_department(payload),
        ),
        expires_at=datetime.now(UTC) + _MAAS_TOKEN_TTL,
    )


def _extract_token(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    cloud_dragon_tokens = payload.get("cloudDragonTokens")
    if not isinstance(cloud_dragon_tokens, dict):
        return None
    token = cloud_dragon_tokens.get("authToken")
    if not isinstance(token, str):
        return None
    normalized = token.strip()
    return normalized or None


def _extract_error_message(payload: object) -> str | None:
    if isinstance(payload, dict):
        for key in ("message", "detail", "error_description"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        if isinstance(error, str) and error.strip():
            return error.strip()
    return None


def _extract_department(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    user_info = payload.get("userInfo")
    if not isinstance(user_info, dict):
        return None
    direct_department = user_info.get("hwDepartName")
    if isinstance(direct_department, str):
        normalized = direct_department.strip()
        if normalized:
            return normalized
    segments: list[str] = []
    for index in range(1, 7):
        segment = user_info.get(f"hwDepartName{index}")
        if not isinstance(segment, str):
            continue
        normalized = segment.strip()
        if normalized:
            segments.append(normalized)
    if len(segments) == 0:
        return None
    return "/".join(segments)


def _response_json(response: httpx.Response) -> object:
    try:
        return response.json()
    except Exception:
        return None


def _clone_request_with_maas_headers(
    request: httpx.Request,
    *,
    token: str,
    app_id: str,
) -> httpx.Request:
    headers = _strip_reserved_headers(dict(request.headers))
    headers["X-Auth-Token"] = token
    headers["app-id"] = app_id or DEFAULT_MAAS_APP_ID
    return httpx.Request(
        method=request.method,
        url=request.url,
        headers=headers,
        content=request.content,
        extensions=dict(request.extensions),
    )


def _strip_reserved_headers(headers: dict[str, str]) -> dict[str, str]:
    reserved = maas_reserved_header_names()
    return {
        name: value
        for name, value in headers.items()
        if name.casefold() not in reserved
    }
