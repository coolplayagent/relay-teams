# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, JsonValue

FEISHU_PLATFORM = "feishu"
FEISHU_METADATA_PLATFORM_KEY = "feishu_platform"
FEISHU_METADATA_TENANT_KEY = "feishu_tenant_key"
FEISHU_METADATA_CHAT_ID_KEY = "feishu_chat_id"
FEISHU_METADATA_CHAT_TYPE_KEY = "feishu_chat_type"
FEISHU_METADATA_TRIGGER_ID_KEY = "feishu_trigger_id"


class FeishuMessageFormat(str, Enum):
    TEXT = "text"
    CARD = "card"


class FeishuEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: str = Field(min_length=1)
    app_secret: str = Field(min_length=1)
    app_name: str | None = None
    verification_token: str | None = None
    encrypt_key: str | None = None


class FeishuNotificationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_key: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)
    chat_type: str = Field(min_length=1)


class FeishuNormalizedMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    tenant_key: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)
    chat_type: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    message_type: str = Field(min_length=1)
    sender_type: str | None = None
    sender_open_id: str | None = None
    raw_text: str = ""
    trigger_text: str = ""
    mentioned: bool = False
    mention_names: tuple[str, ...] = ()
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)


class TriggerProcessingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1)
    trigger_id: str | None = None
    trigger_name: str | None = None
    event_id: str | None = None
    duplicate: bool = False
    session_id: str | None = None
    run_id: str | None = None
    ignored: bool = False
    reason: str | None = None
