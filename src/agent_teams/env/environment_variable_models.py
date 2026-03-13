# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class EnvironmentVariableScope(str, Enum):
    SYSTEM = "system"
    USER = "user"


class EnvironmentVariableValueKind(str, Enum):
    STRING = "string"
    EXPANDABLE = "expandable"


class EnvironmentVariableRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    value: str
    scope: EnvironmentVariableScope
    value_kind: EnvironmentVariableValueKind = EnvironmentVariableValueKind.STRING


class EnvironmentVariableCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system: tuple[EnvironmentVariableRecord, ...] = ()
    user: tuple[EnvironmentVariableRecord, ...] = ()


class EnvironmentVariableSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str | None = None
    value: str
