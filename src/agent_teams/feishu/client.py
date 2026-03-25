# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from json import dumps
import time

import httpx

from agent_teams.env.runtime_env import load_merged_env_vars
from agent_teams.feishu.models import FeishuEnvironment
from agent_teams.net import create_sync_http_client

_TOKEN_REFRESH_SKEW_SECONDS = 60.0


def load_feishu_environment(
    merged_env: Mapping[str, str] | None = None,
) -> FeishuEnvironment | None:
    resolved_env = (
        load_merged_env_vars() if merged_env is None else dict(merged_env.items())
    )
    app_id = str(resolved_env.get("FEISHU_APP_ID", "")).strip()
    app_secret = str(resolved_env.get("FEISHU_APP_SECRET", "")).strip()
    app_name_raw = str(resolved_env.get("FEISHU_APP_NAME", "")).strip()
    app_name = app_name_raw or None
    verification_token_raw = str(
        resolved_env.get("FEISHU_VERIFICATION_TOKEN", "")
    ).strip()
    verification_token = verification_token_raw or None
    encrypt_key_raw = str(resolved_env.get("FEISHU_ENCRYPT_KEY", "")).strip()
    encrypt_key = encrypt_key_raw or None
    if not app_id or not app_secret:
        return None
    return FeishuEnvironment(
        app_id=app_id,
        app_secret=app_secret,
        app_name=app_name,
        verification_token=verification_token,
        encrypt_key=encrypt_key,
    )


class _CachedTenantAccessToken:
    def __init__(self, *, value: str, expires_at_epoch_seconds: float) -> None:
        self.value = value
        self.expires_at_epoch_seconds = expires_at_epoch_seconds

    def is_expired(self, *, now_epoch_seconds: float) -> bool:
        return now_epoch_seconds >= self.expires_at_epoch_seconds


