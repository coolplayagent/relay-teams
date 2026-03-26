# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agent_teams.gateway.feishu.models import FeishuEnvironment
from agent_teams.gateway.im.context import _AutomationProjectLookup
from agent_teams.gateway.im.context import (
    FeishuChatContext,
    WeChatChatContext,
    _GatewaySessionLookup,
    _RuntimeConfigLookup,
    _SessionLookup,
    resolve_im_chat_context,
)
from agent_teams.gateway.wechat.models import WeChatAccountRecord


class _FeishuSender(Protocol):
    def send_text_message(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> None: ...

    def send_file(
        self,
        *,
        chat_id: str,
        file_path: Path,
        environment: FeishuEnvironment | None = None,
    ) -> str: ...


class _WeChatAccountLookup(Protocol):
    def get_account(self, account_id: str) -> WeChatAccountRecord: ...


class _WeChatSecretStore(Protocol):
    def get_bot_token(self, config_dir: Path, account_id: str) -> str | None: ...


class _WeChatSender(Protocol):
    def send_text_message(
        self,
        *,
        account: WeChatAccountRecord,
        token: str,
        to_user_id: str,
        text: str,
        context_token: str | None,
    ) -> None: ...

    def send_file(
        self,
        *,
        account: WeChatAccountRecord,
        token: str,
        to_user_id: str,
        file_path: Path,
        context_token: str | None,
    ) -> str: ...


class ImToolService:
    def __init__(
        self,
        *,
        config_dir: Path,
        session_repo: _SessionLookup,
        runtime_config_lookup: _RuntimeConfigLookup,
        automation_project_repo: _AutomationProjectLookup | None = None,
        gateway_session_lookup: _GatewaySessionLookup | None = None,
        feishu_client: _FeishuSender,
        wechat_account_repo: _WeChatAccountLookup,
        wechat_secret_store: _WeChatSecretStore,
        wechat_client: _WeChatSender,
    ) -> None:
        self._config_dir = config_dir
        self._session_repo = session_repo
        self._runtime_config_lookup = runtime_config_lookup
        self._automation_project_repo = automation_project_repo
        self._gateway_session_lookup = gateway_session_lookup
        self._feishu_client = feishu_client
        self._wechat_account_repo = wechat_account_repo
        self._wechat_secret_store = wechat_secret_store
        self._wechat_client = wechat_client

    def send_text(self, *, session_id: str, text: str) -> str:
        ctx = self._resolve_context(session_id)
        if ctx is None:
            return "Session is not linked to an IM chat."
        if isinstance(ctx, FeishuChatContext):
            self._feishu_client.send_text_message(
                chat_id=ctx.chat_id,
                text=text,
                environment=ctx.environment,
            )
            return "Message sent."
        self._send_wechat_text(ctx=ctx, text=text)
        return "Message sent."

    def send_file(self, *, session_id: str, file_path: Path) -> str:
        ctx = self._resolve_context(session_id)
        if ctx is None:
            return "Session is not linked to an IM chat."
        if not file_path.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")
        if isinstance(ctx, FeishuChatContext):
            return self._feishu_client.send_file(
                chat_id=ctx.chat_id,
                file_path=file_path,
                environment=ctx.environment,
            )
        return self._send_wechat_file(ctx=ctx, file_path=file_path)

    def _resolve_context(
        self,
        session_id: str,
    ) -> FeishuChatContext | WeChatChatContext | None:
        return resolve_im_chat_context(
            session_repo=self._session_repo,
            runtime_config_lookup=self._runtime_config_lookup,
            automation_project_repo=self._automation_project_repo,
            gateway_session_lookup=self._gateway_session_lookup,
            session_id=session_id,
        )

    def _send_wechat_text(
        self,
        *,
        ctx: WeChatChatContext,
        text: str,
    ) -> None:
        account = self._wechat_account_repo.get_account(ctx.account_id)
        token = self._wechat_secret_store.get_bot_token(
            self._config_dir,
            ctx.account_id,
        )
        if token is None:
            raise RuntimeError(
                "WeChat send is unavailable because the bot token is missing."
            )
        self._wechat_client.send_text_message(
            account=account,
            token=token,
            to_user_id=ctx.peer_user_id,
            text=text,
            context_token=ctx.context_token,
        )

    def _send_wechat_file(
        self,
        *,
        ctx: WeChatChatContext,
        file_path: Path,
    ) -> str:
        account = self._wechat_account_repo.get_account(ctx.account_id)
        token = self._wechat_secret_store.get_bot_token(
            self._config_dir,
            ctx.account_id,
        )
        if token is None:
            raise RuntimeError(
                "WeChat send is unavailable because the bot token is missing."
            )
        return self._wechat_client.send_file(
            account=account,
            token=token,
            to_user_id=ctx.peer_user_id,
            file_path=file_path,
            context_token=ctx.context_token,
        )
