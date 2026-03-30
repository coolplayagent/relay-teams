# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from agent_teams.validation import RequiredIdentifierStr


class ExternalAgentTransportType(str, Enum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"
    CUSTOM = "custom"


class ExternalAgentSecretBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    value: str | None = None
    secret: bool = False
    configured: bool = False


class StdioTransportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transport: Literal[ExternalAgentTransportType.STDIO] = (
        ExternalAgentTransportType.STDIO
    )
    command: str = Field(min_length=1)
    args: tuple[str, ...] = ()
    env: tuple[ExternalAgentSecretBinding, ...] = ()


class StreamableHttpTransportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transport: Literal[ExternalAgentTransportType.STREAMABLE_HTTP] = (
        ExternalAgentTransportType.STREAMABLE_HTTP
    )
    url: str = Field(min_length=1)
    headers: tuple[ExternalAgentSecretBinding, ...] = ()
    ssl_verify: bool | None = None


class CustomTransportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transport: Literal[ExternalAgentTransportType.CUSTOM] = (
        ExternalAgentTransportType.CUSTOM
    )
    adapter_id: RequiredIdentifierStr
    config: dict[str, JsonValue] = Field(default_factory=dict)


ExternalAgentTransportConfig = (
    StdioTransportConfig | StreamableHttpTransportConfig | CustomTransportConfig
)


class ExternalAgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    description: str = ""
    transport: ExternalAgentTransportConfig = Field(discriminator="transport")


class ExternalAgentCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agents: tuple[ExternalAgentConfig, ...] = ()


class ExternalAgentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    description: str = ""
    transport: ExternalAgentTransportType


class ExternalAgentOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    transport: ExternalAgentTransportType


class ExternalAgentTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    message: str = ""
    protocol_version: int | None = None
    agent_name: str | None = None
    agent_version: str | None = None


class ExternalAgentSessionStatus(str, Enum):
    READY = "ready"
    FAILED = "failed"


class ExternalAgentSessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: RequiredIdentifierStr
    role_id: RequiredIdentifierStr
    agent_id: RequiredIdentifierStr
    transport: ExternalAgentTransportType
    external_session_id: RequiredIdentifierStr
    status: ExternalAgentSessionStatus = ExternalAgentSessionStatus.READY
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
