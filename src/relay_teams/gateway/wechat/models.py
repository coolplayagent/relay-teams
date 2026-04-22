# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from relay_teams.validation import (
    normalize_optional_string,
    require_non_empty_patch,
)
from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.sessions.session_models import SessionMode
from relay_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr

WECHAT_PLATFORM = "wechat"
DEFAULT_WECHAT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_WECHAT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
DEFAULT_WECHAT_BOT_TYPE = "3"


class WeChatAccountStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class WeChatChatType(str, Enum):
    DIRECT = "direct"
    GROUP = "group"


class WeChatUploadMediaType(int, Enum):
    IMAGE = 1
    VIDEO = 2
    FILE = 3
    VOICE = 4


class WeChatInboundQueueStatus(str, Enum):
    QUEUED = "queued"
    STARTING = "starting"
    WAITING_RESULT = "waiting_result"
    COMPLETED = "completed"
    FAILED = "failed"


class WeChatAccountRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: RequiredIdentifierStr
    display_name: str = Field(min_length=1)
    base_url: str = Field(min_length=1, default=DEFAULT_WECHAT_BASE_URL)
    cdn_base_url: str = Field(min_length=1, default=DEFAULT_WECHAT_CDN_BASE_URL)
    route_tag: str | None = None
    status: WeChatAccountStatus = WeChatAccountStatus.ENABLED
    remote_user_id: str | None = None
    sync_cursor: str = ""
    workspace_id: RequiredIdentifierStr = "default"
    session_mode: SessionMode = SessionMode.NORMAL
    normal_root_role_id: OptionalIdentifierStr = None
    orchestration_preset_id: OptionalIdentifierStr = None
    yolo: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)
    last_login_at: datetime | None = None
    last_error: str | None = None
    last_event_at: datetime | None = None
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None
    running: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class WeChatAccountUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    base_url: str | None = None
    cdn_base_url: str | None = None
    route_tag: str | None = None
    enabled: bool | None = None
    workspace_id: OptionalIdentifierStr = None
    session_mode: SessionMode | None = None
    normal_root_role_id: OptionalIdentifierStr = None
    orchestration_preset_id: OptionalIdentifierStr = None
    yolo: bool | None = None
    thinking: RunThinkingConfig | None = None

    @field_validator("display_name", "base_url", "cdn_base_url")
    @classmethod
    def _normalize_optional_text(cls, value: str | None, info) -> str | None:
        return normalize_optional_string(
            value,
            field_name=info.field_name,
        )

    @field_validator("route_tag")
    @classmethod
    def _normalize_route_tag(cls, value: str | None) -> str | None:
        return normalize_optional_string(
            value,
            field_name="route_tag",
            empty_to_none=True,
        )

    @model_validator(mode="after")
    def _validate_patch(self) -> WeChatAccountUpdateInput:
        require_non_empty_patch(self)
        return self


class WeChatLoginStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str | None = None
    route_tag: str | None = None
    bot_type: str = DEFAULT_WECHAT_BOT_TYPE


class WeChatLoginStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_key: RequiredIdentifierStr
    qr_code_url: str | None = None
    message: str = Field(min_length=1)


class WeChatLoginWaitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_key: RequiredIdentifierStr
    timeout_ms: int = Field(default=480000, ge=1000, le=900000)


class WeChatLoginWaitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connected: bool
    account_id: OptionalIdentifierStr = None
    message: str = Field(min_length=1)


class WeChatBaseInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_version: str = "agent-teams"


class WeChatCdnMedia(BaseModel):
    model_config = ConfigDict(extra="ignore")

    encrypt_query_param: str | None = None
    aes_key: str | None = None
    encrypt_type: int | None = None


class WeChatTextItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = ""


class WeChatImageItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    media: WeChatCdnMedia | None = None
    thumb_media: WeChatCdnMedia | None = None
    aeskey: str | None = None
    url: str | None = None


class WeChatFileItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    media: WeChatCdnMedia | None = None
    file_name: str | None = None
    md5: str | None = None
    length: str | None = Field(default=None, alias="len")


class WeChatVideoItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    media: WeChatCdnMedia | None = None
    thumb_media: WeChatCdnMedia | None = None
    video_size: int | None = None
    play_length: int | None = None
    video_md5: str | None = None


class WeChatVoiceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    media: WeChatCdnMedia | None = None
    text: str | None = None


class WeChatMessageItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: int
    text_item: WeChatTextItem | None = None
    image_item: WeChatImageItem | None = None
    voice_item: WeChatVoiceItem | None = None
    file_item: WeChatFileItem | None = None
    video_item: WeChatVideoItem | None = None


class WeChatInboundMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    seq: int | None = None
    message_id: int | None = None
    from_user_id: str | None = None
    to_user_id: str | None = None
    create_time_ms: int | None = None
    session_id: str | None = None
    group_id: str | None = None
    message_type: int | None = None
    message_state: int | None = None
    item_list: tuple[WeChatMessageItem, ...] = ()
    context_token: str | None = None


class WeChatInboundQueueRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inbound_queue_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    message_key: str = Field(min_length=1)
    gateway_session_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    peer_user_id: str = Field(min_length=1)
    context_token: str | None = None
    text: str = Field(min_length=1)
    status: WeChatInboundQueueStatus = WeChatInboundQueueStatus.QUEUED
    run_id: str | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    completed_at: datetime | None = None


class WeChatGetUpdatesResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ret: int = 0
    errcode: int | None = None
    errmsg: str | None = None
    msgs: tuple[WeChatInboundMessage, ...] = ()
    get_updates_buf: str = ""
    longpolling_timeout_ms: int | None = None


class WeChatQrCodeResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ret: int = 0
    errcode: int | None = None
    errmsg: str | None = None
    qrcode: str = Field(min_length=1)
    qrcode_img_content: str = Field(min_length=1)


class WeChatQrStatusResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ret: int = 0
    errcode: int | None = None
    errmsg: str | None = None
    status: Literal["wait", "scaned", "confirmed", "expired"]
    bot_token: str | None = None
    ilink_bot_id: str | None = None
    baseurl: str | None = None
    ilink_user_id: str | None = None


class WeChatTypingConfigResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ret: int = 0
    errmsg: str | None = None
    typing_ticket: str | None = None


class WeChatOperationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ret: int = 0
    errcode: int | None = None
    errmsg: str | None = None


class WeChatUploadUrlResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ret: int = 0
    errcode: int | None = None
    errmsg: str | None = None
    upload_param: str | None = None
    thumb_upload_param: str | None = None
    upload_full_url: str | None = None
    thumb_upload_full_url: str | None = None


class WeChatUploadedMedia(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filekey: str = Field(min_length=1)
    download_encrypted_query_param: str = Field(min_length=1)
    aes_key_hex: str = Field(min_length=32, max_length=32)
    file_size: int = Field(ge=0)
    file_size_ciphertext: int = Field(ge=0)


class WeChatGatewaySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: RequiredIdentifierStr
    running: bool = False
    last_error: str | None = None
    last_event_at: datetime | None = None
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None


class WeChatLoginSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_key: RequiredIdentifierStr
    qrcode: str = Field(min_length=1)
    qr_code_url: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    route_tag: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
