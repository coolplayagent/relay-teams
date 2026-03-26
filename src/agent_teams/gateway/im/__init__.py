# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.gateway.im.command_service import ImSessionCommandService
    from agent_teams.gateway.im.context import (
        FeishuChatContext,
        ImToolContextResolver,
        WeChatChatContext,
        resolve_feishu_chat_context,
        resolve_im_chat_context,
        resolve_wechat_chat_context,
    )
    from agent_teams.gateway.im.service import ImToolService

__all__ = [
    "FeishuChatContext",
    "ImSessionCommandService",
    "ImToolContextResolver",
    "ImToolService",
    "WeChatChatContext",
    "resolve_feishu_chat_context",
    "resolve_im_chat_context",
    "resolve_wechat_chat_context",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "FeishuChatContext": ("agent_teams.gateway.im.context", "FeishuChatContext"),
    "ImSessionCommandService": (
        "agent_teams.gateway.im.command_service",
        "ImSessionCommandService",
    ),
    "ImToolContextResolver": (
        "agent_teams.gateway.im.context",
        "ImToolContextResolver",
    ),
    "ImToolService": ("agent_teams.gateway.im.service", "ImToolService"),
    "WeChatChatContext": ("agent_teams.gateway.im.context", "WeChatChatContext"),
    "resolve_feishu_chat_context": (
        "agent_teams.gateway.im.context",
        "resolve_feishu_chat_context",
    ),
    "resolve_im_chat_context": (
        "agent_teams.gateway.im.context",
        "resolve_im_chat_context",
    ),
    "resolve_wechat_chat_context": (
        "agent_teams.gateway.im.context",
        "resolve_wechat_chat_context",
    ),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
