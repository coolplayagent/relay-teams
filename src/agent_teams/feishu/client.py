# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from json import dumps

import lark_oapi as lark
from lark_oapi.api.contact.v3.model.get_user_request import GetUserRequest
from lark_oapi.api.im.v1.model.get_chat_request import GetChatRequest
from lark_oapi.api.im.v1.model.create_message_request import CreateMessageRequest
from lark_oapi.api.im.v1.model.create_message_request_body import (
    CreateMessageRequestBody,
)

from agent_teams.env.runtime_env import load_merged_env_vars
from agent_teams.feishu.models import FeishuEnvironment


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


class FeishuClient:
    def __init__(
        self,
        *,
        merged_env: Mapping[str, str] | None = None,
        base_url: str = "https://open.feishu.cn",
    ) -> None:
        self._merged_env = None if merged_env is None else dict(merged_env.items())
        self._base_url = base_url.rstrip("/")
        self._sdk_clients: dict[tuple[str, str, str], lark.Client] = {}
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
        request = GetChatRequest.builder().chat_id(normalized_chat_id).build()
        sdk_client = self._sdk(resolved_environment)
        im_service = sdk_client.im
        if im_service is None or im_service.v1 is None:
            raise RuntimeError("Feishu SDK client did not initialize IM services.")
        response = im_service.v1.chat.get(request)
        if not response.success():
            message = str(response.msg or "").strip() or "unknown_error"
            raise RuntimeError(f"Feishu API failed to load chat: {message}")
        chat_name = str(response.data.name or "").strip() if response.data is not None else ""
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
        request = (
            GetUserRequest.builder()
            .user_id_type("open_id")
            .user_id(normalized_open_id)
            .build()
        )
        sdk_client = self._sdk(resolved_environment)
        contact_service = sdk_client.contact
        if contact_service is None or contact_service.v3 is None:
            raise RuntimeError("Feishu SDK client did not initialize Contact services.")
        response = contact_service.v3.user.get(request)
        if not response.success():
            message = str(response.msg or "").strip() or "unknown_error"
            raise RuntimeError(f"Feishu API failed to load user: {message}")
        user_name = (
            str(response.data.user.name or "").strip()
            if response.data is not None and response.data.user is not None
            else ""
        )
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
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type(msg_type)
                .content(dumps(content, ensure_ascii=False))
                .build()
            )
            .build()
        )
        sdk_client = self._sdk(environment)
        im_service = sdk_client.im
        if im_service is None:
            raise RuntimeError("Feishu SDK client did not initialize IM services.")
        response = im_service.v1.message.create(request)
        if response.success():
            return
        message = str(response.msg or "").strip() or "unknown_error"
        raise RuntimeError(f"Feishu API failed to send message: {message}")

    def _sdk(self, environment: FeishuEnvironment | None = None) -> lark.Client:
        resolved_environment = self.require_environment(environment)
        signature = (
            resolved_environment.app_id,
            resolved_environment.app_secret,
            self._base_url,
        )
        existing = self._sdk_clients.get(signature)
        if existing is not None:
            return existing
        client = (
            lark.Client.builder()
            .app_id(resolved_environment.app_id)
            .app_secret(resolved_environment.app_secret)
            .domain(self._base_url)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
        self._sdk_clients[signature] = client
        return client

    def _load_environment(self) -> FeishuEnvironment | None:
        return load_feishu_environment(self._merged_env)

    def _resolve_environment(
        self,
        environment: FeishuEnvironment | None,
    ) -> FeishuEnvironment | None:
        if environment is not None:
            return environment
        return self._load_environment()
