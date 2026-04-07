from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class MediaModality(str, Enum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class TextContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["text"] = "text"
    text: str = Field(min_length=1)


class MediaRefContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["media_ref"] = "media_ref"
    asset_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    modality: MediaModality
    mime_type: str = Field(min_length=1)
    name: str = ""
    url: str = Field(min_length=1)
    size_bytes: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    thumbnail_asset_id: str | None = None


class InlineMediaContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["inline_media"] = "inline_media"
    modality: MediaModality
    mime_type: str = Field(min_length=1)
    base64_data: str = Field(min_length=1)
    name: str = ""
    size_bytes: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    thumbnail_asset_id: str | None = None


ContentPart = Annotated[
    TextContentPart | MediaRefContentPart | InlineMediaContentPart,
    Field(discriminator="kind"),
]
ContentPartAdapter = TypeAdapter(ContentPart)
ContentPartsAdapter = TypeAdapter(tuple[ContentPart, ...])


class MediaAssetStorageKind(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"


class MediaAssetRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    storage_kind: MediaAssetStorageKind
    modality: MediaModality
    mime_type: str = Field(min_length=1)
    name: str = ""
    relative_path: str | None = None
    external_url: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    thumbnail_asset_id: str | None = None
    source: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


def text_part(text: str) -> TextContentPart | None:
    normalized = str(text or "").strip()
    if not normalized:
        return None
    return TextContentPart(text=normalized)


def content_parts_from_text(text: str) -> tuple[ContentPart, ...]:
    part = text_part(text)
    if part is None:
        return ()
    return (part,)


def content_parts_to_text(parts: tuple[ContentPart, ...]) -> str:
    fragments: list[str] = []
    for part in parts:
        if isinstance(part, TextContentPart):
            fragments.append(part.text)
            continue
        label = part.name.strip() if part.name.strip() else part.modality.value
        fragments.append(f"[{part.modality.value}: {label}]")
    return "\n\n".join(fragment for fragment in fragments if fragment.strip()).strip()
