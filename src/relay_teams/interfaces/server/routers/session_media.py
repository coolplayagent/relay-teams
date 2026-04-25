# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from relay_teams.interfaces.server.async_call import call_maybe_async
from relay_teams.interfaces.server.deps import (
    get_media_asset_service,
    get_session_service,
)
from relay_teams.media import (
    MediaAssetService,
    MediaModality,
    MediaRefContentPart,
    infer_media_modality,
)
from relay_teams.sessions.session_service import SessionService
from relay_teams.validation import RequiredIdentifierStr

router = APIRouter(prefix="/sessions", tags=["Sessions"])


@router.get("/{session_id}/media", response_model=list[MediaRefContentPart])
async def list_session_media(
    session_id: RequiredIdentifierStr,
    session_service: Annotated[SessionService, Depends(get_session_service)],
    media_asset_service: Annotated[MediaAssetService, Depends(get_media_asset_service)],
) -> list[MediaRefContentPart]:
    try:
        await call_maybe_async(session_service.get_session, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    records = await call_maybe_async(
        media_asset_service.list_session_assets,
        session_id,
    )
    return [media_asset_service.to_content_part(record) for record in records]


@router.post("/{session_id}/media", response_model=MediaRefContentPart)
async def upload_session_media(
    session_id: RequiredIdentifierStr,
    file: Annotated[UploadFile, File(...)],
    session_service: Annotated[SessionService, Depends(get_session_service)],
    media_asset_service: Annotated[MediaAssetService, Depends(get_media_asset_service)],
    modality: Annotated[MediaModality | None, Form()] = None,
) -> MediaRefContentPart:
    try:
        session = await call_maybe_async(session_service.get_session, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded media file is empty")

    content_type = str(file.content_type or "").strip()
    try:
        resolved_modality = (
            modality
            if modality is not None
            else infer_media_modality(
                content_type=content_type,
                filename=str(file.filename or ""),
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not content_type:
        content_type = _default_mime_type(resolved_modality)

    record = await call_maybe_async(
        media_asset_service.store_bytes,
        session_id=session_id,
        workspace_id=session.workspace_id,
        modality=resolved_modality,
        mime_type=content_type,
        data=raw,
        name=str(file.filename or ""),
        size_bytes=len(raw),
        source="session_upload",
    )
    return media_asset_service.to_content_part(record)


@router.get("/{session_id}/media/{asset_id}", response_model=MediaRefContentPart)
async def get_session_media(
    session_id: RequiredIdentifierStr,
    asset_id: RequiredIdentifierStr,
    session_service: Annotated[SessionService, Depends(get_session_service)],
    media_asset_service: Annotated[MediaAssetService, Depends(get_media_asset_service)],
) -> MediaRefContentPart:
    try:
        await call_maybe_async(session_service.get_session, session_id)
        record = await call_maybe_async(media_asset_service.get_asset, asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if record.session_id != session_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    return media_asset_service.to_content_part(record)


@router.get("/{session_id}/media/{asset_id}/file", response_model=None)
async def get_session_media_file(
    session_id: RequiredIdentifierStr,
    asset_id: RequiredIdentifierStr,
    session_service: Annotated[SessionService, Depends(get_session_service)],
    media_asset_service: Annotated[MediaAssetService, Depends(get_media_asset_service)],
) -> FileResponse | RedirectResponse:
    try:
        await call_maybe_async(session_service.get_session, session_id)
        record = await call_maybe_async(media_asset_service.get_asset, asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if record.session_id != session_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    if record.external_url is not None and record.external_url.strip():
        return RedirectResponse(url=record.external_url.strip())
    try:
        file_path, media_type = await call_maybe_async(
            media_asset_service.get_asset_file,
            session_id=session_id,
            asset_id=asset_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path=file_path,
        filename=file_path.name,
        media_type=media_type,
    )


def _default_mime_type(modality: MediaModality) -> str:
    if modality == MediaModality.IMAGE:
        return "image/png"
    if modality == MediaModality.AUDIO:
        return "audio/mpeg"
    return "video/mp4"
