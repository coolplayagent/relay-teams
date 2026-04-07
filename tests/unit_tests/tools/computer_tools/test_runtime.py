# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from pydantic import JsonValue

from relay_teams.computer import (
    ComputerActionResult,
    ComputerActionTarget,
    ComputerObservation,
    describe_builtin_tool,
)
from relay_teams.media import MediaAssetRecord, MediaModality, MediaRefContentPart
from relay_teams.media.models import MediaAssetStorageKind
from relay_teams.tools.runtime import ToolContext
from relay_teams.tools.computer_tools.runtime import _approval_request, _project_result


class _FakeMediaAssetService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

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
        self.calls.append(
            {
                "session_id": session_id,
                "workspace_id": workspace_id,
                "modality": modality.value,
                "mime_type": mime_type,
                "data": data,
                "name": name,
                "width": width,
                "height": height,
                "source": source,
            }
        )
        return MediaAssetRecord(
            asset_id="asset-computer-1",
            session_id=session_id,
            workspace_id=workspace_id,
            storage_kind=MediaAssetStorageKind.LOCAL,
            modality=modality,
            mime_type=mime_type,
            name=name,
            relative_path="asset-computer-1.png",
            size_bytes=size_bytes if size_bytes is not None else len(data),
            width=width,
            height=height,
            duration_ms=duration_ms,
            thumbnail_asset_id=thumbnail_asset_id,
            source=source,
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
        )

    def to_content_part(self, record: MediaAssetRecord) -> MediaRefContentPart:
        return MediaRefContentPart(
            asset_id=record.asset_id,
            session_id=record.session_id,
            modality=record.modality,
            mime_type=record.mime_type,
            name=record.name,
            url=f"/api/sessions/{record.session_id}/media/{record.asset_id}/file",
            size_bytes=record.size_bytes,
            width=record.width,
            height=record.height,
            duration_ms=record.duration_ms,
            thumbnail_asset_id=record.thumbnail_asset_id,
        )


class _FakeDeps:
    def __init__(self) -> None:
        self.session_id = "session-1"
        self.workspace_id = "workspace-1"
        self.media_asset_service = _FakeMediaAssetService()


class _FakeContext:
    def __init__(self) -> None:
        self.deps = _FakeDeps()


def test_approval_request_uses_builtin_computer_descriptor() -> None:
    request = _approval_request(
        "drag_between",
        target=ComputerActionTarget(x=1, y=2, end_x=3, end_y=4),
    )

    assert request is not None
    assert request.permission_scope == "destructive"
    assert request.risk_level == "destructive"
    assert request.target_summary == "(1, 2) | -> (3, 4)"
    assert request.source == "tool"
    assert request.execution_surface == "desktop"


def test_project_result_attaches_media_ref_when_screenshot_is_present() -> None:
    descriptor = describe_builtin_tool("capture_screen")
    assert descriptor is not None
    ctx = _FakeContext()
    result = ComputerActionResult(
        action=descriptor,
        message="Captured the screen.",
        observation=ComputerObservation(
            text="snapshot",
            screenshot_bytes=b"\x89PNG\r\n\x1a\nproject",
            screenshot_mime_type="image/png",
            screenshot_name="screen.png",
            screenshot_width=1280,
            screenshot_height=720,
        ),
        data={"window_count": 2},
    )

    projection = _project_result(
        ctx=cast(ToolContext, cast(object, ctx)), result=result
    )
    visible_data = cast(dict[str, JsonValue], projection.visible_data)

    assert visible_data["text"] == "Captured the screen."
    content = cast(list[dict[str, JsonValue]], visible_data["content"])
    assert isinstance(content, list)
    assert content[0]["kind"] == "media_ref"
    assert content[0]["asset_id"] == "asset-computer-1"
    assert projection.internal_data is not None
    internal_data = cast(dict[str, JsonValue], projection.internal_data)
    assert internal_data["media_asset_id"] == "asset-computer-1"
    assert ctx.deps.media_asset_service.calls[0]["source"] == "computer_tool"
