# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from relay_teams.gateway.feishu.account_repository import (
        FeishuAccountNameConflictError,
        FeishuAccountRepository,
    )
    from relay_teams.gateway.feishu.client import (
        FeishuClient,
        load_feishu_environment,
    )
    from relay_teams.gateway.feishu.gateway_service import FeishuGatewayService
    from relay_teams.gateway.feishu.inbound_runtime import FeishuInboundRuntime
    from relay_teams.gateway.feishu.message_pool_repository import (
        FeishuMessagePoolRepository,
    )
    from relay_teams.gateway.feishu.message_pool_service import FeishuMessagePoolService
    from relay_teams.gateway.feishu.models import (
        FEISHU_METADATA_CHAT_ID_KEY,
        FEISHU_METADATA_CHAT_TYPE_KEY,
        FEISHU_METADATA_ACCOUNT_ID_KEY,
        FEISHU_METADATA_PLATFORM_KEY,
        FEISHU_METADATA_TENANT_KEY,
        FEISHU_METADATA_TRIGGER_ID_KEY,
        FEISHU_PLATFORM,
        SESSION_METADATA_SOURCE_ICON_KEY,
        SESSION_METADATA_SOURCE_KIND_KEY,
        SESSION_METADATA_SOURCE_LABEL_KEY,
        SESSION_METADATA_SOURCE_PROVIDER_KEY,
        SESSION_METADATA_TITLE_SOURCE_KEY,
        SESSION_SOURCE_ICON_IM,
        SESSION_SOURCE_KIND_IM,
        SESSION_TITLE_SOURCE_AUTO,
        SESSION_TITLE_SOURCE_MANUAL,
        FeishuChatQueueClearResult,
        FeishuChatQueueItemPreview,
        FeishuChatQueueSummary,
        FeishuEnvironment,
        FeishuGatewayAccountCreateInput,
        FeishuGatewayAccountRecord,
        FeishuGatewayAccountStatus,
        FeishuGatewayAccountUpdateInput,
        FeishuMessageDeliveryStatus,
        FeishuMessageFormat,
        FeishuMessagePoolRecord,
        FeishuMessageProcessingStatus,
        FeishuNormalizedMessage,
        FeishuNotificationTarget,
        TriggerProcessingResult,
    )
    from relay_teams.gateway.feishu.notification_delivery import (
        FeishuNotificationDispatcher,
    )
    from relay_teams.gateway.feishu.subscription_service import (
        FeishuSubscriptionService,
    )
    from relay_teams.gateway.feishu.trigger_handler import FeishuTriggerHandler

