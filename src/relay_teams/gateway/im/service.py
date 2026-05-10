# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from relay_teams.gateway.discord.models import DiscordAccountRecord
from relay_teams.gateway.feishu.models import FeishuEnvironment
from relay_teams.gateway.im.context import _AutomationProjectLookup
from relay_teams.gateway.im.context import (
    DiscordChatContext,
    FeishuChatContext,
    WeChatChatContext,
    _GatewaySessionLookup,
    _RuntimeConfigLookup,
    _SessionLookup,
    resolve_im_chat_context,
)
from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.gateway.wechat.models import WeChatAccountRecord
from relay_teams.automation.automation_models import AutomationRunDeliveryRecord


class _FeishuSender(Protocol):
    async def reply_text_message(
        self,
        *,
        message_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> str:
        raise NotImplementedError

    async def send_text_message(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> str:
        raise NotImplementedError

    async def send_file(
        self,
        *,
        chat_id: str,
        file_path: Path,
        environment: FeishuEnvironment | None = None,
    ) -> str:
        raise NotImplementedError


class _WeChatAccountLookup(Protocol):
    def get_account(self, account_id: str) -> WeChatAccountRecord:
        raise NotImplementedError


class _WeChatSecretStore(Protocol):
    def get_bot_token(self, config_dir: Path, account_id: str) -> str | None:
        raise NotImplementedError


class _WeChatSender(Protocol):
    async def send_text_message(
        self,
        *,
        account: WeChatAccountRecord,
        token: str,
        to_user_id: str,
        text: str,
        context_token: str | None,
    ) -> None:
        raise NotImplementedError

    async def send_file(
        self,
        *,
        account: WeChatAccountRecord,
        token: str,
        to_user_id: str,
        file_path: Path,
        context_token: str | None,
    ) -> str:
        raise NotImplementedError


class _DiscordAccountLookup(Protocol):
    async def get_account(self, account_id: str) -> DiscordAccountRecord:
        raise NotImplementedError  # pragma: no cover


class _DiscordSecretStore(Protocol):
    def get_bot_token(self, config_dir: Path, account_id: str) -> str | None:
        raise NotImplementedError  # pragma: no cover


class _DiscordSender(Protocol):
    async def send_text_message(
        self,
        *,
        token: str,
        channel_id: str,
        text: str,
        reply_to_message_id: str | None = None,
    ) -> str:
        raise NotImplementedError  # pragma: no cover

    async def send_file(
        self,
        *,
        token: str,
        channel_id: str,
        file_path: Path,
        reply_to_message_id: str | None = None,
    ) -> str:
        raise NotImplementedError  # pragma: no cover


class _RunIntentLookup(Protocol):
    def get(self, run_id: str) -> IntentInput:
        raise NotImplementedError


class _AutomationDeliveryLookup(Protocol):
    def get_by_run_id(self, run_id: str) -> AutomationRunDeliveryRecord:
        raise NotImplementedError


class ImToolService:
    def __init__(
        self,
        *,
        config_dir: Path,
        session_repo: _SessionLookup,
        runtime_config_lookup: _RuntimeConfigLookup,
        run_intent_lookup: _RunIntentLookup | None = None,
        automation_project_repo: _AutomationProjectLookup | None = None,
        automation_delivery_lookup: _AutomationDeliveryLookup | None = None,
        gateway_session_lookup: _GatewaySessionLookup | None = None,
        feishu_client: _FeishuSender,
        wechat_account_repo: _WeChatAccountLookup,
        wechat_secret_store: _WeChatSecretStore,
        wechat_client: _WeChatSender,
        discord_account_repo: _DiscordAccountLookup | None = None,
        discord_secret_store: _DiscordSecretStore | None = None,
        discord_client: _DiscordSender | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._session_repo = session_repo
        self._runtime_config_lookup = runtime_config_lookup
        self._run_intent_lookup = run_intent_lookup
        self._automation_project_repo = automation_project_repo
        self._automation_delivery_lookup = automation_delivery_lookup
        self._gateway_session_lookup = gateway_session_lookup
        self._feishu_client = feishu_client
        self._wechat_account_repo = wechat_account_repo
        self._wechat_secret_store = wechat_secret_store
        self._wechat_client = wechat_client
        self._discord_account_repo = discord_account_repo
        self._discord_secret_store = discord_secret_store
        self._discord_client = discord_client

    async def send_text(
        self,
        *,
        session_id: str,
        text: str,
        run_id: str | None = None,
    ) -> str:
        ctx = self._resolve_context(session_id, run_id=run_id)
        if ctx is None:
            return "Session is not linked to an IM chat."
        await self.send_text_to_context(ctx=ctx, text=text)
        return "Message sent."

    async def send_file(
        self,
        *,
        session_id: str,
        file_path: Path,
        run_id: str | None = None,
    ) -> str:
        ctx = self._resolve_context(session_id, run_id=run_id)
        if ctx is None:
            return "Session is not linked to an IM chat."
        if not file_path.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")
        if isinstance(ctx, FeishuChatContext):
            return await self._feishu_client.send_file(
                chat_id=ctx.chat_id,
                file_path=file_path,
                environment=ctx.environment,
            )
        if isinstance(ctx, DiscordChatContext):
            return await self._send_discord_file(ctx=ctx, file_path=file_path)
        return await self._send_wechat_file(ctx=ctx, file_path=file_path)

    async def send_text_to_context(
        self,
        *,
        ctx: FeishuChatContext | WeChatChatContext | DiscordChatContext,
        text: str,
    ) -> None:
        if isinstance(ctx, FeishuChatContext):
            await self.send_text_to_feishu_chat(
                chat_id=ctx.chat_id,
                text=text,
                environment=ctx.environment,
                reply_to_message_id=(
                    ctx.reply_to_message_id if ctx.prefer_reply else None
                ),
            )
            return
        if isinstance(ctx, DiscordChatContext):
            await self.send_text_to_discord_channel(
                account_id=ctx.account_id,
                channel_id=ctx.channel_id,
                text=text,
                reply_to_message_id=ctx.reply_to_message_id,
            )
            return
        await self.send_text_to_wechat_peer(
            account_id=ctx.account_id,
            peer_user_id=ctx.peer_user_id,
            text=text,
            context_token=ctx.context_token,
        )

    async def send_text_to_feishu_chat(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
        reply_to_message_id: str | None = None,
    ) -> None:
        normalized_reply_to_message_id = str(reply_to_message_id or "").strip()
        if normalized_reply_to_message_id:
            await self._feishu_client.reply_text_message(
                message_id=normalized_reply_to_message_id,
                text=text,
                environment=environment,
            )
            return
        await self._feishu_client.send_text_message(
            chat_id=chat_id,
            text=text,
            environment=environment,
        )

    async def send_text_to_wechat_peer(
        self,
        *,
        account_id: str,
        peer_user_id: str,
        text: str,
        context_token: str | None,
    ) -> None:
        await self._send_wechat_text(
            ctx=WeChatChatContext(
                account_id=account_id,
                peer_user_id=peer_user_id,
                context_token=context_token,
            ),
            text=text,
        )

    async def send_text_to_discord_channel(
        self,
        *,
        account_id: str,
        channel_id: str,
        text: str,
        reply_to_message_id: str | None = None,
    ) -> None:
        await self._send_discord_text(
            ctx=DiscordChatContext(
                account_id=account_id,
                channel_id=channel_id,
                reply_to_message_id=reply_to_message_id,
            ),
            text=text,
        )

    def _resolve_context(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
    ) -> FeishuChatContext | WeChatChatContext | DiscordChatContext | None:
        prefer_direct_send = self._should_force_direct_send(run_id)
        ctx = resolve_im_chat_context(
            session_repo=self._session_repo,
            runtime_config_lookup=self._runtime_config_lookup,
            automation_project_repo=self._automation_project_repo,
            gateway_session_lookup=self._gateway_session_lookup,
            session_id=session_id,
            prefer_direct_send=prefer_direct_send,
        )
        if not isinstance(ctx, FeishuChatContext):
            return ctx
        reply_to_message_id = self._resolve_reply_to_message_id(run_id)
        if reply_to_message_id is None:
            if self._has_delivery_without_receipt(run_id):
                return FeishuChatContext(
                    chat_id=ctx.chat_id,
                    environment=ctx.environment,
                    chat_type=ctx.chat_type,
                    prefer_reply=False,
                )
            return ctx
        return FeishuChatContext(
            chat_id=ctx.chat_id,
            environment=ctx.environment,
            chat_type=ctx.chat_type,
            reply_to_message_id=reply_to_message_id,
            prefer_reply=True,
        )

    def _should_force_direct_send(self, run_id: str | None) -> bool:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id or self._run_intent_lookup is None:
            return False
        try:
            intent = self._run_intent_lookup.get(normalized_run_id)
        except KeyError:
            return False
        context = intent.conversation_context
        if context is None:
            return False
        return context.im_force_direct_send

    def _resolve_reply_to_message_id(self, run_id: str | None) -> str | None:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return None
        intent_reply_to_message_id = self._intent_reply_to_message_id(normalized_run_id)
        if intent_reply_to_message_id is not None:
            return intent_reply_to_message_id
        if self._automation_delivery_lookup is None:
            return None
        try:
            delivery = self._automation_delivery_lookup.get_by_run_id(normalized_run_id)
        except KeyError:
            return None
        reply_to_message_id = (
            str(delivery.started_message_id or "").strip()
            or str(delivery.reply_to_message_id or "").strip()
        )
        return reply_to_message_id or None

    def _has_delivery_without_receipt(self, run_id: str | None) -> bool:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id or self._automation_delivery_lookup is None:
            return False
        try:
            _ = self._automation_delivery_lookup.get_by_run_id(normalized_run_id)
        except KeyError:
            return False
        return True

    def _intent_reply_to_message_id(self, run_id: str) -> str | None:
        if self._run_intent_lookup is None:
            return None
        try:
            intent = self._run_intent_lookup.get(run_id)
        except KeyError:
            return None
        context = intent.conversation_context
        if context is None:
            return None
        reply_to_message_id = str(context.im_reply_to_message_id or "").strip()
        return reply_to_message_id or None

    async def _send_wechat_text(
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
        await self._wechat_client.send_text_message(
            account=account,
            token=token,
            to_user_id=ctx.peer_user_id,
            text=text,
            context_token=ctx.context_token,
        )

    async def _send_wechat_file(
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
        return await self._wechat_client.send_file(
            account=account,
            token=token,
            to_user_id=ctx.peer_user_id,
            file_path=file_path,
            context_token=ctx.context_token,
        )

    async def _send_discord_text(
        self,
        *,
        ctx: DiscordChatContext,
        text: str,
    ) -> None:
        if (
            self._discord_account_repo is None
            or self._discord_secret_store is None
            or self._discord_client is None
        ):
            raise RuntimeError("Discord send is not available in this session.")
        _ = await self._discord_account_repo.get_account(ctx.account_id)
        token = self._discord_secret_store.get_bot_token(
            self._config_dir,
            ctx.account_id,
        )
        if token is None:
            raise RuntimeError(
                "Discord send is unavailable because the bot token is missing."
            )
        await self._discord_client.send_text_message(
            token=token,
            channel_id=ctx.channel_id,
            text=text,
            reply_to_message_id=ctx.reply_to_message_id,
        )

    async def _send_discord_file(
        self,
        *,
        ctx: DiscordChatContext,
        file_path: Path,
    ) -> str:
        if (
            self._discord_account_repo is None
            or self._discord_secret_store is None
            or self._discord_client is None
        ):
            raise RuntimeError("Discord send is not available in this session.")
        _ = await self._discord_account_repo.get_account(ctx.account_id)
        token = self._discord_secret_store.get_bot_token(
            self._config_dir,
            ctx.account_id,
        )
        if token is None:
            raise RuntimeError(
                "Discord send is unavailable because the bot token is missing."
            )
        return await self._discord_client.send_file(
            token=token,
            channel_id=ctx.channel_id,
            file_path=file_path,
            reply_to_message_id=ctx.reply_to_message_id,
        )
