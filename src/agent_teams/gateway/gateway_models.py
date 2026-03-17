# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class GatewayChannelType(str, Enum):
    ACP_STDIO = "acp_stdio"


class GatewayMcpConnectionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class GatewayMcpServerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    transport: str = Field(min_length=1)
    config: dict[str, JsonValue] = Field(default_factory=dict)


class GatewayMcpConnectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    connection_id: str = Field(min_length=1)
    server_id: str = Field(min_length=1)
    status: GatewayMcpConnectionStatus
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class GatewaySessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gateway_session_id: str = Field(min_length=1)
    channel_type: GatewayChannelType
    external_session_id: str = Field(min_length=1)
    internal_session_id: str = Field(min_length=1)
    active_run_id: str | None = None
    peer_user_id: str | None = None
    peer_chat_id: str | None = None
    cwd: str | None = None
    capabilities: dict[str, JsonValue] = Field(default_factory=dict)
    channel_state: dict[str, JsonValue] = Field(default_factory=dict)
    session_mcp_servers: tuple[GatewayMcpServerSpec, ...] = ()
    mcp_connections: tuple[GatewayMcpConnectionRecord, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
