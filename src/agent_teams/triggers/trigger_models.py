from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator, JsonValue


class TriggerSourceType(str, Enum):
    SCHEDULE = "schedule"
    WEBHOOK = "webhook"
    IM = "im"
    RSS = "rss"
    CUSTOM = "custom"


class TriggerStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class TriggerAuthMode(str, Enum):
    NONE = "none"
    URL_TOKEN = "url_token"
    HEADER_TOKEN = "header_token"
    HMAC_SHA256 = "hmac_sha256"


class TriggerEventStatus(str, Enum):
    RECEIVED = "received"
    DUPLICATE = "duplicate"
    REJECTED_AUTH = "rejected_auth"


class TriggerAuthPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: TriggerAuthMode
    header_name: str | None = None
    token: str | None = None
    secret: str | None = None
    signature_header: str = "X-Signature"
    timestamp_header: str = "X-Timestamp"
    max_skew_seconds: int = Field(default=300, ge=1, le=3600)

    @model_validator(mode="after")
    def _validate_mode_fields(self) -> TriggerAuthPolicy:
        if self.mode == TriggerAuthMode.HEADER_TOKEN:
            if not self.header_name or not self.token:
                raise ValueError(
                    "header_name and token are required for header_token mode"
                )
        if self.mode == TriggerAuthMode.HMAC_SHA256 and not self.secret:
            raise ValueError("secret is required for hmac_sha256 mode")
        return self


class TriggerCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    display_name: str | None = None
    source_type: TriggerSourceType
    source_config: dict[str, JsonValue] = Field(default_factory=dict)
    auth_policies: tuple[TriggerAuthPolicy, ...] = ()
    target_config: dict[str, JsonValue] | None = None
    public_token: str | None = None
    enabled: bool = True


class TriggerUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    display_name: str | None = None
    source_config: dict[str, JsonValue] | None = None
    auth_policies: tuple[TriggerAuthPolicy, ...] | None = None
    target_config: dict[str, JsonValue] | None = None


class TriggerDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    source_type: TriggerSourceType
    status: TriggerStatus
    public_token: str | None = None
    source_config: dict[str, JsonValue] = Field(default_factory=dict)
    auth_policies: tuple[TriggerAuthPolicy, ...] = ()
    target_config: dict[str, JsonValue] | None = None
    created_at: datetime
    updated_at: datetime


class TriggerIngestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_id: str | None = None
    trigger_name: str | None = None
    source_type: TriggerSourceType
    event_key: str | None = None
    occurred_at: datetime | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_selector(self) -> TriggerIngestInput:
        if not self.trigger_id and not self.trigger_name:
            raise ValueError("trigger_id or trigger_name is required")
        return self


class TriggerEventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence_id: int
    event_id: str = Field(min_length=1)
    trigger_id: str = Field(min_length=1)
    trigger_name: str = Field(min_length=1)
    source_type: TriggerSourceType
    event_key: str | None = None
    status: TriggerEventStatus
    received_at: datetime
    occurred_at: datetime | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    remote_addr: str | None = None
    auth_mode: TriggerAuthMode | None = None
    auth_result: str = Field(min_length=1)
    auth_reason: str | None = None


class TriggerIngestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    event_id: str = Field(min_length=1)
    duplicate: bool = False
    status: TriggerEventStatus
    trigger_id: str = Field(min_length=1)
    trigger_name: str = Field(min_length=1)
