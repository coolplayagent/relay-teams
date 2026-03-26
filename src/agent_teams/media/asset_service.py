from __future__ import annotations

import base64
import binascii
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    ImageUrl,
    UserContent,
    VideoUrl,
)

from agent_teams.media.asset_repository import MediaAssetRepository
from agent_teams.media.models import (
    ContentPart,
    InlineMediaContentPart,
    MediaAssetRecord,
    MediaAssetStorageKind,
    MediaModality,
    MediaRefContentPart,
    TextContentPart,
)
from agent_teams.workspace import WorkspaceManager


class MediaAssetService:
    def __init__(
        self,
        *,
        repository: MediaAssetRepository,
        workspace_manager: WorkspaceManager,
    ) -> None:
        self._repository = repository
        self._workspace_manager = workspace_manager

    def normalize_content_parts(
        self,
        *,
        session_id: str,
        workspace_id: str,
        parts: tuple[ContentPart, ...],
    ) -> tuple[ContentPart, ...]:
        normalized: list[ContentPart] = []
        for part in parts:
            if isinstance(part, InlineMediaContentPart):
                record = self.store_inline_media(
                    session_id=session_id,
                    workspace_id=workspace_id,
                    part=part,
                )
                normalized.append(self.to_content_part(record))
                continue
            normalized.append(part)
        return tuple(normalized)

    def store_inline_media(
        self,
        *,
        session_id: str,
        workspace_id: str,
        part: InlineMediaContentPart,
        source: str = "inline_input",
    ) -> MediaAssetRecord:
        compact = "".join(part.base64_data.split())
        try:
            payload = base64.b64decode(compact, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid base64 media payload") from exc
        return self.store_bytes(
            session_id=session_id,
            workspace_id=workspace_id,
            modality=part.modality,
            mime_type=part.mime_type,
            data=payload,
            name=part.name,
            size_bytes=part.size_bytes,
            width=part.width,
            height=part.height,
            duration_ms=part.duration_ms,
            thumbnail_asset_id=part.thumbnail_asset_id,
            source=source,
        )

    def store_bytes(
        self,
        *,
        session_id: str,
        workspace_id: str,
        modality: MediaModality,
        mime_type: str,
        data: bytes,
        name: str = "",
        size_bytes: int | None = None,
        width: int | None = None,
        height: int | None = None,
        duration_ms: int | None = None,
        thumbnail_asset_id: str | None = None,
        source: str = "generated",
    ) -> MediaAssetRecord:
        asset_id = f"asset-{uuid4().hex[:12]}"
        storage_dir = self._storage_dir(
            workspace_id=workspace_id,
            session_id=session_id,
        )
        storage_dir.mkdir(parents=True, exist_ok=True)
        suffix = _suffix_for_media(name=name, mime_type=mime_type)
        file_name = f"{asset_id}{suffix}"
        file_path = storage_dir / file_name
        file_path.write_bytes(data)
        now = datetime.now(tz=timezone.utc)
        return self._repository.upsert(
            MediaAssetRecord(
                asset_id=asset_id,
                session_id=session_id,
                workspace_id=workspace_id,
                storage_kind=MediaAssetStorageKind.LOCAL,
                modality=modality,
                mime_type=mime_type,
                name=name.strip(),
                relative_path=file_name,
                size_bytes=size_bytes if size_bytes is not None else len(data),
                width=width,
                height=height,
                duration_ms=duration_ms,
                thumbnail_asset_id=thumbnail_asset_id,
                source=source,
                created_at=now,
                updated_at=now,
            )
        )

    def store_remote_reference(
        self,
        *,
        session_id: str,
        workspace_id: str,
        modality: MediaModality,
        mime_type: str,
        url: str,
        name: str = "",
        size_bytes: int | None = None,
        width: int | None = None,
        height: int | None = None,
        duration_ms: int | None = None,
        thumbnail_asset_id: str | None = None,
        source: str = "remote_reference",
    ) -> MediaAssetRecord:
        asset_id = f"asset-{uuid4().hex[:12]}"
        now = datetime.now(tz=timezone.utc)
        return self._repository.upsert(
            MediaAssetRecord(
                asset_id=asset_id,
                session_id=session_id,
                workspace_id=workspace_id,
                storage_kind=MediaAssetStorageKind.REMOTE,
                modality=modality,
                mime_type=mime_type,
                name=name.strip(),
                external_url=url.strip(),
                size_bytes=size_bytes,
                width=width,
                height=height,
                duration_ms=duration_ms,
                thumbnail_asset_id=thumbnail_asset_id,
                source=source,
                created_at=now,
                updated_at=now,
            )
        )

    def get_asset(self, asset_id: str) -> MediaAssetRecord:
        return self._repository.get(asset_id)

    def list_session_assets(self, session_id: str) -> tuple[MediaAssetRecord, ...]:
        return self._repository.list_by_session(session_id)

    def delete_session_assets(self, session_id: str) -> None:
        self._repository.delete_by_session(session_id)

    def get_asset_file(self, *, session_id: str, asset_id: str) -> tuple[Path, str]:
        record = self._repository.get(asset_id)
        if record.session_id != session_id:
            raise KeyError(f"Asset {asset_id} does not belong to session {session_id}")
        if (
            record.storage_kind != MediaAssetStorageKind.LOCAL
            or record.relative_path is None
        ):
            raise FileNotFoundError(f"Asset file is not available locally: {asset_id}")
        file_path = (
            self._storage_dir(
                workspace_id=record.workspace_id,
                session_id=record.session_id,
            )
            / record.relative_path
        )
        if not file_path.exists() or not file_path.is_file():
            raise FileNotFoundError(f"Asset file not found: {asset_id}")
        return file_path, record.mime_type

    def to_content_part(self, record: MediaAssetRecord) -> MediaRefContentPart:
        return MediaRefContentPart(
            asset_id=record.asset_id,
            session_id=record.session_id,
            modality=record.modality,
            mime_type=record.mime_type,
            name=record.name,
            url=self.asset_url(record.session_id, record.asset_id, record.external_url),
            size_bytes=record.size_bytes,
            width=record.width,
            height=record.height,
            duration_ms=record.duration_ms,
            thumbnail_asset_id=record.thumbnail_asset_id,
        )

    def asset_url(
        self,
        session_id: str,
        asset_id: str,
        external_url: str | None = None,
    ) -> str:
        if external_url is not None and external_url.strip():
            return external_url.strip()
        return f"/api/sessions/{session_id}/media/{asset_id}/file"

    def load_provider_content(
        self,
        *,
        part: MediaRefContentPart,
    ) -> ImageUrl | AudioUrl | VideoUrl | BinaryContent:
        record = self._repository.get(part.asset_id)
        if record.storage_kind == MediaAssetStorageKind.REMOTE:
            url = self.asset_url(
                record.session_id, record.asset_id, record.external_url
            )
            if record.modality == MediaModality.IMAGE:
                return ImageUrl(url=url, media_type=record.mime_type)
            if record.modality == MediaModality.AUDIO:
                return AudioUrl(url=url, media_type=record.mime_type)
            return VideoUrl(url=url, media_type=record.mime_type)
        file_path, _ = self.get_asset_file(
            session_id=record.session_id,
            asset_id=record.asset_id,
        )
        return BinaryContent(
            data=file_path.read_bytes(),
            media_type=record.mime_type,
        )

    def to_provider_user_prompt_content(
        self,
        *,
        parts: tuple[ContentPart, ...],
    ) -> str | tuple[UserContent, ...]:
        if not parts:
            return ""
        if all(isinstance(part, TextContentPart) for part in parts):
            return "\n\n".join(
                part.text for part in parts if isinstance(part, TextContentPart)
            ).strip()
        content: list[UserContent] = []
        for part in parts:
            if isinstance(part, TextContentPart):
                content.append(part.text)
                continue
            if isinstance(part, InlineMediaContentPart):
                raise ValueError(
                    "Inline media must be normalized before provider execution"
                )
            content.append(self.load_provider_content(part=part))
        return tuple(content)

    def _storage_dir(self, *, workspace_id: str, session_id: str) -> Path:
        return (
            self._workspace_manager.session_artifact_dir(
                workspace_id=workspace_id,
                session_id=session_id,
            )
            / "media"
        )


def infer_media_modality(content_type: str, filename: str = "") -> MediaModality:
    normalized = str(content_type or "").strip().lower()
    if normalized.startswith("image/"):
        return MediaModality.IMAGE
    if normalized.startswith("audio/"):
        return MediaModality.AUDIO
    if normalized.startswith("video/"):
        return MediaModality.VIDEO
    guessed_type, _ = mimetypes.guess_type(filename)
    if isinstance(guessed_type, str):
        return infer_media_modality(guessed_type)
    raise ValueError(f"Unsupported media type: {content_type or filename}")


def _suffix_for_media(*, name: str, mime_type: str) -> str:
    candidate = Path(name).suffix.strip()
    if candidate:
        return candidate if candidate.startswith(".") else f".{candidate}"
    guessed = mimetypes.guess_extension(mime_type, strict=False)
    if guessed is not None:
        return guessed
    return ""
