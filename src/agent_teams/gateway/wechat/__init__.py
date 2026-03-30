# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.gateway.wechat.account_repository import WeChatAccountRepository
    from agent_teams.gateway.wechat.client import WeChatClient
    from agent_teams.gateway.wechat.models import (
        DEFAULT_WECHAT_BASE_URL,
        DEFAULT_WECHAT_BOT_TYPE,
        DEFAULT_WECHAT_CDN_BASE_URL,
        WECHAT_PLATFORM,
        WeChatAccountRecord,
        WeChatAccountStatus,
        WeChatAccountUpdateInput,
        WeChatGatewaySnapshot,
        WeChatLoginStartRequest,
        WeChatLoginStartResponse,
        WeChatInboundQueueRecord,
        WeChatInboundQueueStatus,
        WeChatLoginWaitRequest,
        WeChatLoginWaitResponse,
    )
    from agent_teams.gateway.wechat.inbound_queue_repository import (
        WeChatInboundQueueRepository,
    )
    from agent_teams.gateway.wechat.secret_store import (
        WeChatSecretStore,
        get_wechat_secret_store,
    )
    from agent_teams.gateway.wechat.service import WeChatGatewayService

__all__ = [
    "DEFAULT_WECHAT_BASE_URL",
    "DEFAULT_WECHAT_BOT_TYPE",
    "DEFAULT_WECHAT_CDN_BASE_URL",
    "WECHAT_PLATFORM",
    "WeChatAccountRecord",
    "WeChatAccountRepository",
    "WeChatAccountStatus",
    "WeChatAccountUpdateInput",
    "WeChatClient",
    "WeChatGatewayService",
    "WeChatGatewaySnapshot",
    "WeChatInboundQueueRecord",
    "WeChatInboundQueueRepository",
    "WeChatInboundQueueStatus",
    "WeChatLoginStartRequest",
    "WeChatLoginStartResponse",
    "WeChatLoginWaitRequest",
    "WeChatLoginWaitResponse",
    "WeChatSecretStore",
    "get_wechat_secret_store",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "DEFAULT_WECHAT_BASE_URL": (
        "agent_teams.gateway.wechat.models",
        "DEFAULT_WECHAT_BASE_URL",
    ),
    "DEFAULT_WECHAT_BOT_TYPE": (
        "agent_teams.gateway.wechat.models",
        "DEFAULT_WECHAT_BOT_TYPE",
    ),
    "DEFAULT_WECHAT_CDN_BASE_URL": (
        "agent_teams.gateway.wechat.models",
        "DEFAULT_WECHAT_CDN_BASE_URL",
    ),
    "WECHAT_PLATFORM": ("agent_teams.gateway.wechat.models", "WECHAT_PLATFORM"),
    "WeChatAccountRecord": (
        "agent_teams.gateway.wechat.models",
        "WeChatAccountRecord",
    ),
    "WeChatAccountRepository": (
        "agent_teams.gateway.wechat.account_repository",
        "WeChatAccountRepository",
    ),
    "WeChatAccountStatus": (
        "agent_teams.gateway.wechat.models",
        "WeChatAccountStatus",
    ),
    "WeChatAccountUpdateInput": (
        "agent_teams.gateway.wechat.models",
        "WeChatAccountUpdateInput",
    ),
    "WeChatClient": ("agent_teams.gateway.wechat.client", "WeChatClient"),
    "WeChatGatewayService": (
        "agent_teams.gateway.wechat.service",
        "WeChatGatewayService",
    ),
    "WeChatGatewaySnapshot": (
        "agent_teams.gateway.wechat.models",
        "WeChatGatewaySnapshot",
    ),
    "WeChatInboundQueueRecord": (
        "agent_teams.gateway.wechat.models",
        "WeChatInboundQueueRecord",
    ),
    "WeChatInboundQueueRepository": (
        "agent_teams.gateway.wechat.inbound_queue_repository",
        "WeChatInboundQueueRepository",
    ),
    "WeChatInboundQueueStatus": (
        "agent_teams.gateway.wechat.models",
        "WeChatInboundQueueStatus",
    ),
    "WeChatLoginStartRequest": (
        "agent_teams.gateway.wechat.models",
        "WeChatLoginStartRequest",
    ),
    "WeChatLoginStartResponse": (
        "agent_teams.gateway.wechat.models",
        "WeChatLoginStartResponse",
    ),
    "WeChatLoginWaitRequest": (
        "agent_teams.gateway.wechat.models",
        "WeChatLoginWaitRequest",
    ),
    "WeChatLoginWaitResponse": (
        "agent_teams.gateway.wechat.models",
        "WeChatLoginWaitResponse",
    ),
    "WeChatSecretStore": (
        "agent_teams.gateway.wechat.secret_store",
        "WeChatSecretStore",
    ),
    "get_wechat_secret_store": (
        "agent_teams.gateway.wechat.secret_store",
        "get_wechat_secret_store",
    ),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
