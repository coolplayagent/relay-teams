# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class McpConfigScope(str, Enum):
    APP = "app"
    PLUGIN = "plugin"
    SESSION = "session"


class McpDiscoveryStatus(str, Enum):
    DISABLED = "disabled"
    PENDING = "pending"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


class McpToolInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str = ""


class McpToolSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str = ""
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)


class McpServerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    config: dict[str, JsonValue]
    server_config: dict[str, JsonValue]
    source: McpConfigScope
    enabled: bool = True


class McpServerSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    source: McpConfigScope
    transport: str
    enabled: bool = True
    discovery_status: McpDiscoveryStatus = McpDiscoveryStatus.PENDING
    tool_count: int = 0
    last_checked_at: datetime | None = None
    error: str | None = None


class McpServerToolsSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server: str
    source: McpConfigScope
    transport: str
    enabled: bool = True
    tools: tuple[McpToolInfo, ...] = ()
    status: McpDiscoveryStatus = McpDiscoveryStatus.PENDING
    last_checked_at: datetime | None = None
    error: str | None = None


class McpServerAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    config: dict[str, JsonValue]
    overwrite: bool = False


class McpServerAddResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server: McpServerSummary
    config_path: str


class McpServerConfigResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server: McpServerSummary
    config: dict[str, JsonValue]


class McpServerUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config: dict[str, JsonValue]


class McpServerConnectionTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server: str
    source: McpConfigScope
    transport: str
    enabled: bool = True
    ok: bool
    tool_count: int = 0
    tools: tuple[McpToolInfo, ...] = ()
    error: str | None = None


class McpServerEnabledUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
