# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Protocol

from agent_teams.automation import AutomationProjectRecord
from agent_teams.gateway.feishu.models import (
    FEISHU_METADATA_CHAT_ID_KEY,
    FEISHU_METADATA_CHAT_TYPE_KEY,
    FEISHU_METADATA_MESSAGE_ID_KEY,
    FEISHU_METADATA_PLATFORM_KEY,
    FEISHU_METADATA_TRIGGER_ID_KEY,
    FEISHU_PLATFORM,
    FeishuEnvironment,
    FeishuTriggerRuntimeConfig,
)
from agent_teams.gateway.gateway_models import GatewayChannelType, GatewaySessionRecord
from agent_teams.sessions.session_models import ProjectKind, SessionRecord
from agent_teams.tools.registry.registry import ToolResolutionContext

IM_IMPLICIT_TOOLS: tuple[str, ...] = ("im_send",)


class _SessionLookup(Protocol):
    def get(self, session_id: str) -> SessionRecord: ...


class _RuntimeConfigLookup(Protocol):
    def get_runtime_config_by_trigger_id(
        self,
        trigger_id: str,
    ) -> FeishuTriggerRuntimeConfig | None: ...


class _AutomationProjectLookup(Protocol):
    def get(self, automation_project_id: str) -> AutomationProjectRecord: ...


class _GatewaySessionLookup(Protocol):
    def get_by_internal_session_id(
        self,
        internal_session_id: str,
    ) -> GatewaySessionRecord | None: ...


class FeishuChatContext:
    def __init__(
        self,
        *,
        chat_id: str,
        environment: FeishuEnvironment,
        chat_type: str | None = None,
        reply_to_message_id: str | None = None,
        prefer_reply: bool = False,
    ) -> None:
        self.chat_id = chat_id
        self.environment = environment
        self.chat_type = str(chat_type or "").strip()
        self.reply_to_message_id = str(reply_to_message_id or "").strip() or None
        self.prefer_reply = prefer_reply


class WeChatChatContext:
    def __init__(
        self,
        *,
        account_id: str,
        peer_user_id: str,
        context_token: str | None,
    ) -> None:
        self.account_id = account_id
        self.peer_user_id = peer_user_id
        self.context_token = context_token


def resolve_feishu_chat_context(
    *,
    session_repo: _SessionLookup,
    runtime_config_lookup: _RuntimeConfigLookup,
    automation_project_repo: _AutomationProjectLookup | None = None,
    session_id: str,
    prefer_direct_send: bool = False,
) -> FeishuChatContext | None:
    try:
        session = session_repo.get(session_id)
    except KeyError:
        return None
    return _resolve_from_session(
        session=session,
        runtime_config_lookup=runtime_config_lookup,
        automation_project_repo=automation_project_repo,
        prefer_direct_send=prefer_direct_send,
    )


def resolve_im_chat_context(
    *,
    session_repo: _SessionLookup,
    runtime_config_lookup: _RuntimeConfigLookup,
    automation_project_repo: _AutomationProjectLookup | None = None,
    gateway_session_lookup: _GatewaySessionLookup | None = None,
    session_id: str,
    prefer_direct_send: bool = False,
) -> FeishuChatContext | WeChatChatContext | None:
    feishu_context = resolve_feishu_chat_context(
        session_repo=session_repo,
        runtime_config_lookup=runtime_config_lookup,
        automation_project_repo=automation_project_repo,
        session_id=session_id,
        prefer_direct_send=prefer_direct_send,
    )
    if feishu_context is not None:
        return feishu_context
    if gateway_session_lookup is None:
        return None
    return resolve_wechat_chat_context(
        gateway_session_lookup=gateway_session_lookup,
        session_id=session_id,
    )