__all__ = [
    "FEISHU_METADATA_ACCOUNT_ID_KEY",
    "FEISHU_METADATA_CHAT_ID_KEY",
    "FEISHU_METADATA_CHAT_TYPE_KEY",
    "FEISHU_METADATA_PLATFORM_KEY",
    "FEISHU_METADATA_TENANT_KEY",
    "FEISHU_METADATA_TRIGGER_ID_KEY",
    "FEISHU_PLATFORM",
    "FeishuAccountNameConflictError",
    "FeishuAccountRepository",
    "FeishuChatQueueClearResult",
    "FeishuChatQueueItemPreview",
    "FeishuChatQueueSummary",
    "FeishuInboundRuntime",
    "SESSION_METADATA_SOURCE_ICON_KEY",
    "SESSION_METADATA_SOURCE_KIND_KEY",
    "SESSION_METADATA_SOURCE_LABEL_KEY",
    "SESSION_METADATA_SOURCE_PROVIDER_KEY",
    "SESSION_METADATA_TITLE_SOURCE_KEY",
    "SESSION_SOURCE_ICON_IM",
    "SESSION_SOURCE_KIND_IM",
    "SESSION_TITLE_SOURCE_AUTO",
    "SESSION_TITLE_SOURCE_MANUAL",
    "FeishuClient",
    "FeishuEnvironment",
    "FeishuGatewayAccountCreateInput",
    "FeishuGatewayAccountRecord",
    "FeishuGatewayAccountStatus",
    "FeishuGatewayAccountUpdateInput",
    "FeishuGatewayService",
    "FeishuMessageDeliveryStatus",
    "FeishuMessageFormat",
    "FeishuMessagePoolRecord",
    "FeishuMessagePoolRepository",
    "FeishuMessagePoolService",
    "FeishuMessageProcessingStatus",
    "FeishuNormalizedMessage",
    "FeishuNotificationDispatcher",
    "FeishuNotificationTarget",
    "FeishuSubscriptionService",
    "FeishuTriggerHandler",
    "TriggerProcessingResult",
    "load_feishu_environment",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "FEISHU_METADATA_ACCOUNT_ID_KEY": (
        "relay_teams.gateway.feishu.models",
        "FEISHU_METADATA_ACCOUNT_ID_KEY",
    ),
    "FEISHU_METADATA_CHAT_ID_KEY": (
        "relay_teams.gateway.feishu.models",
        "FEISHU_METADATA_CHAT_ID_KEY",
    ),
    "FEISHU_METADATA_CHAT_TYPE_KEY": (
        "relay_teams.gateway.feishu.models",
        "FEISHU_METADATA_CHAT_TYPE_KEY",
    ),
    "FEISHU_METADATA_PLATFORM_KEY": (
        "relay_teams.gateway.feishu.models",
        "FEISHU_METADATA_PLATFORM_KEY",
    ),
    "FEISHU_METADATA_TENANT_KEY": (
        "relay_teams.gateway.feishu.models",
        "FEISHU_METADATA_TENANT_KEY",
    ),
    "FEISHU_METADATA_TRIGGER_ID_KEY": (
        "relay_teams.gateway.feishu.models",
        "FEISHU_METADATA_TRIGGER_ID_KEY",
    ),
    "FEISHU_PLATFORM": ("relay_teams.gateway.feishu.models", "FEISHU_PLATFORM"),
    "FeishuAccountNameConflictError": (
        "relay_teams.gateway.feishu.account_repository",
        "FeishuAccountNameConflictError",
    ),
    "FeishuAccountRepository": (
        "relay_teams.gateway.feishu.account_repository",
        "FeishuAccountRepository",
    ),
    "FeishuInboundRuntime": (
        "relay_teams.gateway.feishu.inbound_runtime",
        "FeishuInboundRuntime",
    ),
    "SESSION_METADATA_SOURCE_ICON_KEY": (
        "relay_teams.gateway.feishu.models",
        "SESSION_METADATA_SOURCE_ICON_KEY",
    ),
    "SESSION_METADATA_SOURCE_KIND_KEY": (
        "relay_teams.gateway.feishu.models",
        "SESSION_METADATA_SOURCE_KIND_KEY",
    ),
    "SESSION_METADATA_SOURCE_LABEL_KEY": (
        "relay_teams.gateway.feishu.models",
        "SESSION_METADATA_SOURCE_LABEL_KEY",
    ),
    "SESSION_METADATA_SOURCE_PROVIDER_KEY": (
        "relay_teams.gateway.feishu.models",
        "SESSION_METADATA_SOURCE_PROVIDER_KEY",
    ),
    "SESSION_METADATA_TITLE_SOURCE_KEY": (
        "relay_teams.gateway.feishu.models",
        "SESSION_METADATA_TITLE_SOURCE_KEY",
    ),
    "SESSION_SOURCE_ICON_IM": (
        "relay_teams.gateway.feishu.models",
        "SESSION_SOURCE_ICON_IM",
    ),
    "SESSION_SOURCE_KIND_IM": (
        "relay_teams.gateway.feishu.models",
        "SESSION_SOURCE_KIND_IM",
    ),
    "SESSION_TITLE_SOURCE_AUTO": (
        "relay_teams.gateway.feishu.models",
        "SESSION_TITLE_SOURCE_AUTO",
    ),
    "SESSION_TITLE_SOURCE_MANUAL": (
        "relay_teams.gateway.feishu.models",
        "SESSION_TITLE_SOURCE_MANUAL",
    ),
    "FeishuClient": ("relay_teams.gateway.feishu.client", "FeishuClient"),
    "FeishuChatQueueClearResult": (
        "relay_teams.gateway.feishu.models",
        "FeishuChatQueueClearResult",
    ),
    "FeishuChatQueueItemPreview": (
        "relay_teams.gateway.feishu.models",
        "FeishuChatQueueItemPreview",
    ),
    "FeishuChatQueueSummary": (
        "relay_teams.gateway.feishu.models",
        "FeishuChatQueueSummary",
    ),
    "FeishuEnvironment": ("relay_teams.gateway.feishu.models", "FeishuEnvironment"),
    "FeishuGatewayAccountCreateInput": (
        "relay_teams.gateway.feishu.models",
        "FeishuGatewayAccountCreateInput",
    ),
    "FeishuGatewayAccountRecord": (
        "relay_teams.gateway.feishu.models",
        "FeishuGatewayAccountRecord",
    ),
    "FeishuGatewayAccountStatus": (
        "relay_teams.gateway.feishu.models",
        "FeishuGatewayAccountStatus",
    ),
    "FeishuGatewayAccountUpdateInput": (
        "relay_teams.gateway.feishu.models",
        "FeishuGatewayAccountUpdateInput",
    ),
    "FeishuGatewayService": (
        "relay_teams.gateway.feishu.gateway_service",
        "FeishuGatewayService",
    ),
    "FeishuMessageDeliveryStatus": (
        "relay_teams.gateway.feishu.models",
        "FeishuMessageDeliveryStatus",
    ),
    "FeishuMessageFormat": (
        "relay_teams.gateway.feishu.models",
        "FeishuMessageFormat",
    ),
    "FeishuMessagePoolRecord": (
        "relay_teams.gateway.feishu.models",
        "FeishuMessagePoolRecord",
    ),
    "FeishuMessagePoolRepository": (
        "relay_teams.gateway.feishu.message_pool_repository",
        "FeishuMessagePoolRepository",
    ),
    "FeishuMessagePoolService": (
        "relay_teams.gateway.feishu.message_pool_service",
        "FeishuMessagePoolService",
    ),
    "FeishuMessageProcessingStatus": (
        "relay_teams.gateway.feishu.models",
        "FeishuMessageProcessingStatus",
    ),
    "FeishuNormalizedMessage": (
        "relay_teams.gateway.feishu.models",
        "FeishuNormalizedMessage",
    ),
    "FeishuNotificationDispatcher": (
        "relay_teams.gateway.feishu.notification_delivery",
        "FeishuNotificationDispatcher",
    ),
    "FeishuNotificationTarget": (
        "relay_teams.gateway.feishu.models",
        "FeishuNotificationTarget",
    ),
    "FeishuSubscriptionService": (
        "relay_teams.gateway.feishu.subscription_service",
        "FeishuSubscriptionService",
    ),
    "FeishuTriggerHandler": (
        "relay_teams.gateway.feishu.trigger_handler",
        "FeishuTriggerHandler",
    ),
    "TriggerProcessingResult": (
        "relay_teams.gateway.feishu.models",
        "TriggerProcessingResult",
    ),
    "load_feishu_environment": (
        "relay_teams.gateway.feishu.client",
        "load_feishu_environment",
    ),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
