# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from relay_teams.validation import (
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


class XiaolubanAccountRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: RequiredIdentifierStr
    display_name: str = Field(min_length=1)
    base_url: str = Field(min_length=1, default=DEFAULT_XIAOLUBAN_BASE_URL)
    status: XiaolubanAccountStatus = XiaolubanAccountStatus.ENABLED
    derived_uid: RequiredIdentifierStr
    notification_workspace_ids: tuple[RequiredIdentifierStr, ...] = ()
    notification_receiver: Optional[str] = None
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
    def _normalize_receiver(cls, value: Optional[str]) -> Optional[str]:
        return normalize_optional_string(value, field_name="notification_receiver")


class XiaolubanAccountCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1)
    token: str = Field(min_length=1)
    base_url: str = Field(min_length=1, default=DEFAULT_XIAOLUBAN_BASE_URL)
    enabled: bool = True
    notification_workspace_ids: tuple[RequiredIdentifierStr, ...] = ()
    notification_receiver: Optional[str] = None

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
    def _normalize_receiver(cls, value: Optional[str]) -> Optional[str]:
        return normalize_optional_string(value, field_name="notification_receiver")


class XiaolubanAccountUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Optional[str] = None
    token: Optional[str] = None
    base_url: Optional[str] = None
    enabled: Optional[bool] = None
    notification_workspace_ids: Optional[tuple[RequiredIdentifierStr, ...]] = None
    notification_receiver: Optional[str] = None

    @field_validator("display_name", "token", "base_url", "notification_receiver")
    @classmethod
    def _normalize_optional_text(cls, value: Optional[str], info) -> Optional[str]:
        return normalize_optional_string(value, field_name=info.field_name)

    @field_validator("notification_workspace_ids", mode="before")
    @classmethod
    def _normalize_workspace_ids(cls, value: object) -> Optional[tuple[str, ...]]:
        return normalize_identifier_tuple(
            value,
            field_name="notification_workspace_ids",
        )

    @model_validator(mode="after")
    def _validate_patch(self) -> XiaolubanAccountUpdateInput:
        require_non_empty_patch(self)
        return self


class XiaolubanSendTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)
    receiver: str = Field(min_length=1)
    auth: str = Field(min_length=1)
    sender: Optional[str] = None


class XiaolubanSendTextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1)
    raw_response: Optional[str] = None


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
    "XiaolubanSecretStatus",
    "XiaolubanSendTextRequest",
    "XiaolubanSendTextResponse",
]
