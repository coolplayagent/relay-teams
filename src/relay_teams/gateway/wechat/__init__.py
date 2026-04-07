# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from relay_teams.gateway.wechat.account_repository import WeChatAccountRepository
    from relay_teams.gateway.wechat.client import WeChatClient
    from relay_teams.gateway.wechat.models import (
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
    from relay_teams.gateway.wechat.inbound_queue_repository import (
        WeChatInboundQueueRepository,
    )
    from relay_teams.gateway.wechat.secret_store import (
        WeChatSecretStore,
        get_wechat_secret_store,
    )
    from relay_teams.gateway.wechat.service import WeChatGatewayService

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
        "relay_teams.gateway.wechat.models",
        "DEFAULT_WECHAT_BASE_URL",
    ),
    "DEFAULT_WECHAT_BOT_TYPE": (
        "relay_teams.gateway.wechat.models",
        "DEFAULT_WECHAT_BOT_TYPE",
    ),
    "DEFAULT_WECHAT_CDN_BASE_URL": (
        "relay_teams.gateway.wechat.models",
        "DEFAULT_WECHAT_CDN_BASE_URL",
    ),
    "WECHAT_PLATFORM": ("relay_teams.gateway.wechat.models", "WECHAT_PLATFORM"),
    "WeChatAccountRecord": (
        "relay_teams.gateway.wechat.models",
        "WeChatAccountRecord",
    ),
    "WeChatAccountRepository": (
        "relay_teams.gateway.wechat.account_repository",
        "WeChatAccountRepository",
    ),
    "WeChatAccountStatus": (
        "relay_teams.gateway.wechat.models",
        "WeChatAccountStatus",
    ),
    "WeChatAccountUpdateInput": (
        "relay_teams.gateway.wechat.models",
        "WeChatAccountUpdateInput",
    ),
    "WeChatClient": ("relay_teams.gateway.wechat.client", "WeChatClient"),
    "WeChatGatewayService": (
        "relay_teams.gateway.wechat.service",
        "WeChatGatewayService",
    ),
    "WeChatGatewaySnapshot": (
        "relay_teams.gateway.wechat.models",
        "WeChatGatewaySnapshot",
    ),
    "WeChatInboundQueueRecord": (
        "relay_teams.gateway.wechat.models",
        "WeChatInboundQueueRecord",
    ),
    "WeChatInboundQueueRepository": (
        "relay_teams.gateway.wechat.inbound_queue_repository",
        "WeChatInboundQueueRepository",
    ),
    "WeChatInboundQueueStatus": (
        "relay_teams.gateway.wechat.models",
        "WeChatInboundQueueStatus",
    ),
    "WeChatLoginStartRequest": (
        "relay_teams.gateway.wechat.models",
        "WeChatLoginStartRequest",
    ),
    "WeChatLoginStartResponse": (
        "relay_teams.gateway.wechat.models",
        "WeChatLoginStartResponse",
    ),
    "WeChatLoginWaitRequest": (
        "relay_teams.gateway.wechat.models",
        "WeChatLoginWaitRequest",
    ),
    "WeChatLoginWaitResponse": (
        "relay_teams.gateway.wechat.models",
        "WeChatLoginWaitResponse",
    ),
    "WeChatSecretStore": (
        "relay_teams.gateway.wechat.secret_store",
        "WeChatSecretStore",
    ),
    "get_wechat_secret_store": (
        "relay_teams.gateway.wechat.secret_store",
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
