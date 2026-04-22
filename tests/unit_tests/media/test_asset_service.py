# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from pydantic_ai.messages import AudioUrl, BinaryContent, ImageUrl, VideoUrl

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


def test_load_provider_content_for_local_asset_uses_force_download_local_url(
    tmp_path: Path,
) -> None:
    service = _build_service(tmp_path)
    record = service.store_bytes(
        session_id="session-local-1",
        workspace_id="default",
        modality=MediaModality.IMAGE,
        mime_type="image/png",
        data=b"png-bytes",
        name="diagram.png",
        source="local-test",
    )

    provider_content = service.load_provider_content(
        part=service.to_content_part(record)
    )

    assert provider_content == ImageUrl(
        url=(
            "http://127.0.0.1:8000"
            f"/api/sessions/{record.session_id}/media/{record.asset_id}/file"
        ),
        media_type="image/png",
        force_download="allow-local",
    )


def test_hydrate_user_prompt_content_resolves_absolute_local_asset_urls(
    tmp_path: Path,
) -> None:
    service = _build_service(tmp_path)
    record = service.store_bytes(
        session_id="session-local-2",
        workspace_id="default",
        modality=MediaModality.IMAGE,
        mime_type="image/png",
        data=b"png-bytes",
        name="diagram.png",
        source="local-test",
    )

    hydrated = service.hydrate_user_prompt_content(
        content=(
            ImageUrl(
                url=(
                    "http://127.0.0.1:8000"
                    f"/api/sessions/{record.session_id}/media/{record.asset_id}/file"
                ),
                media_type="image/png",
                force_download="allow-local",
            ),
        )
    )

    assert hydrated == (BinaryContent(data=b"png-bytes", media_type="image/png"),)


def test_hydrate_user_prompt_content_resolves_configured_local_asset_host(
    tmp_path: Path,
) -> None:
    service = _build_service(
        tmp_path,
        local_server_base_url="http://agent-teams.local:9100",
    )
    record = service.store_bytes(
        session_id="session-local-custom-host",
        workspace_id="default",
        modality=MediaModality.IMAGE,
        mime_type="image/png",
        data=b"png-bytes",
        name="diagram.png",
        source="local-test",
    )

    hydrated = service.hydrate_user_prompt_content(
        content=(
            ImageUrl(
                url=service.provider_asset_url(record.session_id, record.asset_id),
                media_type="image/png",
                force_download="allow-local",
            ),
        )
    )

    assert hydrated == (BinaryContent(data=b"png-bytes", media_type="image/png"),)


def test_hydrate_user_prompt_content_resolves_configured_local_asset_base_path(
    tmp_path: Path,
) -> None:
    service = _build_service(
        tmp_path,
        local_server_base_url="https://agent-teams.local/app",
    )
    record = service.store_bytes(
        session_id="session-local-custom-path",
        workspace_id="default",
        modality=MediaModality.IMAGE,
        mime_type="image/png",
        data=b"png-bytes",
        name="diagram.png",
        source="local-test",
    )
    provider_url = service.provider_asset_url(record.session_id, record.asset_id)

    hydrated = service.hydrate_user_prompt_content(
        content=(
            ImageUrl(
                url=provider_url,
                media_type="image/png",
                force_download="allow-local",
            ),
        )
    )

    assert provider_url == (
        "https://agent-teams.local/app"
        f"/api/sessions/{record.session_id}/media/{record.asset_id}/file"
    )
    assert hydrated == (BinaryContent(data=b"png-bytes", media_type="image/png"),)


def test_hydrate_user_prompt_content_ignores_localhost_url_with_wrong_port(
    tmp_path: Path,
) -> None:
    service = _build_service(tmp_path, local_server_base_url="http://127.0.0.1:8000")
    image_url = ImageUrl(
        url=("http://127.0.0.1:9999/api/sessions/session-other/media/asset-other/file"),
        media_type="image/png",
        force_download="allow-local",
    )

    hydrated = service.hydrate_user_prompt_content(content=(image_url,))

    assert hydrated == (image_url,)


def test_hydrate_user_prompt_content_resolves_localhost_alias_with_same_port(
    tmp_path: Path,
) -> None:
    service = _build_service(tmp_path, local_server_base_url="http://127.0.0.1:8000")
    record = service.store_bytes(
        session_id="session-local-alias",
        workspace_id="default",
        modality=MediaModality.IMAGE,
        mime_type="image/png",
        data=b"png-bytes",
        name="diagram.png",
        source="local-test",
    )

    hydrated = service.hydrate_user_prompt_content(
        content=(
            ImageUrl(
                url=(
                    "http://localhost:8000"
                    f"/api/sessions/{record.session_id}/media/{record.asset_id}/file"
                ),
                media_type="image/png",
                force_download="allow-local",
            ),
        )
    )

    assert hydrated == (BinaryContent(data=b"png-bytes", media_type="image/png"),)


def test_load_provider_content_for_local_audio_and_video_assets(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    audio_record = service.store_bytes(
        session_id="session-local-audio",
        workspace_id="default",
        modality=MediaModality.AUDIO,
        mime_type="audio/mpeg",
        data=b"mp3-bytes",
        name="demo.mp3",
        source="local-test",
    )
    video_record = service.store_bytes(
        session_id="session-local-video",
        workspace_id="default",
        modality=MediaModality.VIDEO,
        mime_type="video/mp4",
        data=b"mp4-bytes",
        name="demo.mp4",
        source="local-test",
    )

    assert service.load_provider_content(
        part=service.to_content_part(audio_record)
    ) == AudioUrl(
        url=service.provider_asset_url(audio_record.session_id, audio_record.asset_id),
        media_type="audio/mpeg",
        force_download="allow-local",
    )
    assert service.load_provider_content(
        part=service.to_content_part(video_record)
    ) == VideoUrl(
        url=service.provider_asset_url(video_record.session_id, video_record.asset_id),
        media_type="video/mp4",
        force_download="allow-local",
    )


def _build_service(
    tmp_path: Path,
    *,
    local_server_base_url: str = "http://127.0.0.1:8000",
) -> MediaAssetService:
    return MediaAssetService(
        repository=MediaAssetRepository(tmp_path / "media-assets.db"),
        workspace_manager=WorkspaceManager(project_root=tmp_path),
        local_server_base_url=local_server_base_url,
    )
