# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.feishu.client import FeishuClient, load_feishu_environment
    from agent_teams.feishu.models import (
        FEISHU_METADATA_CHAT_ID_KEY,
        FEISHU_METADATA_CHAT_TYPE_KEY,
        FEISHU_METADATA_PLATFORM_KEY,
        FEISHU_METADATA_TENANT_KEY,
        FEISHU_METADATA_TRIGGER_ID_KEY,
        FEISHU_PLATFORM,
        FeishuChatQueueClearResult,
        FeishuChatQueueItemPreview,
        FeishuChatQueueSummary,
        FeishuMessageDeliveryStatus,
        SESSION_METADATA_SOURCE_ICON_KEY,
        SESSION_METADATA_SOURCE_KIND_KEY,
        SESSION_METADATA_SOURCE_LABEL_KEY,
        SESSION_METADATA_SOURCE_PROVIDER_KEY,
        SESSION_METADATA_TITLE_SOURCE_KEY,
        SESSION_SOURCE_ICON_IM,
        SESSION_SOURCE_KIND_IM,
        SESSION_TITLE_SOURCE_AUTO,
        SESSION_TITLE_SOURCE_MANUAL,
        FeishuEnvironment,
        FeishuMessageFormat,
        FeishuMessagePoolRecord,
        FeishuMessageProcessingStatus,
        FeishuNormalizedMessage,
        FeishuNotificationTarget,
        TriggerProcessingResult,
    )
    from agent_teams.feishu.inbound_runtime import FeishuInboundRuntime
    from agent_teams.feishu.message_pool_repository import FeishuMessagePoolRepository
    from agent_teams.feishu.message_pool_service import FeishuMessagePoolService
    from agent_teams.feishu.notification_delivery import FeishuNotificationDispatcher
    from agent_teams.feishu.subscription_service import FeishuSubscriptionService
    from agent_teams.feishu.trigger_config_service import FeishuTriggerConfigService
    from agent_teams.feishu.trigger_handler import FeishuTriggerHandler

