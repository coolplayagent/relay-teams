# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.sessions.session_models import SessionMode
from relay_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr

FEISHU_PLATFORM = "feishu"
FEISHU_METADATA_PLATFORM_KEY = "feishu_platform"
FEISHU_METADATA_TENANT_KEY = "feishu_tenant_key"
FEISHU_METADATA_CHAT_ID_KEY = "feishu_chat_id"
FEISHU_METADATA_CHAT_TYPE_KEY = "feishu_chat_type"
FEISHU_METADATA_TRIGGER_ID_KEY = "feishu_trigger_id"
FEISHU_METADATA_ACCOUNT_ID_KEY = "feishu_account_id"
FEISHU_METADATA_MESSAGE_ID_KEY = "feishu_message_id"
FEISHU_METADATA_SENDER_NAME_KEY = "feishu_sender_name"
FEISHU_METADATA_SENDER_OPEN_ID_KEY = "feishu_sender_open_id"
SESSION_METADATA_SOURCE_KIND_KEY = "source_kind"
SESSION_METADATA_SOURCE_PROVIDER_KEY = "source_provider"
SESSION_METADATA_SOURCE_LABEL_KEY = "source_label"
SESSION_METADATA_SOURCE_ICON_KEY = "source_icon"
SESSION_METADATA_TITLE_SOURCE_KEY = "title_source"

SESSION_SOURCE_KIND_IM = "im"
SESSION_SOURCE_ICON_IM = "im"
SESSION_TITLE_SOURCE_AUTO = "auto"
SESSION_TITLE_SOURCE_MANUAL = "manual"


class FeishuMessageFormat(str, Enum):
    TEXT = "text"
    CARD = "card"


class FeishuEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: RequiredIdentifierStr
    app_secret: str = Field(min_length=1)
    app_name: str | None = None
    verification_token: str | None = None
    encrypt_key: str | None = None


class FeishuNotificationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_key: RequiredIdentifierStr
    chat_id: RequiredIdentifierStr
    chat_type: str = Field(min_length=1)


class FeishuTriggerSecretConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_secret: str | None = None
    verification_token: str | None = None
    encrypt_key: str | None = None


class FeishuTriggerSecretStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_secret_configured: bool = False
    verification_token_configured: bool = False
    encrypt_key_configured: bool = False


class FeishuTriggerSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["feishu"] = FEISHU_PLATFORM
    trigger_rule: Literal["mention_only", "all_messages"] = "mention_only"
    app_id: RequiredIdentifierStr
    app_name: str = Field(min_length=1)


class FeishuTriggerTargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr = "default"
    session_mode: SessionMode = SessionMode.NORMAL
    normal_root_role_id: OptionalIdentifierStr = None
    orchestration_preset_id: OptionalIdentifierStr = None
    yolo: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)

    @model_validator(mode="after")
    def _validate_mode_settings(self) -> FeishuTriggerTargetConfig:
        if (
            self.session_mode == SessionMode.ORCHESTRATION
            and not str(self.orchestration_preset_id or "").strip()
        ):
            raise ValueError(
                "orchestration_preset_id is required in orchestration mode"
            )
        return self


class FeishuTriggerRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_id: RequiredIdentifierStr
    trigger_name: str = Field(min_length=1)
    source: FeishuTriggerSourceConfig
    target: FeishuTriggerTargetConfig
    environment: FeishuEnvironment

    @property
    def signature(self) -> tuple[str, str, str | None, str | None]:
        return (
            self.environment.app_id,
            self.environment.app_secret,
            self.environment.verification_token,
            self.environment.encrypt_key,
        )


class FeishuGatewayAccountStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class FeishuGatewayAccountCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: RequiredIdentifierStr
    display_name: str | None = None
    source_config: dict[str, JsonValue] = Field(default_factory=dict)
    target_config: dict[str, JsonValue] | None = None
    secret_config: dict[str, str] | None = None
    enabled: bool = True


class FeishuGatewayAccountUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: OptionalIdentifierStr = None
    display_name: str | None = None
    source_config: dict[str, JsonValue] | None = None
    target_config: dict[str, JsonValue] | None = None
    secret_config: dict[str, str] | None = None


class FeishuGatewayAccountRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: RequiredIdentifierStr
    name: RequiredIdentifierStr
    display_name: str = Field(min_length=1)
    status: FeishuGatewayAccountStatus
    source_config: dict[str, JsonValue] = Field(default_factory=dict)
    target_config: dict[str, JsonValue] | None = None
    secret_config: dict[str, str] | None = None
    secret_status: dict[str, bool] | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


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
    sender_name: str | None = None
    raw_text: str = ""
    trigger_text: str = ""
    mentioned: bool = False
    mention_names: tuple[str, ...] = ()
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)


class TriggerProcessingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1)
    trigger_id: OptionalIdentifierStr = None
    trigger_name: str | None = None
    event_id: OptionalIdentifierStr = None
    duplicate: bool = False
    session_id: OptionalIdentifierStr = None
    run_id: OptionalIdentifierStr = None
    ignored: bool = False
    reason: str | None = None


class FeishuMessageProcessingStatus(str, Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    WAITING_RESULT = "waiting_result"
    RETRYABLE_FAILED = "retryable_failed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    IGNORED = "ignored"
    DEAD_LETTER = "dead_letter"


class FeishuMessageDeliveryStatus(str, Enum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"


class FeishuMessagePoolRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence_id: int = Field(default=0, ge=0)
    message_pool_id: RequiredIdentifierStr
    trigger_id: RequiredIdentifierStr
    trigger_name: str = Field(min_length=1)
    tenant_key: RequiredIdentifierStr
    chat_id: RequiredIdentifierStr
    chat_type: str = Field(min_length=1)
    event_id: RequiredIdentifierStr
    message_key: RequiredIdentifierStr
    message_id: OptionalIdentifierStr = None
    command_name: str | None = None
    sender_name: str | None = None
    intent_text: str = ""
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)
    processing_status: FeishuMessageProcessingStatus = (
        FeishuMessageProcessingStatus.QUEUED
    )
    reaction_status: FeishuMessageDeliveryStatus = FeishuMessageDeliveryStatus.PENDING
    reaction_type: str | None = None
    reaction_attempts: int = Field(default=0, ge=0)
    ack_status: FeishuMessageDeliveryStatus = FeishuMessageDeliveryStatus.PENDING
    ack_text: str | None = None
    final_reply_status: FeishuMessageDeliveryStatus = (
        FeishuMessageDeliveryStatus.PENDING
    )
    final_reply_text: str | None = None
    delivery_count: int = Field(default=1, ge=1)
    process_attempts: int = Field(default=0, ge=0)
    ack_attempts: int = Field(default=0, ge=0)
    final_reply_attempts: int = Field(default=0, ge=0)
    session_id: OptionalIdentifierStr = None
    run_id: OptionalIdentifierStr = None
    next_attempt_at: datetime
    last_claimed_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class FeishuChatQueueItemPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message_pool_id: RequiredIdentifierStr
    processing_status: FeishuMessageProcessingStatus
    intent_preview: str = ""
    run_id: OptionalIdentifierStr = None
    run_status: str | None = None
    run_phase: str | None = None
    blocking_reason: str | None = None
    last_error: str | None = None


class FeishuChatQueueSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_id: RequiredIdentifierStr
    tenant_key: RequiredIdentifierStr
    chat_id: RequiredIdentifierStr
    active_total: int = Field(default=0, ge=0)
    queued_count: int = Field(default=0, ge=0)
    claimed_count: int = Field(default=0, ge=0)
    waiting_result_count: int = Field(default=0, ge=0)
    retryable_failed_count: int = Field(default=0, ge=0)
    cancelled_count: int = Field(default=0, ge=0)
    dead_letter_count: int = Field(default=0, ge=0)
    processing_item: FeishuChatQueueItemPreview | None = None
    queued_items: tuple[FeishuChatQueueItemPreview, ...] = ()


class FeishuChatQueueClearResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_id: RequiredIdentifierStr
    tenant_key: RequiredIdentifierStr
    chat_id: RequiredIdentifierStr
    cleared_queue_count: int = Field(default=0, ge=0)
    stopped_run_count: int = Field(default=0, ge=0)
