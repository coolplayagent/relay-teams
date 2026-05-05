# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from relay_teams.validation import (
    OptionalIdentifierStr,
    RequiredIdentifierStr,
    normalize_identifier_tuple,
    normalize_optional_string,
    require_non_empty_patch,
)

XIAOLUBAN_PLATFORM = "xiaoluban"
DEFAULT_XIAOLUBAN_BASE_URL = "http://xiaoluban.rnd.huawei.com:80/"


class XiaolubanAccountStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class XiaolubanSecretStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_configured: bool = False


class XiaolubanImConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workspace_id: OptionalIdentifierStr = None


class XiaolubanAccountRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: RequiredIdentifierStr
    display_name: str = Field(min_length=1)
    base_url: str = Field(min_length=1, default=DEFAULT_XIAOLUBAN_BASE_URL)
    status: XiaolubanAccountStatus = XiaolubanAccountStatus.ENABLED
    derived_uid: RequiredIdentifierStr
    notification_workspace_ids: tuple[RequiredIdentifierStr, ...] = ()
    notification_receivers: tuple[str, ...] = ()
    notify_self: bool = True
    notification_receiver: (str) | None = None
    im_config: XiaolubanImConfig = Field(default_factory=XiaolubanImConfig)
    secret_status: XiaolubanSecretStatus = Field(default_factory=XiaolubanSecretStatus)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @field_validator("notification_workspace_ids", mode="before")
    @classmethod
    def _normalize_workspace_ids(cls, value: object) -> tuple[str, ...]:
        normalized = normalize_identifier_tuple(
            value,
            field_name="notification_workspace_ids",
        )
        return () if normalized is None else normalized

    @field_validator("notification_receiver")
    @classmethod
    def _normalize_receiver(cls, value: (str) | None) -> (str) | None:
        return normalize_optional_string(value, field_name="notification_receiver")

    @field_validator("notification_receivers", mode="before")
    @classmethod
    def _normalize_receivers(cls, value: object) -> tuple[str, ...]:
        return normalize_xiaoluban_notification_receivers(value)

    @model_validator(mode="after")
    def _sync_legacy_receiver(self) -> XiaolubanAccountRecord:
        if "notification_receiver" in self.model_fields_set:
            if self.notification_receiver and not self.notification_receivers:
                self.notification_receivers = (self.notification_receiver,)
            elif not self.notification_receiver and not self.notification_receivers:
                self.notify_self = True
        self.notify_self = True
        self.notification_receiver = (
            self.notification_receivers[0] if self.notification_receivers else None
        )
        return self


class XiaolubanAccountCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: OptionalIdentifierStr = None
    display_name: str = Field(min_length=1)
    token: str = Field(min_length=1)
    base_url: str = Field(min_length=1, default=DEFAULT_XIAOLUBAN_BASE_URL)
    enabled: bool = True
    notification_workspace_ids: tuple[RequiredIdentifierStr, ...] = ()
    notification_receivers: tuple[str, ...] = ()
    notify_self: bool = True
    notification_receiver: (str) | None = None
    im_config: XiaolubanImConfig = Field(default_factory=XiaolubanImConfig)

    @field_validator("display_name", "token", "base_url")
    @classmethod
    def _normalize_text(cls, value: str, info) -> str:
        normalized = normalize_optional_string(value, field_name=info.field_name)
        if normalized is None:
            raise ValueError(f"{info.field_name} must not be empty")
        return normalized

    @field_validator("notification_workspace_ids", mode="before")
    @classmethod
    def _normalize_workspace_ids(cls, value: object) -> tuple[str, ...]:
        normalized = normalize_identifier_tuple(
            value,
            field_name="notification_workspace_ids",
        )
        return () if normalized is None else normalized

    @field_validator("notification_receiver")
    @classmethod
    def _normalize_receiver(cls, value: (str) | None) -> (str) | None:
        return normalize_optional_string(value, field_name="notification_receiver")

    @field_validator("notification_receivers", mode="before")
    @classmethod
    def _normalize_receivers(cls, value: object) -> tuple[str, ...]:
        return normalize_xiaoluban_notification_receivers(value)

    @model_validator(mode="after")
    def _sync_legacy_receiver(self) -> XiaolubanAccountCreateInput:
        if "notification_receiver" in self.model_fields_set:
            if self.notification_receiver:
                self.notification_receivers = (self.notification_receiver,)
            else:
                self.notification_receivers = ()
        self.notify_self = True
        self.notification_receiver = (
            self.notification_receivers[0] if self.notification_receivers else None
        )
        return self


class XiaolubanAccountUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: (str) | None = None
    token: (str) | None = None
    base_url: (str) | None = None
    enabled: (bool) | None = None
    notification_workspace_ids: (tuple[RequiredIdentifierStr, ...]) | None = None
    notification_receivers: (tuple[str, ...]) | None = None
    notify_self: (bool) | None = None
    notification_receiver: (str) | None = None
    im_config: (XiaolubanImConfig) | None = None

    @field_validator("display_name", "token", "base_url", "notification_receiver")
    @classmethod
    def _normalize_optional_text(cls, value: (str) | None, info) -> (str) | None:
        return normalize_optional_string(value, field_name=info.field_name)

    @field_validator("notification_workspace_ids", mode="before")
    @classmethod
    def _normalize_workspace_ids(cls, value: object) -> (tuple[str, ...]) | None:
        return normalize_identifier_tuple(
            value,
            field_name="notification_workspace_ids",
        )

    @field_validator("notification_receivers", mode="before")
    @classmethod
    def _normalize_receivers(cls, value: object) -> (tuple[str, ...]) | None:
        if value is None:
            return None
        return normalize_xiaoluban_notification_receivers(value)

    @model_validator(mode="after")
    def _validate_patch(self) -> XiaolubanAccountUpdateInput:
        if (
            "notification_receiver" in self.model_fields_set
            and self.notification_receiver
        ):
            self.notification_receivers = (self.notification_receiver,)
        if "notify_self" in self.model_fields_set:
            self.notify_self = True
        require_non_empty_patch(self)
        return self


class XiaolubanImConfigUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: OptionalIdentifierStr = None

    @model_validator(mode="after")
    def _validate_patch(self) -> XiaolubanImConfigUpdateInput:
        require_non_empty_patch(self)
        return self


class XiaolubanImForwardingCommandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: RequiredIdentifierStr
    forwarding_url: str = Field(min_length=1)
    forwarding_command: str = Field(min_length=1)
    listener_running: bool = False


class XiaolubanTokenRevealResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: (str) | None = None


class XiaolubanInboundMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str = ""
    receiver: str = ""
    sender: str = ""
    type: str = "Text"
    save_info: str = ""
    session_id: str = ""

    @field_validator("content", "receiver", "sender", "type", "save_info", "session_id")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return str(value or "").strip()


class XiaolubanKeepAliveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    save_info: str = ""
    minute: int = Field(ge=1)
    auth: str = Field(min_length=1)


class XiaolubanSendTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)
    receiver: str = Field(min_length=1)
    auth: str = Field(min_length=1)
    sender: (str) | None = None


class XiaolubanSendTextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1)
    raw_response: (str) | None = None


def normalize_xiaoluban_notification_receivers(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    raw_items: list[str] = []
    if isinstance(value, str):
        raw_items = _split_notification_receivers(value)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            if isinstance(item, str):
                raw_items.extend(_split_notification_receivers(item))
            else:
                raw_items.append(str(item))
    else:
        raw_items = [str(value)]
    seen: set[str] = set()
    result: list[str] = []
    for item in raw_items:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def _split_notification_receivers(value: str) -> list[str]:
    normalized = value.replace("，", ",").replace("；", ";")
    for separator in ("\r\n", "\r", "\n", ",", ";"):
        normalized = normalized.replace(separator, "\n")
    return [item.strip() for item in normalized.split("\n")]


class XiaolubanAutomationBindingPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(default=XIAOLUBAN_PLATFORM, pattern=f"^{XIAOLUBAN_PLATFORM}$")
    account_id: RequiredIdentifierStr
    display_name: str = Field(min_length=1)
    derived_uid: RequiredIdentifierStr
    source_label: str = Field(min_length=1)
    updated_at: datetime


__all__ = [
    "DEFAULT_XIAOLUBAN_BASE_URL",
    "XIAOLUBAN_PLATFORM",
    "XiaolubanAccountCreateInput",
    "XiaolubanAccountRecord",
    "XiaolubanAccountStatus",
    "XiaolubanAccountUpdateInput",
    "XiaolubanAutomationBindingPreview",
    "XiaolubanImConfig",
    "XiaolubanImConfigUpdateInput",
    "XiaolubanImForwardingCommandResponse",
    "XiaolubanInboundMessage",
    "XiaolubanKeepAliveRequest",
    "XiaolubanSecretStatus",
    "XiaolubanSendTextRequest",
    "XiaolubanSendTextResponse",
    "XiaolubanTokenRevealResponse",
    "normalize_xiaoluban_notification_receivers",
]
