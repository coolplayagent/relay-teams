# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from pydantic_ai.messages import ImageUrl

from relay_teams.media import (
    MediaAssetRecord,
    MediaAssetRepository,
    MediaAssetService,
    MediaAssetStorageKind,
    MediaModality,
    TextContentPart,
)
from relay_teams.workspace import WorkspaceManager


def test_to_persisted_user_prompt_content_preserves_remote_asset_urls(
    tmp_path: Path,
) -> None:
    service = _build_service(tmp_path)
    record = MediaAssetRecord(
        asset_id="asset-remote-1",
        session_id="session-remote-1",
        workspace_id="default",
        storage_kind=MediaAssetStorageKind.REMOTE,
        modality=MediaModality.IMAGE,
        mime_type="image/png",
        name="diagram.png",
        external_url="https://cdn.example.com/assets/diagram.png",
        source="remote-test",
    )
    service._repository.upsert(record)
    content_part = service.to_content_part(record)

    persisted = service.to_persisted_user_prompt_content(
        parts=(
            TextContentPart(text="describe this"),
            content_part,
        )
    )

    assert persisted == (
        "describe this",
        ImageUrl(
            url="https://cdn.example.com/assets/diagram.png",
            media_type="image/png",
        ),
    )


def test_hydrate_user_prompt_content_keeps_remote_asset_urls_as_urls(
    tmp_path: Path,
) -> None:
    service = _build_service(tmp_path)
    record = MediaAssetRecord(
        asset_id="asset-remote-2",
        session_id="session-remote-2",
        workspace_id="default",
        storage_kind=MediaAssetStorageKind.REMOTE,
        modality=MediaModality.IMAGE,
        mime_type="image/png",
        name="preview.png",
        external_url="https://cdn.example.com/assets/preview.png",
        source="remote-test",
    )
    service._repository.upsert(record)
    persisted = service.to_persisted_user_prompt_content(
        parts=(service.to_content_part(record),)
    )

    hydrated = service.hydrate_user_prompt_content(content=persisted)

    assert hydrated == (
        ImageUrl(
            url="https://cdn.example.com/assets/preview.png",
            media_type="image/png",
        ),
    )


def _build_service(tmp_path: Path) -> MediaAssetService:
    return MediaAssetService(
        repository=MediaAssetRepository(tmp_path / "media-assets.db"),
        workspace_manager=WorkspaceManager(project_root=tmp_path),
    )
