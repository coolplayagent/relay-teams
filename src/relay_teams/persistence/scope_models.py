from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ScopeType(str, Enum):
    GLOBAL = "global"
    WORKSPACE = "workspace"
    SESSION = "session"
    ROLE = "role"
    CONVERSATION = "conversation"
    TASK = "task"
    INSTANCE = "instance"


class ScopeRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_type: ScopeType
    scope_id: str = Field(min_length=1)


class StateMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ScopeRef
    key: str = Field(min_length=1)
    value_json: str = Field(min_length=1)
