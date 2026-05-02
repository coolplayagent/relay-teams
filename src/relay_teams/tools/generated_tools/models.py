# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from relay_teams.validation import RequiredIdentifierStr


class GeneratedToolStatus(StrEnum):
    PENDING = "pending"
    ENABLED = "enabled"
    DISABLED = "disabled"


class GeneratedToolTestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: dict[str, JsonValue] = Field(default_factory=dict)
    expected: JsonValue | None = None
    has_expected: bool = False

    @field_validator("input", mode="before")
    @classmethod
    def _coerce_input(cls, value: object) -> object:
        if value is None:
            return {}
        return value

    @model_validator(mode="before")
    @classmethod
    def _infer_expected_presence(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        if "has_expected" in value:
            return value
        updated = dict(value)
        updated["has_expected"] = "expected" in value
        return updated


class GeneratedToolRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: RequiredIdentifierStr
    description: str = Field(min_length=1)
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)
    test_cases: tuple[GeneratedToolTestCase, ...] = ()
    code_hash: str = Field(min_length=1)
    status: GeneratedToolStatus = GeneratedToolStatus.PENDING
    target_role_id: RequiredIdentifierStr
    created_by_role_id: RequiredIdentifierStr
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class GeneratedToolDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    notes: str = ""


class GeneratedToolSynthesisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: RequiredIdentifierStr
    code_hash: str = Field(min_length=1)
    status: GeneratedToolStatus
    test_count: int = Field(ge=0)
    notes: str = ""
    retry_count: int = Field(default=0, ge=0)
    retry_messages: tuple[str, ...] = ()


class GeneratedToolEnableResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: RequiredIdentifierStr
    code_hash: str = Field(min_length=1)
    target_role_id: RequiredIdentifierStr
    status: GeneratedToolStatus
    role_updated: bool


class GeneratedToolDisableResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: RequiredIdentifierStr
    code_hash: str = Field(min_length=1)
    target_role_id: RequiredIdentifierStr
    status: GeneratedToolStatus
    role_updated: bool


class GeneratedToolUpgradeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: RequiredIdentifierStr
    code_hash: str = Field(min_length=1)
    target_role_id: RequiredIdentifierStr
    status: GeneratedToolStatus
    previous_version: int = Field(ge=1)
    new_version: int = Field(ge=1)
    test_count: int = Field(ge=0)