def resolve_wechat_chat_context(
    *,
    gateway_session_lookup: _GatewaySessionLookup,
    session_id: str,
) -> WeChatChatContext | None:
    gateway_session = gateway_session_lookup.get_by_internal_session_id(session_id)
    if (
        gateway_session is None
        or gateway_session.channel_type != GatewayChannelType.WECHAT
    ):
        return None
    account_id = str(gateway_session.channel_state.get("account_id", "")).strip()
    if not account_id:
        return None
    peer_user_id = str(
        gateway_session.channel_state.get("peer_user_id")
        or gateway_session.peer_user_id
        or ""
    ).strip()
    if not peer_user_id:
        return None
    raw_context_token = gateway_session.channel_state.get("context_token")
    context_token = None
    if isinstance(raw_context_token, str):
        normalized_context_token = raw_context_token.strip()
        if normalized_context_token:
            context_token = normalized_context_token
    return WeChatChatContext(
        account_id=account_id,
        peer_user_id=peer_user_id,
        context_token=context_token,
    )


class ImToolContextResolver:
    def __init__(
        self,
        *,
        session_repo: _SessionLookup,
        runtime_config_lookup: _RuntimeConfigLookup,
        automation_project_repo: _AutomationProjectLookup | None = None,
        gateway_session_lookup: _GatewaySessionLookup | None = None,
    ) -> None:
        self._session_repo = session_repo
        self._runtime_config_lookup = runtime_config_lookup
        self._automation_project_repo = automation_project_repo
        self._gateway_session_lookup = gateway_session_lookup

    def resolve_implicit_tools(
        self,
        context: ToolResolutionContext,
    ) -> tuple[str, ...]:
        session_id = context.session_id.strip()
        if not session_id:
            return ()
        chat_context = resolve_im_chat_context(
            session_repo=self._session_repo,
            runtime_config_lookup=self._runtime_config_lookup,
            automation_project_repo=self._automation_project_repo,
            gateway_session_lookup=self._gateway_session_lookup,
            session_id=session_id,
        )
        if chat_context is None:
            return ()
        return IM_IMPLICIT_TOOLS


def _resolve_from_session(
    *,
    session: SessionRecord,
    runtime_config_lookup: _RuntimeConfigLookup,
    automation_project_repo: _AutomationProjectLookup | None,
    prefer_direct_send: bool,
) -> FeishuChatContext | None:
    metadata = session.metadata
    if str(metadata.get(FEISHU_METADATA_PLATFORM_KEY, "")).strip() != FEISHU_PLATFORM:
        return _resolve_automation_binding_context(
            session=session,
            runtime_config_lookup=runtime_config_lookup,
            automation_project_repo=automation_project_repo,
            prefer_direct_send=prefer_direct_send,
        )
    trigger_id = str(metadata.get(FEISHU_METADATA_TRIGGER_ID_KEY, "")).strip()
    if not trigger_id:
        return None
    chat_id = str(metadata.get(FEISHU_METADATA_CHAT_ID_KEY, "")).strip()
    if not chat_id:
        return None
    runtime_config = runtime_config_lookup.get_runtime_config_by_trigger_id(trigger_id)
    if runtime_config is None:
        return None
    chat_type = str(metadata.get(FEISHU_METADATA_CHAT_TYPE_KEY, "")).strip()
    message_id = str(metadata.get(FEISHU_METADATA_MESSAGE_ID_KEY, "")).strip() or None
    return FeishuChatContext(
        chat_id=chat_id,
        environment=runtime_config.environment,
        chat_type=chat_type,
        reply_to_message_id=message_id,
        prefer_reply=(not prefer_direct_send and message_id is not None),
    )


def _resolve_automation_binding_context(
    *,
    session: SessionRecord,
    runtime_config_lookup: _RuntimeConfigLookup,
    automation_project_repo: _AutomationProjectLookup | None,
    prefer_direct_send: bool,
) -> FeishuChatContext | None:
    if (
        session.project_kind != ProjectKind.AUTOMATION
        or automation_project_repo is None
    ):
        return None
    automation_project_id = str(session.project_id or "").strip()
    if not automation_project_id:
        return None
    try:
        project = automation_project_repo.get(automation_project_id)
    except KeyError:
        return None
    binding = project.delivery_binding
    if binding is None:
        return None
    runtime_config = runtime_config_lookup.get_runtime_config_by_trigger_id(
        binding.trigger_id
    )
    if runtime_config is None:
        return None
    return FeishuChatContext(
        chat_id=binding.chat_id,
        environment=runtime_config.environment,
        chat_type=binding.chat_type,
        prefer_reply=False,
    )
