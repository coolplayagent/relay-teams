# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.validation import RequiredIdentifierStr


class ConnectorProvider(str, Enum):
    GITHUB = "github"
    DISCORD = "discord"
    FEISHU = "feishu"
    WECHAT = "wechat"
    XIAOLUBAN = "xiaoluban"
    W3 = "w3"
    RELAY_KNOWLEDGE = "relay-knowledge"


class ConnectorCategory(str, Enum):
    AUTH = "auth"
    DEVELOPMENT = "development"
    IM = "im"
    MODELS = "models"


class ConnectorStatus(str, Enum):
    NEEDS_CONFIG = "needs_config"
    CONNECTED = "connected"
    DISABLED = "disabled"
    ERROR = "error"


class ConnectorAuthType(str, Enum):
    OAUTH = "oauth"
    API_KEY = "api_key"
    API_TOKEN = "api_token"
    WEBHOOK = "webhook"
    QR_LOGIN = "qr_login"
    USERNAME_PASSWORD = "username_password"
    CLI = "cli"


class ConnectorSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connected: int = Field(ge=0)
    needs_config: int = Field(ge=0)
    disabled: int = Field(ge=0)
    error: int = Field(ge=0)
    total: int = Field(ge=0)


class ConnectorItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector_id: RequiredIdentifierStr
    provider: ConnectorProvider
    category: ConnectorCategory
    display_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    status: ConnectorStatus
    auth_type: ConnectorAuthType
    account_count: int = Field(ge=0)
    enabled_count: int = Field(ge=0)
    last_activity_at: datetime | None = None
    last_error: str | None = None
    capabilities: tuple[str, ...] = ()


class ConnectorListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: ConnectorSummary
    items: tuple[ConnectorItem, ...]


class ConnectorHealthCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    ok: bool
    message: str = Field(min_length=1)


class ConnectorTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector_id: RequiredIdentifierStr
    provider: ConnectorProvider
    status: ConnectorStatus
    ok: bool
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message: str = Field(min_length=1)
    account_count: int = Field(ge=0)
    enabled_count: int = Field(ge=0)
    runtime_running: bool | None = None
    login_active: bool | None = None
    last_error: str | None = None
    capabilities: tuple[str, ...] = ()
    checks: tuple[ConnectorHealthCheck, ...] = ()
