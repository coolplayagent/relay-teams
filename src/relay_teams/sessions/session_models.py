# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from relay_teams.interfaces.server.api_write_validation import (
    normalize_optional_text_field,
    require_non_empty_patch,
)
from relay_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr


class SessionMode(str, Enum):
    NORMAL = "normal"
    ORCHESTRATION = "orchestration"


class ProjectKind(str, Enum):
    WORKSPACE = "workspace"
    AUTOMATION = "automation"


_RESERVED_SESSION_METADATA_KEYS = {
    "title",
    "title_source",
    "source_label",
    "source_icon",
    "source_kind",
    "source_provider",
}

_SESSION_CREATE_METADATA_FIELDS = {
    "title",
    "title_source",
    "source_label",
    "source_icon",
    "custom_metadata",
}

_SESSION_PATCH_METADATA_FIELDS = {
    "title",
    "title_source",
    "source_label",
    "source_icon",
    "custom_metadata",
}


def _validate_session_custom_metadata(
    value: dict[str, str] | None,
) -> dict[str, str] | None:
    if value is None:
        return None
    normalized: dict[str, str] = {}
    for key, raw_value in value.items():
        normalized_key = str(key).strip()
        if not normalized_key:
            raise ValueError("custom_metadata keys must not be blank")
        if (
            normalized_key in _RESERVED_SESSION_METADATA_KEYS
            or normalized_key.startswith("feishu_")
        ):
            raise ValueError(f"custom_metadata key is reserved: {normalized_key}")
        normalized_value = str(raw_value).strip()
        if not normalized_value:
            raise ValueError(
                f"custom_metadata value must not be blank: {normalized_key}"
            )
        normalized[normalized_key] = normalized_value
    return normalized


def normalize_session_create_metadata_input(value: object) -> object:
    if not isinstance(value, dict):
        return value
    payload = {str(key): item for key, item in value.items()}
    if all(key in _SESSION_CREATE_METADATA_FIELDS for key in payload):
        return payload
    normalized: dict[str, object] = {}
    legacy_custom_metadata: dict[str, object] = {}
    for key, item in payload.items():
        if key in _SESSION_CREATE_METADATA_FIELDS:
            normalized[key] = item
            continue
        if key in _RESERVED_SESSION_METADATA_KEYS or key.startswith("feishu_"):
            continue
        legacy_custom_metadata[key] = item
    if not legacy_custom_metadata:
        return normalized
    existing_custom_metadata = normalized.get("custom_metadata")
    if existing_custom_metadata is None:
        normalized["custom_metadata"] = legacy_custom_metadata
        return normalized
    if not isinstance(existing_custom_metadata, dict):
        return value
    merged_custom_metadata = {
        str(key): item for key, item in existing_custom_metadata.items()
    }
    merged_custom_metadata.update(legacy_custom_metadata)
    normalized["custom_metadata"] = merged_custom_metadata
    return normalized


def normalize_session_metadata_patch_input(value: object) -> object:
    if not isinstance(value, dict):
        return value
    payload = {str(key): item for key, item in value.items()}
    if set(payload) == {"metadata"} and isinstance(payload.get("metadata"), dict):
        payload = {str(key): item for key, item in payload["metadata"].items()}
    if all(key in _SESSION_PATCH_METADATA_FIELDS for key in payload):
        return payload
    normalized: dict[str, object] = {}
    legacy_custom_metadata: dict[str, object] = {}
    for key, item in payload.items():
        if key in _SESSION_PATCH_METADATA_FIELDS:
            normalized[key] = item
            continue
        if key in _RESERVED_SESSION_METADATA_KEYS or key.startswith("feishu_"):
            continue
        legacy_custom_metadata[key] = item
    if not legacy_custom_metadata:
        return normalized
    existing_custom_metadata = normalized.get("custom_metadata")
    if existing_custom_metadata is None:
        normalized["custom_metadata"] = legacy_custom_metadata
        return normalized
    if not isinstance(existing_custom_metadata, dict):
        return value
    merged_custom_metadata = {
        str(key): item for key, item in existing_custom_metadata.items()
    }
    merged_custom_metadata.update(legacy_custom_metadata)
    normalized["custom_metadata"] = merged_custom_metadata
    return normalized


class SessionCreateMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    title_source: str | None = None
    source_label: str | None = None
    source_icon: str | None = None
    custom_metadata: dict[str, str] | None = None

    @field_validator(
        "title", "title_source", "source_label", "source_icon", mode="before"
    )
    @classmethod
    def _normalize_optional_text(cls, value: object) -> object:
        return normalize_optional_text_field(
            value,
            field_name="session metadata field",
            empty_to_none=True,
        )

    @field_validator("custom_metadata")
    @classmethod
    def _validate_custom_metadata(
        cls, value: dict[str, str] | None
    ) -> dict[str, str] | None:
        return _validate_session_custom_metadata(value)

    @model_validator(mode="after")
    def _validate_title_source(self) -> "SessionCreateMetadata":
        if self.title_source is not None and not str(self.title or "").strip():
            raise ValueError("title_source requires title to be set")
        return self

    def to_metadata_dict(self) -> dict[str, str]:
        metadata = {} if self.custom_metadata is None else dict(self.custom_metadata)
        if self.title is not None:
            metadata["title"] = self.title
            metadata["title_source"] = (
                self.title_source if self.title_source is not None else "manual"
            )
        elif self.title_source is not None:
            metadata["title_source"] = self.title_source
        if self.source_label is not None:
            metadata["source_label"] = self.source_label
        if self.source_icon is not None:
            metadata["source_icon"] = self.source_icon
        return metadata


class SessionMetadataPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    title_source: str | None = None
    source_label: str | None = None
    source_icon: str | None = None
    custom_metadata: dict[str, str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_payload(cls, value: object) -> object:
        return normalize_session_metadata_patch_input(value)

    @field_validator(
        "title", "title_source", "source_label", "source_icon", mode="before"
    )
    @classmethod
    def _normalize_optional_text(cls, value: object) -> object:
        return normalize_optional_text_field(
            value,
            field_name="session metadata field",
            empty_to_none=True,
        )

    @field_validator("custom_metadata")
    @classmethod
    def _validate_custom_metadata(
        cls, value: dict[str, str] | None
    ) -> dict[str, str] | None:
        return _validate_session_custom_metadata(value)

    @model_validator(mode="after")
    def _require_non_empty_patch(self) -> "SessionMetadataPatch":
        return require_non_empty_patch(
            self,
            message="session update must include at least one field",
        )


class SessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
    project_kind: ProjectKind = ProjectKind.WORKSPACE
    project_id: OptionalIdentifierStr = None
    metadata: dict[str, str] = Field(default_factory=dict)
    session_mode: SessionMode = SessionMode.NORMAL
    normal_root_role_id: OptionalIdentifierStr = None
    orchestration_preset_id: OptionalIdentifierStr = None
    started_at: datetime | None = None
    can_switch_mode: bool = True
    has_active_run: bool = False
    active_run_id: OptionalIdentifierStr = None
    active_run_status: str | None = None
    active_run_phase: str | None = None
    pending_tool_approval_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @model_validator(mode="after")
    def _default_project_id(self) -> SessionRecord:
        if self.project_id is None or not self.project_id.strip():
            self.project_id = self.workspace_id
        return self
