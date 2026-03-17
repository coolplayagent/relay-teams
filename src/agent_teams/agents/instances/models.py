# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from agent_teams.agents.instances.enums import InstanceStatus
from agent_teams.agents.instances.ids import new_instance_id
from agent_teams.workspace.ids import (
    build_conversation_id,
    build_instance_conversation_id,
)


class SubAgentInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    status: InstanceStatus = InstanceStatus.IDLE
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    last_active_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    completed_tasks: int = 0
    failed_tasks: int = 0

    @model_validator(mode="before")
    @classmethod
    def _populate_workspace_defaults(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        payload = dict(data)
        role_id = payload.get("role_id")
        conversation_id = payload.get("conversation_id")
        if (
            isinstance(payload.get("session_id"), str)
            and str(payload["session_id"])
            and isinstance(role_id, str)
            and role_id
            and not conversation_id
        ):
            payload["conversation_id"] = build_conversation_id(
                str(payload["session_id"]),
                role_id,
            )
        return payload


class AgentRuntimeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    status: InstanceStatus
    runtime_system_prompt: str = ""
    runtime_tools_json: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @model_validator(mode="before")
    @classmethod
    def _populate_workspace_defaults(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        payload = dict(data)
        role_id = payload.get("role_id")
        conversation_id = payload.get("conversation_id")
        if (
            isinstance(payload.get("session_id"), str)
            and str(payload["session_id"])
            and isinstance(role_id, str)
            and role_id
            and not conversation_id
        ):
            payload["conversation_id"] = build_conversation_id(
                str(payload["session_id"]),
                role_id,
            )
        return payload


class RuntimeToolSnapshotEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["local", "skill", "mcp"]
    name: str = Field(min_length=1)
    description: str = ""
    server_name: str = ""
    kind: Literal["function", "output", "external", "unapproved"] = "function"
    strict: bool | None = None
    sequential: bool = False
    parameters_json_schema: dict[str, JsonValue] = Field(default_factory=dict)


class RuntimeToolsSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    local_tools: tuple[RuntimeToolSnapshotEntry, ...] = ()
    skill_tools: tuple[RuntimeToolSnapshotEntry, ...] = ()
    mcp_tools: tuple[RuntimeToolSnapshotEntry, ...] = ()


def create_subagent_instance(
    role_id: str,
    *,
    workspace_id: str,
    session_id: str | None = None,
    conversation_id: str | None = None,
) -> SubAgentInstance:
    instance_id = new_instance_id().value
    resolved_conversation_id = conversation_id
    if session_id is not None:
        if resolved_conversation_id is None:
            resolved_conversation_id = build_instance_conversation_id(
                session_id,
                role_id,
                instance_id,
            )
    if resolved_conversation_id is None:
        raise ValueError("conversation_id is required when session_id is not provided")
    return SubAgentInstance(
        instance_id=instance_id,
        role_id=role_id,
        workspace_id=workspace_id,
        conversation_id=resolved_conversation_id,
    )
