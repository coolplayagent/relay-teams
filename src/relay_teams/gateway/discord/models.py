# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.sessions.session_models import SessionMode
from relay_teams.validation import (
    OptionalIdentifierStr,
    RequiredIdentifierStr,
    normalize_optional_string,
    require_non_empty_patch,
)

DISCORD_PLATFORM = "discord"


class DiscordAccountStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class DiscordChatType(str, Enum):
    DIRECT = "direct"
    GUILD = "guild"


class DiscordInboundQueueStatus(str, Enum):
    QUEUED = "queued"
    STARTING = "starting"
    WAITING_RESULT = "waiting_result"
    COMPLETED = "completed"
    FAILED = "failed"


class DiscordSecretStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bot_token_configured: bool = False


class DiscordAccountRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: RequiredIdentifierStr
    display_name: str = Field(min_length=1)
    status: DiscordAccountStatus = DiscordAccountStatus.ENABLED
    bot_user_id: OptionalIdentifierStr = None
    application_id: OptionalIdentifierStr = None
    allowed_channel_ids: tuple[RequiredIdentifierStr, ...] = ()
    allow_channel_messages: bool = False
    workspace_id: RequiredIdentifierStr = "default"
    session_mode: SessionMode = SessionMode.NORMAL
    normal_root_role_id: OptionalIdentifierStr = None
    orchestration_preset_id: OptionalIdentifierStr = None
    yolo: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)
    secret_status: DiscordSecretStatus = Field(default_factory=DiscordSecretStatus)
    last_error: str | None = None
    last_event_at: datetime | None = None
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None
    running: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class DiscordAccountCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    bot_token: str = Field(min_length=1)
    application_id: OptionalIdentifierStr = None
    enabled: bool = True
    allowed_channel_ids: tuple[RequiredIdentifierStr, ...] = ()
    allow_channel_messages: bool = False
    workspace_id: RequiredIdentifierStr = "default"
    session_mode: SessionMode = SessionMode.NORMAL
    normal_root_role_id: OptionalIdentifierStr = None
    orchestration_preset_id: OptionalIdentifierStr = None
    yolo: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)

    @field_validator("display_name", "bot_token")
    @classmethod
    def _normalize_optional_text(cls, value: str | None, info) -> str | None:
        return normalize_optional_string(
            value,
            field_name=info.field_name,
            empty_to_none=(info.field_name == "display_name"),
        )


class DiscordAccountUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    bot_token: str | None = None
    application_id: OptionalIdentifierStr = None
    enabled: bool | None = None
    allowed_channel_ids: tuple[RequiredIdentifierStr, ...] | None = None
    allow_channel_messages: bool | None = None
    workspace_id: OptionalIdentifierStr = None
    session_mode: SessionMode | None = None
    normal_root_role_id: OptionalIdentifierStr = None
    orchestration_preset_id: OptionalIdentifierStr = None
    yolo: bool | None = None
    thinking: RunThinkingConfig | None = None

    @field_validator("display_name", "bot_token")
    @classmethod
    def _normalize_optional_text(cls, value: str | None, info) -> str | None:
        return normalize_optional_string(
            value,
            field_name=info.field_name,
            empty_to_none=(info.field_name == "bot_token"),
        )

    @model_validator(mode="after")
    def _validate_patch(self) -> DiscordAccountUpdateInput:
        require_non_empty_patch(self)
        return self


class DiscordBotIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: RequiredIdentifierStr
    username: str = Field(min_length=1)
    application_id: OptionalIdentifierStr = None


class DiscordInboundMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: RequiredIdentifierStr
    channel_id: RequiredIdentifierStr
    author_id: RequiredIdentifierStr
    author_name: str = ""
    content: str = ""
    guild_id: OptionalIdentifierStr = None
    thread_id: OptionalIdentifierStr = None
    mentions_bot: bool = False
    is_dm: bool = False
    author_is_bot: bool = False


class DiscordInboundQueueRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inbound_queue_id: RequiredIdentifierStr
    account_id: RequiredIdentifierStr
    message_key: RequiredIdentifierStr
    gateway_session_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    peer_user_id: RequiredIdentifierStr
    channel_id: RequiredIdentifierStr
    guild_id: OptionalIdentifierStr = None
    thread_id: OptionalIdentifierStr = None
    reply_to_message_id: OptionalIdentifierStr = None
    text: str = Field(min_length=1)
    status: DiscordInboundQueueStatus = DiscordInboundQueueStatus.QUEUED
    run_id: OptionalIdentifierStr = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    completed_at: datetime | None = None