class FeishuClient:
    def __init__(
        self,
        *,
        merged_env: Mapping[str, str] | None = None,
        base_url: str = "https://open.feishu.cn",
    ) -> None:
        self._merged_env = None if merged_env is None else dict(merged_env.items())
        self._base_url = base_url.rstrip("/")
        self._http_client: httpx.Client | None = None
        self._token_cache: dict[tuple[str, str, str], _CachedTenantAccessToken] = {}
        self._chat_name_cache: dict[tuple[str, str, str], str] = {}
        self._user_name_cache: dict[tuple[str, str, str], str] = {}

    def is_configured(self, environment: FeishuEnvironment | None = None) -> bool:
        return self._resolve_environment(environment) is not None

    def require_environment(
        self,
        environment: FeishuEnvironment | None = None,
    ) -> FeishuEnvironment:
        resolved_environment = self._resolve_environment(environment)
        if resolved_environment is None:
            raise RuntimeError(
                "Feishu integration requires trigger-level app_id and app_secret."
            )
        return resolved_environment

    def send_text_message(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> None:
        self._send_message(
            chat_id=chat_id,
            msg_type="text",
            content={"text": text},
            environment=environment,
        )

    def send_card_message(
        self,
        *,
        chat_id: str,
        card: dict[str, object],
        environment: FeishuEnvironment | None = None,
    ) -> None:
        self._send_message(
            chat_id=chat_id,
            msg_type="interactive",
            content={"card": card},
            environment=environment,
        )

    def get_chat_name(
        self,
        *,
        chat_id: str,
        environment: FeishuEnvironment | None = None,
    ) -> str | None:
        resolved_environment = self.require_environment(environment)
        normalized_chat_id = str(chat_id).strip()
        if not normalized_chat_id:
            return None
        cache_key = (
            resolved_environment.app_id,
            resolved_environment.app_secret,
            normalized_chat_id,
        )
        existing = self._chat_name_cache.get(cache_key)
        if existing is not None:
            return existing
        response_json = self._request_json(
            method="GET",
            path=f"/open-apis/im/v1/chats/{normalized_chat_id}",
            environment=resolved_environment,
            error_context="load chat",
        )
        response_data = _require_json_object(
            response_json.get("data"),
            error_context="load chat",
        )
        chat_name = str(response_data.get("name", "")).strip()
        if not chat_name:
            return None
        self._chat_name_cache[cache_key] = chat_name
        return chat_name

    def get_user_name(
        self,
        *,
        open_id: str,
        environment: FeishuEnvironment | None = None,
    ) -> str | None:
        resolved_environment = self.require_environment(environment)
        normalized_open_id = str(open_id).strip()
        if not normalized_open_id:
            return None
        cache_key = (
            resolved_environment.app_id,
            resolved_environment.app_secret,
            normalized_open_id,
        )
        existing = self._user_name_cache.get(cache_key)
        if existing is not None:
            return existing
        response_json = self._request_json(
            method="GET",
            path=f"/open-apis/contact/v3/users/{normalized_open_id}",
            params={"user_id_type": "open_id"},
            environment=resolved_environment,
            error_context="load user",
        )
        response_data = _require_json_object(
            response_json.get("data"),
            error_context="load user",
        )
        user_data = _require_json_object(
            response_data.get("user"),
            error_context="load user",
        )
        user_name = str(user_data.get("name", "")).strip()
        if not user_name:
            return None
        self._user_name_cache[cache_key] = user_name
        return user_name

    def _send_message(
        self,
        *,
        chat_id: str,
        msg_type: str,
        content: dict[str, object],
        environment: FeishuEnvironment | None,
    ) -> None:
        self._request_json(
            method="POST",
            path="/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            json_body={
                "receive_id": chat_id,
                "msg_type": msg_type,
                "content": dumps(content, ensure_ascii=False),
            },
            environment=self.require_environment(environment),
            error_context="send message",
        )

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        environment: FeishuEnvironment,
        error_context: str,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
        include_access_token: bool = True,
    ) -> dict[str, object]:
        headers: dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
        if include_access_token:
            headers["Authorization"] = (
                f"Bearer {self._get_tenant_access_token(environment)}"
            )
        response = self._client().request(
            method=method,
            url=f"{self._base_url}{path}",
            headers=headers,
            params=None if params is None else dict(params.items()),
            json=None if json_body is None else dict(json_body.items()),
        )
        response_json = _parse_json_response(response, error_context=error_context)
        response_code = response_json.get("code")
        if response_code not in (0, "0", None):
            response_message = str(response_json.get("msg", "")).strip()
            message = response_message or "unknown_error"
            raise RuntimeError(f"Feishu API failed to {error_context}: {message}")
        return response_json

    def _get_tenant_access_token(self, environment: FeishuEnvironment) -> str:
        cache_key = (
            environment.app_id,
            environment.app_secret,
            self._base_url,
        )
        now_epoch_seconds = time.time()
        cached = self._token_cache.get(cache_key)
        if cached is not None and not cached.is_expired(
            now_epoch_seconds=now_epoch_seconds
        ):
            return cached.value
        response_json = self._request_json(
            method="POST",
            path="/open-apis/auth/v3/tenant_access_token/internal",
            environment=environment,
            error_context="obtain tenant access token",
            json_body={
                "app_id": environment.app_id,
                "app_secret": environment.app_secret,
            },
            include_access_token=False,
        )
        access_token = str(response_json.get("tenant_access_token", "")).strip()
        if not access_token:
            raise RuntimeError(
                "Feishu API failed to obtain tenant access token: missing token"
            )
        expire_seconds = _coerce_expire_seconds(response_json.get("expire"))
        self._token_cache[cache_key] = _CachedTenantAccessToken(
            value=access_token,
            expires_at_epoch_seconds=(
                now_epoch_seconds
                + max(expire_seconds - _TOKEN_REFRESH_SKEW_SECONDS, 0.0)
            ),
        )
        return access_token

    def _client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = create_sync_http_client(merged_env=self._merged_env)
        return self._http_client

    def _load_environment(self) -> FeishuEnvironment | None:
        return load_feishu_environment(self._merged_env)

    def _resolve_environment(
        self,
        environment: FeishuEnvironment | None,
    ) -> FeishuEnvironment | None:
        if environment is not None:
            return environment
        return self._load_environment()


def _parse_json_response(
    response: httpx.Response,
    *,
    error_context: str,
) -> dict[str, object]:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body_text = exc.response.text.strip()
        detail = body_text or str(exc)
        raise RuntimeError(f"Feishu API failed to {error_context}: {detail}") from exc
    try:
        response_json = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Feishu API failed to {error_context}: invalid JSON response"
        ) from exc
    if not isinstance(response_json, dict):
        raise RuntimeError(
            f"Feishu API failed to {error_context}: invalid JSON response"
        )
    return dict(response_json.items())


def _require_json_object(
    value: object,
    *,
    error_context: str,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"Feishu API failed to {error_context}: missing data")
    return dict(value.items())


def _coerce_expire_seconds(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    value_text = str(value or "").strip()
    if not value_text:
        return 0.0
    try:
        return float(value_text)
    except ValueError:
        return 0.0
