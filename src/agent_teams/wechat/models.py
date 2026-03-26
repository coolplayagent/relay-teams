# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.sessions.runs.run_models import RunThinkingConfig
from agent_teams.sessions.session_models import SessionMode

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


class WeChatAccountRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    base_url: str = Field(min_length=1, default=DEFAULT_WECHAT_BASE_URL)
    cdn_base_url: str = Field(min_length=1, default=DEFAULT_WECHAT_CDN_BASE_URL)
    route_tag: str | None = None
    status: WeChatAccountStatus = WeChatAccountStatus.ENABLED
    remote_user_id: str | None = None
    sync_cursor: str = ""
    workspace_id: str = Field(min_length=1, default="default")
    session_mode: SessionMode = SessionMode.NORMAL
    normal_root_role_id: str | None = None
    orchestration_preset_id: str | None = None
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
    workspace_id: str | None = None
    session_mode: SessionMode | None = None
    normal_root_role_id: str | None = None
    orchestration_preset_id: str | None = None
    yolo: bool | None = None
    thinking: RunThinkingConfig | None = None


class WeChatLoginStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str | None = None
    route_tag: str | None = None
    bot_type: str = DEFAULT_WECHAT_BOT_TYPE


class WeChatLoginStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_key: str = Field(min_length=1)
    qr_code_url: str | None = None
    message: str = Field(min_length=1)


class WeChatLoginWaitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_key: str = Field(min_length=1)
    timeout_ms: int = Field(default=480000, ge=1000, le=900000)


class WeChatLoginWaitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connected: bool
    account_id: str | None = None
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


class WeChatGatewaySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1)
    running: bool = False
    last_error: str | None = None
    last_event_at: datetime | None = None
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None


class WeChatLoginSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_key: str = Field(min_length=1)
    qrcode: str = Field(min_length=1)
    qr_code_url: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    route_tag: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
