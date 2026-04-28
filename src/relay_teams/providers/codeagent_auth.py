# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncGenerator, Generator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_hex, token_urlsafe
from threading import Lock
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from relay_teams.net.clients import create_async_http_client, create_sync_http_client
from relay_teams.providers.maas_auth import (
    MaaSAuthConfig,
    MaaSLoginError,
    get_maas_token_service,
)
from relay_teams.providers.model_config import (
    CodeAgentAuthMethod,
    CodeAgentAuthConfig,
    DEFAULT_CODEAGENT_SSO_BASE_URL,
)
from relay_teams.secrets import get_secret_store

__all__ = [
    "CODEAGENT_ACCESS_TOKEN_SECRET_FIELD",
    "CODEAGENT_PASSWORD_SECRET_FIELD",
    "CODEAGENT_REFRESH_TOKEN_SECRET_FIELD",
    "CodeAgentOAuthError",
    "CodeAgentOAuthSession",
    "CodeAgentOAuthTokenResult",
    "CodeAgentTokenService",
    "build_codeagent_request_headers",
    "build_codeagent_authorization_url",
    "build_codeagent_openai_client",
    "clear_codeagent_oauth_session_store",
    "clear_codeagent_token_service_cache",
    "codeagent_access_token_secret_field_name",
    "codeagent_password_secret_field_name",
    "codeagent_refresh_token_secret_field_name",
    "consume_codeagent_oauth_tokens",
    "create_codeagent_oauth_session",
    "get_codeagent_oauth_session",
    "get_codeagent_oauth_session_by_state",
    "get_codeagent_oauth_tokens",
    "get_codeagent_token_service",
    "is_codeagent_chat_completion_request",
    "save_codeagent_oauth_tokens",
    "save_codeagent_oauth_tokens_for_session",
]

CODEAGENT_ACCESS_TOKEN_SECRET_FIELD = "codeagent_access_token"
CODEAGENT_PASSWORD_SECRET_FIELD = "codeagent_password"
CODEAGENT_REFRESH_TOKEN_SECRET_FIELD = "codeagent_refresh_token"
_CODEAGENT_TOKEN_TTL = timedelta(hours=1)
_CODEAGENT_REFRESH_SKEW = timedelta(minutes=5)
_CODEAGENT_OAUTH_SESSION_TTL = timedelta(minutes=30)
_MODEL_PROFILE_SECRET_NAMESPACE = "model_profile"


class CodeAgentOAuthTokenResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)
    expires_at: datetime


class CodeAgentOAuthSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    auth_session_id: str = Field(min_length=1)
    state: str = Field(min_length=1)
    client_code: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    scope_resource: str = Field(min_length=1)
    callback_url: str = Field(min_length=1)
    expires_at: datetime
    completed: bool = False
    error_message: str | None = None
    has_access_token: bool = False
    has_refresh_token: bool = False


class _CodeAgentTokenRecord:
    def __init__(
        self,
        *,
        token_result: CodeAgentOAuthTokenResult,
    ) -> None:
        self.token_result = token_result


class CodeAgentOAuthError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code

    @property
    def auth_invalid(self) -> bool:
        if self.status_code in {401, 403}:
            return True
        if self.error_code == "DEV.00000001":
            return True
        normalized_message = str(self).strip()
        return normalized_message == "未识别到用户认证信息"


