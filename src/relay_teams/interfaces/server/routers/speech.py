# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from pydantic import JsonValue

from relay_teams.interfaces.server.deps import (
    get_speech_config_service,
    get_websocket_realtime_stt_proxy_service,
)
from relay_teams.speech import (
    RealtimeSttProxyService,
    SpeechConfigService,
    SpeechConfigUpdate,
)

router = APIRouter(prefix="/speech", tags=["Speech"])


@router.get("/config")
async def get_speech_config(
    service: SpeechConfigService = Depends(get_speech_config_service),
) -> dict[str, JsonValue]:
    return await service.get_config_payload_async()


@router.put("/config")
async def save_speech_config(
    config: SpeechConfigUpdate,
    service: SpeechConfigService = Depends(get_speech_config_service),
) -> dict[str, JsonValue]:
    try:
        await service.save_config_async(config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await service.get_config_payload_async()


@router.websocket("/stt/stream")
async def stream_stt(
    websocket: WebSocket,
    service: RealtimeSttProxyService = Depends(
        get_websocket_realtime_stt_proxy_service
    ),
) -> None:
    await service.handle_client(websocket)
