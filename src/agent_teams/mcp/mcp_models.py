# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, JsonValue


class McpConfigScope(str, Enum):
    APP = "app"


class McpToolInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str = ""


class McpServerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    config: dict[str, JsonValue]
    server_config: dict[str, JsonValue]
    source: McpConfigScope


class McpServerSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    source: McpConfigScope
    transport: str


class McpServerToolsSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server: str
    source: McpConfigScope
    transport: str
    tools: tuple[McpToolInfo, ...] = ()
