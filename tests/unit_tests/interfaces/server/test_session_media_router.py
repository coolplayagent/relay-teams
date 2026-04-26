# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.interfaces.server.deps import (
    get_media_asset_service,
    get_session_service,
)
from relay_teams.interfaces.server.routers import session_media
from relay_teams.media import (
    MediaAssetRecord,
    MediaAssetStorageKind,
    MediaModality,
    MediaRefContentPart,
)


class _FakeSessionRecord:
    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id


class _FakeSessionService:
    def get_session(self, session_id: str) -> _FakeSessionRecord:
        if session_id != "session-1":
            raise KeyError(session_id)
        return _FakeSessionRecord(workspace_id="workspace-1")


class _FakeMediaAssetService:
    def __init__(self, asset_file_path: Path) -> None:
        self.asset_file_path = asset_file_path
        self.asset_record = MediaAssetRecord(
            asset_id="asset-1",
            session_id="session-1",
            workspace_id="workspace-1",
            storage_kind=MediaAssetStorageKind.LOCAL,
            modality=MediaModality.IMAGE,
            mime_type="image/png",
            name="image.png",
            relative_path="media/image.png",
            size_bytes=4,
            source="session_upload",
        )

    def list_session_assets(self, session_id: str) -> tuple[MediaAssetRecord, ...]:
        if session_id != "session-1":
            raise KeyError(session_id)
        return (self.asset_record,)

    def store_bytes(
        self,
        *,
        session_id: str,
        workspace_id: str,
        modality: MediaModality,
        mime_type: str,
        data: bytes,
        name: str,
        size_bytes: int,
        source: str,
    ) -> MediaAssetRecord:
        self.asset_record = self.asset_record.model_copy(
            update={
                "session_id": session_id,
                "workspace_id": workspace_id,
                "modality": modality,
                "mime_type": mime_type,
                "name": name,
                "size_bytes": size_bytes,
                "source": source,
            }
        )
        self.asset_file_path.write_bytes(data)
        return self.asset_record

    def get_asset(self, asset_id: str) -> MediaAssetRecord:
        if asset_id != self.asset_record.asset_id:
            raise KeyError(asset_id)
        return self.asset_record

    def get_asset_file(self, *, session_id: str, asset_id: str) -> tuple[Path, str]:
        if (
            session_id != self.asset_record.session_id
            or asset_id != self.asset_record.asset_id
        ):
            raise FileNotFoundError(asset_id)
        return self.asset_file_path, self.asset_record.mime_type

    def to_content_part(self, record: MediaAssetRecord) -> MediaRefContentPart:
        return MediaRefContentPart(
            asset_id=record.asset_id,
            session_id=record.session_id,
            modality=record.modality,
            mime_type=record.mime_type,
            name=record.name,
            url=f"/api/sessions/{record.session_id}/media/{record.asset_id}/file",
            size_bytes=record.size_bytes,
        )


def _create_client(asset_file_path: Path) -> tuple[TestClient, _FakeMediaAssetService]:
    app = FastAPI()
    app.include_router(session_media.router, prefix="/api")
    media_service = _FakeMediaAssetService(asset_file_path)
    app.dependency_overrides[get_session_service] = _FakeSessionService
    app.dependency_overrides[get_media_asset_service] = lambda: media_service
    return TestClient(app), media_service


def test_session_media_routes_offload_sync_service_calls(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def fake_to_thread(
        func: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        calls.append((func.__name__, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(session_media, "call_maybe_async", fake_to_thread)
    asset_file_path = tmp_path / "image.png"
    asset_file_path.write_bytes(b"seed")
    client, _ = _create_client(asset_file_path)

    listed = client.get("/api/sessions/session-1/media")
    uploaded = client.post(
        "/api/sessions/session-1/media",
        files={"file": ("image.png", b"data", "image/png")},
    )
    fetched = client.get("/api/sessions/session-1/media/asset-1")
    file_response = client.get("/api/sessions/session-1/media/asset-1/file")

    assert listed.status_code == 200
    assert uploaded.status_code == 200
    assert fetched.status_code == 200
    assert file_response.status_code == 200
    assert file_response.content == b"data"
    assert [call[0] for call in calls] == [
        "get_session",
        "list_session_assets",
        "get_session",
        "store_bytes",
        "get_session",
        "get_asset",
        "get_session",
        "get_asset",
        "get_asset_file",
    ]
