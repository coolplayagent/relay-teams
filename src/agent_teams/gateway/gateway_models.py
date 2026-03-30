# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from agent_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr


class GatewayChannelType(str, Enum):
    ACP_STDIO = "acp_stdio"
    WECHAT = "wechat"


class GatewayMcpConnectionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class GatewayMcpServerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    transport: str = Field(min_length=1)
    config: dict[str, JsonValue] = Field(default_factory=dict)


class GatewayMcpConnectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    connection_id: RequiredIdentifierStr
    server_id: RequiredIdentifierStr
    status: GatewayMcpConnectionStatus
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class GatewaySessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gateway_session_id: RequiredIdentifierStr
    channel_type: GatewayChannelType
    external_session_id: RequiredIdentifierStr
    internal_session_id: RequiredIdentifierStr
    active_run_id: OptionalIdentifierStr = None
    peer_user_id: OptionalIdentifierStr = None
    peer_chat_id: OptionalIdentifierStr = None
    cwd: str | None = None
    capabilities: dict[str, JsonValue] = Field(default_factory=dict)
    channel_state: dict[str, JsonValue] = Field(default_factory=dict)
    session_mcp_servers: tuple[GatewayMcpServerSpec, ...] = ()
    mcp_connections: tuple[GatewayMcpConnectionRecord, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