class CodeAgentTokenService:
    def __init__(self) -> None:
        self._tokens: dict[str, _CodeAgentTokenRecord] = {}
        self._sync_locks: dict[str, Lock] = {}
        self._async_locks: dict[str, asyncio.Lock] = {}

    def clear(self) -> None:
        self._tokens.clear()
        self._sync_locks.clear()
        self._async_locks.clear()

    def get_token_sync(
        self,
        *,
        base_url: str,
        auth_config: CodeAgentAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool = False,
    ) -> str:
        return self.get_token_result_sync(
            base_url=base_url,
            auth_config=auth_config,
            ssl_verify=ssl_verify,
            connect_timeout_seconds=connect_timeout_seconds,
            force_refresh=force_refresh,
        ).access_token

    def get_token_result_sync(
        self,
        *,
        base_url: str,
        auth_config: CodeAgentAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool = False,
    ) -> CodeAgentOAuthTokenResult:
        cache_key = self._cache_key(base_url=base_url, auth_config=auth_config)
        cached = self._tokens.get(cache_key)
        if (
            not force_refresh
            and cached is not None
            and not self._should_refresh(cached.token_result)
        ):
            return cached.token_result
        lock = self._sync_locks.setdefault(cache_key, Lock())
        with lock:
            cached = self._tokens.get(cache_key)
            if (
                not force_refresh
                and cached is not None
                and not self._should_refresh(cached.token_result)
            ):
                return cached.token_result
            if auth_config.auth_method == CodeAgentAuthMethod.PASSWORD:
                result = self._login_with_password_sync(
                    auth_config=auth_config,
                    ssl_verify=ssl_verify,
                    connect_timeout_seconds=connect_timeout_seconds,
                    force_refresh=force_refresh,
                )
                self._store_token_result(
                    cache_key=cache_key,
                    auth_config=auth_config,
                    token_result=result,
                )
                return result
            config_token_result = self._token_result_from_config(auth_config)
            if not force_refresh and config_token_result is not None:
                self._tokens[cache_key] = _CodeAgentTokenRecord(
                    token_result=config_token_result
                )
                return config_token_result
            result = self.refresh_token_sync(
                base_url=base_url,
                auth_config=self._build_refresh_auth_config(
                    auth_config=auth_config,
                    cached=cached,
                ),
                ssl_verify=ssl_verify,
                connect_timeout_seconds=connect_timeout_seconds,
            )
            self._store_token_result(
                cache_key=cache_key,
                auth_config=auth_config,
                token_result=result,
            )
            return result

    async def get_token(
        self,
        *,
        base_url: str,
        auth_config: CodeAgentAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool = False,
    ) -> str:
        return (
            await self.get_token_result(
                base_url=base_url,
                auth_config=auth_config,
                ssl_verify=ssl_verify,
                connect_timeout_seconds=connect_timeout_seconds,
                force_refresh=force_refresh,
            )
        ).access_token

    async def get_token_result(
        self,
        *,
        base_url: str,
        auth_config: CodeAgentAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool = False,
    ) -> CodeAgentOAuthTokenResult:
        cache_key = self._cache_key(base_url=base_url, auth_config=auth_config)
        cached = self._tokens.get(cache_key)
        if (
            not force_refresh
            and cached is not None
            and not self._should_refresh(cached.token_result)
        ):
            return cached.token_result
        lock = self._async_locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = self._tokens.get(cache_key)
            if (
                not force_refresh
                and cached is not None
                and not self._should_refresh(cached.token_result)
            ):
                return cached.token_result
            if auth_config.auth_method == CodeAgentAuthMethod.PASSWORD:
                result = await self._login_with_password(
                    auth_config=auth_config,
                    ssl_verify=ssl_verify,
                    connect_timeout_seconds=connect_timeout_seconds,
                    force_refresh=force_refresh,
                )
                self._store_token_result(
                    cache_key=cache_key,
                    auth_config=auth_config,
                    token_result=result,
                )
                return result
            config_token_result = self._token_result_from_config(auth_config)
            if not force_refresh and config_token_result is not None:
                self._tokens[cache_key] = _CodeAgentTokenRecord(
                    token_result=config_token_result
                )
                return config_token_result
            result = await self.refresh_token(
                base_url=base_url,
                auth_config=self._build_refresh_auth_config(
                    auth_config=auth_config,
                    cached=cached,
                ),
                ssl_verify=ssl_verify,
                connect_timeout_seconds=connect_timeout_seconds,
            )
            self._store_token_result(
                cache_key=cache_key,
                auth_config=auth_config,
                token_result=result,
            )
            return result

    def refresh_token_sync(
        self,
        *,
        base_url: str,
        auth_config: CodeAgentAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
    ) -> CodeAgentOAuthTokenResult:
        if auth_config.refresh_token is None:
            raise CodeAgentOAuthError(
                "CodeAgent refresh token is not configured.",
                status_code=None,
            )
        with create_sync_http_client(
            ssl_verify=ssl_verify,
            timeout_seconds=connect_timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
        ) as client:
            response = client.post(
                _refresh_token_url(base_url),
                json=_refresh_token_payload(auth_config),
                headers={"Content-Type": "application/json"},
            )
        return _build_token_result(
            response, fallback_refresh_token=auth_config.refresh_token
        )

    async def refresh_token(
        self,
        *,
        base_url: str,
        auth_config: CodeAgentAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
    ) -> CodeAgentOAuthTokenResult:
        if auth_config.refresh_token is None:
            raise CodeAgentOAuthError(
                "CodeAgent refresh token is not configured.",
                status_code=None,
            )
        async with create_async_http_client(
            ssl_verify=ssl_verify,
            timeout_seconds=connect_timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
        ) as client:
            response = await client.post(
                _refresh_token_url(base_url),
                json=_refresh_token_payload(auth_config),
                headers={"Content-Type": "application/json"},
            )
        return _build_token_result(
            response, fallback_refresh_token=auth_config.refresh_token
        )

    def poll_token_sync(
        self,
        *,
        session: CodeAgentOAuthSession,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
    ) -> CodeAgentOAuthTokenResult | None:
        with create_sync_http_client(
            ssl_verify=ssl_verify,
            timeout_seconds=connect_timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
        ) as client:
            response = client.post(
                _get_token_url(session.base_url),
                json={
                    "clientCode": session.client_code,
                    "redirectUrl": session.callback_url,
                },
                headers={"Content-Type": "application/json"},
            )
        return _build_polled_token_result(response)

    def _cache_key(self, *, base_url: str, auth_config: CodeAgentAuthConfig) -> str:
        cache_discriminator = (
            auth_config.secret_owner_id
            or auth_config.oauth_session_id
            or auth_config.username
            or ""
        )
        raw = "\0".join(
            (
                base_url.strip(),
                auth_config.auth_method.value,
                auth_config.client_id,
                auth_config.scope,
                auth_config.scope_resource,
                cache_discriminator,
                auth_config.username or "",
                auth_config.password or "",
                auth_config.refresh_token or "",
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _login_with_password_sync(
        *,
        auth_config: CodeAgentAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool,
    ) -> CodeAgentOAuthTokenResult:
        if auth_config.username is None or auth_config.password is None:
            raise CodeAgentOAuthError(
                "CodeAgent username/password is not configured.",
                status_code=None,
            )
        try:
            auth_context = get_maas_token_service().get_auth_context_sync(
                auth_config=MaaSAuthConfig(
                    username=auth_config.username,
                    password=auth_config.password,
                ),
                ssl_verify=ssl_verify,
                connect_timeout_seconds=connect_timeout_seconds,
                force_refresh=force_refresh,
            )
        except MaaSLoginError as exc:
            raise CodeAgentOAuthError(
                str(exc) or "CodeAgent password login failed.",
                status_code=exc.status_code,
            ) from exc
        return CodeAgentOAuthTokenResult(
            access_token=auth_context.token,
            refresh_token=auth_context.token,
            expires_at=datetime.now(UTC) + _CODEAGENT_TOKEN_TTL,
        )

    @staticmethod
    async def _login_with_password(
        *,
        auth_config: CodeAgentAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool,
    ) -> CodeAgentOAuthTokenResult:
        if auth_config.username is None or auth_config.password is None:
            raise CodeAgentOAuthError(
                "CodeAgent username/password is not configured.",
                status_code=None,
            )
        try:
            auth_context = await get_maas_token_service().get_auth_context(
                auth_config=MaaSAuthConfig(
                    username=auth_config.username,
                    password=auth_config.password,
                ),
                ssl_verify=ssl_verify,
                connect_timeout_seconds=connect_timeout_seconds,
                force_refresh=force_refresh,
            )
        except MaaSLoginError as exc:
            raise CodeAgentOAuthError(
                str(exc) or "CodeAgent password login failed.",
                status_code=exc.status_code,
            ) from exc
        return CodeAgentOAuthTokenResult(
            access_token=auth_context.token,
            refresh_token=auth_context.token,
            expires_at=datetime.now(UTC) + _CODEAGENT_TOKEN_TTL,
        )

    def _should_refresh(self, token_result: CodeAgentOAuthTokenResult) -> bool:
        return datetime.now(UTC) + _CODEAGENT_REFRESH_SKEW >= token_result.expires_at

    def _build_refresh_auth_config(
        self,
        *,
        auth_config: CodeAgentAuthConfig,
        cached: _CodeAgentTokenRecord | None,
    ) -> CodeAgentAuthConfig:
        if cached is None:
            return auth_config
        return auth_config.model_copy(
            update={
                "access_token": cached.token_result.access_token,
                "refresh_token": cached.token_result.refresh_token,
            }
        )

    def _store_token_result(
        self,
        *,
        cache_key: str,
        auth_config: CodeAgentAuthConfig,
        token_result: CodeAgentOAuthTokenResult,
    ) -> None:
        self._tokens[cache_key] = _CodeAgentTokenRecord(token_result=token_result)
        if auth_config.auth_method == CodeAgentAuthMethod.PASSWORD:
            return
        oauth_session_id = auth_config.oauth_session_id
        if oauth_session_id is None:
            _persist_codeagent_profile_tokens(
                auth_config=auth_config,
                token_result=token_result,
            )
            return
        session = get_codeagent_oauth_session(oauth_session_id)
        if session is None:
            _persist_codeagent_profile_tokens(
                auth_config=auth_config,
                token_result=token_result,
            )
            return
        save_codeagent_oauth_tokens_for_session(
            auth_session_id=oauth_session_id,
            token_result=token_result,
        )
        _persist_codeagent_profile_tokens(
            auth_config=auth_config,
            token_result=token_result,
        )

    def _token_result_from_config(
        self,
        auth_config: CodeAgentAuthConfig,
    ) -> CodeAgentOAuthTokenResult | None:
        if auth_config.auth_method == CodeAgentAuthMethod.PASSWORD:
            return None
        if auth_config.access_token is None or auth_config.refresh_token is None:
            return None
        return CodeAgentOAuthTokenResult(
            access_token=auth_config.access_token,
            refresh_token=auth_config.refresh_token,
            expires_at=datetime.now(UTC) + _CODEAGENT_TOKEN_TTL,
        )


class CodeAgentRequestAuth(httpx.Auth):
    requires_request_body = True

    def __init__(
        self,
        *,
        base_url: str,
        auth_config: CodeAgentAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        token_service: CodeAgentTokenService,
    ) -> None:
        self._base_url = base_url
        self._auth_config = auth_config
        self._ssl_verify = ssl_verify
        self._connect_timeout_seconds = connect_timeout_seconds
        self._token_service = token_service

    def sync_auth_flow(
        self,
        request: httpx.Request,
    ) -> Generator[httpx.Request, httpx.Response, None]:
        token = self._token_service.get_token_sync(
            base_url=self._base_url,
            auth_config=self._auth_config,
            ssl_verify=self._ssl_verify,
            connect_timeout_seconds=self._connect_timeout_seconds,
        )
        response = yield _clone_request_with_codeagent_headers(
            request,
            base_url=self._base_url,
            token=token,
        )
        if response.status_code not in {401, 403}:
            return
        response.close()
        retry_token = self._token_service.get_token_sync(
            base_url=self._base_url,
            auth_config=self._auth_config,
            ssl_verify=self._ssl_verify,
            connect_timeout_seconds=self._connect_timeout_seconds,
            force_refresh=True,
        )
        yield _clone_request_with_codeagent_headers(
            response.request,
            base_url=self._base_url,
            token=retry_token,
        )

    async def async_auth_flow(
        self,
        request: httpx.Request,
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = await self._token_service.get_token(
            base_url=self._base_url,
            auth_config=self._auth_config,
            ssl_verify=self._ssl_verify,
            connect_timeout_seconds=self._connect_timeout_seconds,
        )
        response = yield _clone_request_with_codeagent_headers(
            request,
            base_url=self._base_url,
            token=token,
        )
        if response.status_code not in {401, 403}:
            return
        await response.aclose()
        retry_token = await self._token_service.get_token(
            base_url=self._base_url,
            auth_config=self._auth_config,
            ssl_verify=self._ssl_verify,
            connect_timeout_seconds=self._connect_timeout_seconds,
            force_refresh=True,
        )
        yield _clone_request_with_codeagent_headers(
            response.request,
            base_url=self._base_url,
            token=retry_token,
        )


class CodeAgentAsyncOpenAI(AsyncOpenAI):
    def __init__(
        self,
        *,
        base_url: str,
        auth_config: CodeAgentAuthConfig,
        default_headers: Mapping[str, str] | None,
        http_client: httpx.AsyncClient,
        connect_timeout_seconds: float,
        ssl_verify: bool | None,
        token_service: CodeAgentTokenService,
    ) -> None:
        self._codeagent_request_auth = CodeAgentRequestAuth(
            base_url=base_url,
            auth_config=auth_config,
            ssl_verify=ssl_verify,
            connect_timeout_seconds=connect_timeout_seconds,
            token_service=token_service,
        )
        super().__init__(
            api_key="codeagent-auth-not-used",
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
        return self._codeagent_request_auth


def build_codeagent_openai_client(
    *,
    base_url: str,
    auth_config: CodeAgentAuthConfig,
    default_headers: Mapping[str, str] | None,
    http_client: httpx.AsyncClient,
    connect_timeout_seconds: float,
    ssl_verify: bool | None,
    token_service: CodeAgentTokenService | None = None,
) -> AsyncOpenAI:
    return CodeAgentAsyncOpenAI(
        base_url=base_url,
        auth_config=auth_config,
        default_headers=default_headers,
        http_client=http_client,
        connect_timeout_seconds=connect_timeout_seconds,
        ssl_verify=ssl_verify,
        token_service=(
            get_codeagent_token_service() if token_service is None else token_service
        ),
    )


def build_codeagent_authorization_url(
    *,
    base_url: str,
    client_id: str,
    scope: str,
    scope_resource: str,
    redirect_url: str,
) -> str:
    _ = base_url
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_url,
            "scope": scope,
            "response_type": "code",
            "scope_resource": scope_resource,
        }
    )
    return f"{_authorize_url()}?{query}"


def create_codeagent_oauth_session(
    *,
    base_url: str,
    client_id: str,
    scope: str,
    scope_resource: str,
) -> CodeAgentOAuthSession:
    client_code = _generate_client_code()
    callback_url = _codeagent_callback_url(
        base_url=base_url,
        client_code=client_code,
    )
    session = CodeAgentOAuthSession(
        auth_session_id=token_urlsafe(18),
        state=client_code,
        client_code=client_code,
        base_url=base_url,
        client_id=client_id,
        scope=scope,
        scope_resource=scope_resource,
        callback_url=callback_url,
        expires_at=datetime.now(UTC) + _CODEAGENT_OAUTH_SESSION_TTL,
    )
    _CODEAGENT_OAUTH_SESSION_STORE.save(session)
    return session


def get_codeagent_oauth_session(auth_session_id: str) -> CodeAgentOAuthSession | None:
    return _CODEAGENT_OAUTH_SESSION_STORE.get_by_session_id(auth_session_id)


def get_codeagent_oauth_session_by_state(state: str) -> CodeAgentOAuthSession | None:
    return _CODEAGENT_OAUTH_SESSION_STORE.get_by_state(state)


def save_codeagent_oauth_tokens(
    *,
    state: str,
    token_result: CodeAgentOAuthTokenResult,
) -> CodeAgentOAuthSession:
    return _CODEAGENT_OAUTH_SESSION_STORE.complete_by_state(
        state=state,
        token_result=token_result,
    )


def save_codeagent_oauth_tokens_for_session(
    *,
    auth_session_id: str,
    token_result: CodeAgentOAuthTokenResult,
) -> CodeAgentOAuthSession:
    return _CODEAGENT_OAUTH_SESSION_STORE.complete_by_session_id(
        auth_session_id=auth_session_id,
        token_result=token_result,
    )


def consume_codeagent_oauth_tokens(
    auth_session_id: str,
) -> CodeAgentOAuthTokenResult | None:
    return _CODEAGENT_OAUTH_SESSION_STORE.consume_tokens(auth_session_id)


def get_codeagent_oauth_tokens(
    auth_session_id: str,
) -> CodeAgentOAuthTokenResult | None:
    return _CODEAGENT_OAUTH_SESSION_STORE.get_tokens(auth_session_id)


def clear_codeagent_oauth_session_store() -> None:
    _CODEAGENT_OAUTH_SESSION_STORE.clear()


def get_codeagent_token_service() -> CodeAgentTokenService:
    return _DEFAULT_CODEAGENT_TOKEN_SERVICE


def clear_codeagent_token_service_cache() -> None:
    _DEFAULT_CODEAGENT_TOKEN_SERVICE.clear()


def codeagent_access_token_secret_field_name() -> str:
    return CODEAGENT_ACCESS_TOKEN_SECRET_FIELD


def codeagent_password_secret_field_name() -> str:
    return CODEAGENT_PASSWORD_SECRET_FIELD


def codeagent_refresh_token_secret_field_name() -> str:
    return CODEAGENT_REFRESH_TOKEN_SECRET_FIELD


def is_codeagent_chat_completion_request(request: httpx.Request) -> bool:
    return request.url.path.rstrip("/").endswith("/chat/completions")


class _CodeAgentOAuthSessionStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._sessions_by_id: dict[str, CodeAgentOAuthSession] = {}
        self._session_id_by_state: dict[str, str] = {}
        self._tokens_by_session_id: dict[str, CodeAgentOAuthTokenResult] = {}

    def clear(self) -> None:
        with self._lock:
            self._sessions_by_id.clear()
            self._session_id_by_state.clear()
            self._tokens_by_session_id.clear()

    def save(self, session: CodeAgentOAuthSession) -> None:
        with self._lock:
            self._purge_expired_locked()
            self._sessions_by_id[session.auth_session_id] = session
            self._session_id_by_state[session.state] = session.auth_session_id

    def get_by_session_id(self, auth_session_id: str) -> CodeAgentOAuthSession | None:
        with self._lock:
            self._purge_expired_locked()
            return self._sessions_by_id.get(auth_session_id)

    def get_by_state(self, state: str) -> CodeAgentOAuthSession | None:
        with self._lock:
            self._purge_expired_locked()
            session_id = self._session_id_by_state.get(state)
            if session_id is None:
                return None
            return self._sessions_by_id.get(session_id)

    def complete_by_state(
        self,
        *,
        state: str,
        token_result: CodeAgentOAuthTokenResult,
    ) -> CodeAgentOAuthSession:
        with self._lock:
            self._purge_expired_locked()
            session_id = self._session_id_by_state.get(state)
            if session_id is None:
                raise CodeAgentOAuthError(
                    "Unknown or expired CodeAgent OAuth state.",
                    status_code=400,
                )
            session = self._sessions_by_id[session_id]
            completed = session.model_copy(
                update={
                    "completed": True,
                    "has_access_token": True,
                    "has_refresh_token": True,
                    "error_message": None,
                }
            )
            self._sessions_by_id[session_id] = completed
            self._tokens_by_session_id[session_id] = token_result
            return completed

    def complete_by_session_id(
        self,
        *,
        auth_session_id: str,
        token_result: CodeAgentOAuthTokenResult,
    ) -> CodeAgentOAuthSession:
        with self._lock:
            self._purge_expired_locked()
            session = self._sessions_by_id.get(auth_session_id)
            if session is None:
                raise CodeAgentOAuthError(
                    "Unknown or expired CodeAgent OAuth session.",
                    status_code=400,
                )
            completed = session.model_copy(
                update={
                    "completed": True,
                    "has_access_token": True,
                    "has_refresh_token": True,
                    "error_message": None,
                }
            )
            self._sessions_by_id[auth_session_id] = completed
            self._tokens_by_session_id[auth_session_id] = token_result
            return completed

    def consume_tokens(
        self,
        auth_session_id: str,
    ) -> CodeAgentOAuthTokenResult | None:
        with self._lock:
            self._purge_expired_locked()
            return self._tokens_by_session_id.pop(auth_session_id, None)

    def get_tokens(
        self,
        auth_session_id: str,
    ) -> CodeAgentOAuthTokenResult | None:
        with self._lock:
            self._purge_expired_locked()
            return self._tokens_by_session_id.get(auth_session_id)

    def _purge_expired_locked(self) -> None:
        now = datetime.now(UTC)
        expired_ids = [
            session_id
            for session_id, session in self._sessions_by_id.items()
            if session.expires_at <= now
        ]
        for session_id in expired_ids:
            session = self._sessions_by_id.pop(session_id, None)
            self._tokens_by_session_id.pop(session_id, None)
            if session is not None:
                self._session_id_by_state.pop(session.state, None)


def _refresh_token_payload(auth_config: CodeAgentAuthConfig) -> dict[str, str]:
    return {
        "grant_type": "refresh_token",
        "client_id": auth_config.client_id,
        "scope": auth_config.scope,
        "scope_resource": auth_config.scope_resource,
        "refresh_token": auth_config.refresh_token or "",
    }


def _generate_client_code() -> str:
    try:
        return uuid4().hex
    except Exception:
        return token_hex(16)


def _authorize_url() -> str:
    return f"{DEFAULT_CODEAGENT_SSO_BASE_URL.rstrip('/')}/oauth2/authorize"


def _codeagent_callback_url(*, base_url: str, client_code: str) -> str:
    query = urlencode({"client_code": client_code})
    return f"{base_url.rstrip('/')}/codeAgent/oauth/callback?{query}"


def _get_token_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/codeAgent/oauth/getToken"


def _refresh_token_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/codeAgent/oauth/refreshToken"


def _chat_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/chat/completions"


def build_codeagent_request_headers(
    *,
    token: str,
    content_type: str | None = None,
    accept: str | None = None,
) -> dict[str, str]:
    headers = {
        "X-Auth-Token": token,
        "app-id": "CodeAgent2.0",
        "User-Agent": "AgentKernel/1.0",
        "gray": "false",
        "oc-heartbeat": "1",
        "X-snap-traceid": str(uuid4()),
        "X-session-id": f"ses_{uuid4().hex[:20]}",
    }
    if content_type is not None:
        headers["Content-Type"] = content_type
    if accept is not None:
        headers["Accept"] = accept
    return headers


def _build_token_result(
    response: httpx.Response,
    *,
    fallback_refresh_token: str | None,
) -> CodeAgentOAuthTokenResult:
    payload = _response_json(response)
    if response.status_code >= 400:
        error_code = _extract_error_code(payload)
        raise CodeAgentOAuthError(
            _extract_error_message(payload)
            or response.text
            or "CodeAgent OAuth request failed.",
            status_code=response.status_code,
            error_code=error_code,
        )
    access_token = _extract_str(payload, ("access_token", "accessToken", "token"))
    refresh_token = _extract_str(payload, ("refresh_token", "refreshToken"))
    if refresh_token is None:
        refresh_token = fallback_refresh_token
    if access_token is None or refresh_token is None:
        raise CodeAgentOAuthError(
            "CodeAgent token response did not include access_token and refresh_token.",
            status_code=response.status_code,
        )
    expires_in = _extract_int(payload, ("expires_in", "expiresIn", "expire_in"))
    expires_at = datetime.now(UTC) + timedelta(
        seconds=expires_in if expires_in is not None and expires_in > 0 else 3600
    )
    return CodeAgentOAuthTokenResult(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )


def _build_polled_token_result(
    response: httpx.Response,
) -> CodeAgentOAuthTokenResult | None:
    payload = _response_json(response)
    if response.status_code >= 400:
        error_code = _extract_error_code(payload)
        raise CodeAgentOAuthError(
            _extract_error_message(payload)
            or response.text
            or "CodeAgent OAuth token polling failed.",
            status_code=response.status_code,
            error_code=error_code,
        )
    if response.status_code != 200:
        return None
    access_token = _extract_str(payload, ("access_token", "accessToken", "token"))
    if access_token is None:
        if _is_pending_poll_payload(payload):
            return None
        error_message = _extract_error_message(payload)
        if error_message is not None:
            raise CodeAgentOAuthError(
                error_message,
                status_code=response.status_code,
                error_code=_extract_error_code(payload),
            )
        return None
    refresh_token = _extract_str(payload, ("refresh_token", "refreshToken"))
    if refresh_token is None:
        raise CodeAgentOAuthError(
            "CodeAgent token response did not include refresh_token.",
            status_code=response.status_code,
        )
    return _build_token_result(response, fallback_refresh_token=None)


def _clone_request_with_codeagent_headers(
    request: httpx.Request,
    *,
    base_url: str,
    token: str,
) -> httpx.Request:
    headers = _strip_reserved_headers(dict(request.headers))
    is_chat_request = is_codeagent_chat_completion_request(request)
    headers.update(
        build_codeagent_request_headers(
            token=token,
            content_type="application/json" if is_chat_request else None,
            accept="text/event-stream" if is_chat_request else None,
        )
    )
    url = _chat_url(base_url) if is_chat_request else str(request.url)
    return httpx.Request(
        method=request.method,
        url=url,
        headers=headers,
        content=request.content,
        extensions=dict(request.extensions),
    )


def _strip_reserved_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        name: value
        for name, value in headers.items()
        if name.casefold()
        not in {
            "authorization",
            "x-auth-token",
            "app-id",
            "user-agent",
            "gray",
            "oc-heartbeat",
            "x-snap-traceid",
            "x-session-id",
        }
    }


def _response_json(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def _extract_str(payload: object, keys: tuple[str, ...]) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_int(payload: object, keys: tuple[str, ...]) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _extract_error_message(payload: object) -> str | None:
    if isinstance(payload, dict):
        for key in ("message", "detail", "error_description", "error_msg"):
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


def _extract_error_code(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("error_code", "errorCode", "code"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        if isinstance(code, str) and code.strip():
            return code.strip()
    return None


def _is_pending_poll_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    for key in ("status", "state", "message", "detail"):
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        normalized = value.strip().casefold()
        if normalized in {
            "pending",
            "waiting",
            "processing",
            "in_progress",
            "in progress",
        }:
            return True
    return False


def _persist_codeagent_profile_tokens(
    *,
    auth_config: CodeAgentAuthConfig,
    token_result: CodeAgentOAuthTokenResult,
) -> None:
    config_dir = auth_config.secret_config_dir
    owner_id = auth_config.secret_owner_id
    if config_dir is None or owner_id is None:
        return
    _set_model_profile_secret(
        config_dir=config_dir,
        owner_id=owner_id,
        field_name=CODEAGENT_ACCESS_TOKEN_SECRET_FIELD,
        value=token_result.access_token,
    )
    _set_model_profile_secret(
        config_dir=config_dir,
        owner_id=owner_id,
        field_name=CODEAGENT_REFRESH_TOKEN_SECRET_FIELD,
        value=token_result.refresh_token,
    )


def _set_model_profile_secret(
    *,
    config_dir: Path,
    owner_id: str,
    field_name: str,
    value: str,
) -> None:
    get_secret_store().set_secret(
        config_dir,
        namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
        owner_id=owner_id,
        field_name=field_name,
        value=value,
    )


_DEFAULT_CODEAGENT_TOKEN_SERVICE = CodeAgentTokenService()
_CODEAGENT_OAUTH_SESSION_STORE = _CodeAgentOAuthSessionStore()
