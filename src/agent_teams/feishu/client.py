# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from json import dumps

import lark_oapi as lark
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
