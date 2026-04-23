# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from relay_teams.validation import (
    RequiredIdentifierStr,
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
    secret_status: XiaolubanSecretStatus = Field(default_factory=XiaolubanSecretStatus)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class XiaolubanAccountCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1)
    token: str = Field(min_length=1)
    base_url: str = Field(min_length=1, default=DEFAULT_XIAOLUBAN_BASE_URL)
    enabled: bool = True

    @field_validator("display_name", "token", "base_url")
    @classmethod
    def _normalize_text(cls, value: str, info) -> str:
        normalized = normalize_optional_string(value, field_name=info.field_name)
        if normalized is None:
            raise ValueError(f"{info.field_name} must not be empty")
        return normalized


class XiaolubanAccountUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Optional[str] = None
    token: Optional[str] = None
    base_url: Optional[str] = None
    enabled: Optional[bool] = None

    @field_validator("display_name", "token", "base_url")
    @classmethod
    def _normalize_optional_text(cls, value: Optional[str], info) -> Optional[str]:
        return normalize_optional_string(value, field_name=info.field_name)

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