__all__ = [
    "FEISHU_METADATA_CHAT_ID_KEY",
    "FEISHU_METADATA_CHAT_TYPE_KEY",
    "FEISHU_METADATA_PLATFORM_KEY",
    "FEISHU_METADATA_TENANT_KEY",
    "FEISHU_METADATA_TRIGGER_ID_KEY",
    "FEISHU_PLATFORM",
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
    "FeishuTriggerConfigService",
    "FeishuTriggerHandler",
    "TriggerProcessingResult",
    "load_feishu_environment",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "FEISHU_METADATA_CHAT_ID_KEY": (
        "agent_teams.feishu.models",
        "FEISHU_METADATA_CHAT_ID_KEY",
    ),
    "FEISHU_METADATA_CHAT_TYPE_KEY": (
        "agent_teams.feishu.models",
        "FEISHU_METADATA_CHAT_TYPE_KEY",
    ),
    "FEISHU_METADATA_PLATFORM_KEY": (
        "agent_teams.feishu.models",
        "FEISHU_METADATA_PLATFORM_KEY",
    ),
    "FEISHU_METADATA_TENANT_KEY": (
        "agent_teams.feishu.models",
        "FEISHU_METADATA_TENANT_KEY",
    ),
    "FEISHU_METADATA_TRIGGER_ID_KEY": (
        "agent_teams.feishu.models",
        "FEISHU_METADATA_TRIGGER_ID_KEY",
    ),
    "FEISHU_PLATFORM": ("agent_teams.feishu.models", "FEISHU_PLATFORM"),
    "FeishuInboundRuntime": (
        "agent_teams.feishu.inbound_runtime",
        "FeishuInboundRuntime",
    ),
    "SESSION_METADATA_SOURCE_ICON_KEY": (
        "agent_teams.feishu.models",
        "SESSION_METADATA_SOURCE_ICON_KEY",
    ),
    "SESSION_METADATA_SOURCE_KIND_KEY": (
        "agent_teams.feishu.models",
        "SESSION_METADATA_SOURCE_KIND_KEY",
    ),
    "SESSION_METADATA_SOURCE_LABEL_KEY": (
        "agent_teams.feishu.models",
        "SESSION_METADATA_SOURCE_LABEL_KEY",
    ),
    "SESSION_METADATA_SOURCE_PROVIDER_KEY": (
        "agent_teams.feishu.models",
        "SESSION_METADATA_SOURCE_PROVIDER_KEY",
    ),
    "SESSION_METADATA_TITLE_SOURCE_KEY": (
        "agent_teams.feishu.models",
        "SESSION_METADATA_TITLE_SOURCE_KEY",
    ),
    "SESSION_SOURCE_ICON_IM": (
        "agent_teams.feishu.models",
        "SESSION_SOURCE_ICON_IM",
    ),
    "SESSION_SOURCE_KIND_IM": (
        "agent_teams.feishu.models",
        "SESSION_SOURCE_KIND_IM",
    ),
    "SESSION_TITLE_SOURCE_AUTO": (
        "agent_teams.feishu.models",
        "SESSION_TITLE_SOURCE_AUTO",
    ),
    "SESSION_TITLE_SOURCE_MANUAL": (
        "agent_teams.feishu.models",
        "SESSION_TITLE_SOURCE_MANUAL",
    ),
    "FeishuClient": ("agent_teams.feishu.client", "FeishuClient"),
    "FeishuChatQueueClearResult": (
        "agent_teams.feishu.models",
        "FeishuChatQueueClearResult",
    ),
    "FeishuChatQueueItemPreview": (
        "agent_teams.feishu.models",
        "FeishuChatQueueItemPreview",
    ),
    "FeishuChatQueueSummary": (
        "agent_teams.feishu.models",
        "FeishuChatQueueSummary",
    ),
    "FeishuEnvironment": ("agent_teams.feishu.models", "FeishuEnvironment"),
    "FeishuMessageDeliveryStatus": (
        "agent_teams.feishu.models",
        "FeishuMessageDeliveryStatus",
    ),
    "FeishuMessageFormat": ("agent_teams.feishu.models", "FeishuMessageFormat"),
    "FeishuMessagePoolRecord": (
        "agent_teams.feishu.models",
        "FeishuMessagePoolRecord",
    ),
    "FeishuMessagePoolRepository": (
        "agent_teams.feishu.message_pool_repository",
        "FeishuMessagePoolRepository",
    ),
    "FeishuMessagePoolService": (
        "agent_teams.feishu.message_pool_service",
        "FeishuMessagePoolService",
    ),
    "FeishuMessageProcessingStatus": (
        "agent_teams.feishu.models",
        "FeishuMessageProcessingStatus",
    ),
    "FeishuNormalizedMessage": (
        "agent_teams.feishu.models",
        "FeishuNormalizedMessage",
    ),
    "FeishuNotificationDispatcher": (
        "agent_teams.feishu.notification_delivery",
        "FeishuNotificationDispatcher",
    ),
    "FeishuNotificationTarget": (
        "agent_teams.feishu.models",
        "FeishuNotificationTarget",
    ),
    "FeishuSubscriptionService": (
        "agent_teams.feishu.subscription_service",
        "FeishuSubscriptionService",
    ),
    "FeishuTriggerConfigService": (
        "agent_teams.feishu.trigger_config_service",
        "FeishuTriggerConfigService",
    ),
    "FeishuTriggerHandler": (
        "agent_teams.feishu.trigger_handler",
        "FeishuTriggerHandler",
    ),
    "TriggerProcessingResult": (
        "agent_teams.feishu.models",
        "TriggerProcessingResult",
    ),
    "load_feishu_environment": (
        "agent_teams.feishu.client",
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
