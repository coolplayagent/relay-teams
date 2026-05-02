# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from base64 import b64encode
from collections.abc import Mapping
from json import dumps, loads
import logging
from ssl import SSLContext
from typing import AsyncContextManager, Protocol
from urllib.parse import quote, urlparse, urlunparse

from fastapi import WebSocket
from pydantic import BaseModel, ConfigDict
from starlette.websockets import WebSocketDisconnect
import websockets

from relay_teams.logger import get_logger, log_event
from relay_teams.net.websocket import (
    build_websocket_ssl_context,
    resolve_websocket_proxy_url,
)
from relay_teams.providers.model_config import (
    ModelEndpointConfig,
    RealtimeSttStopEventType,
)
from relay_teams.providers.openai_support import build_model_request_headers
from relay_teams.speech.config_service import SpeechConfigService
from relay_teams.speech.models import SpeechConfig

LOGGER = get_logger(__name__)
REALTIME_AUDIO_FORMAT = "pcm16"
REALTIME_AUDIO_SAMPLE_RATE = 24000
REALTIME_ASR_AUDIO_FORMAT = "pcm"
REALTIME_ASR_AUDIO_SAMPLE_RATE = 16000
REALTIME_CANDIDATE_EVENT_TIMEOUT_SECONDS = 0.2
REALTIME_READY_TIMEOUT_SECONDS = 5.0
REALTIME_READY_EVENT_TYPES = frozenset(
    {
        "session.updated",
        "transcription_session.updated",
    }
)


class RealtimeSttConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    uri: str
    model: str
    session_update_type: str
    input_audio_format: str
    input_audio_sample_rate: int | None = None
    send_model_in_session_update: bool
    stop_event_type: RealtimeSttStopEventType
    send_openai_beta_header: bool


class RealtimeSttConnectionError(Exception):
    def __init__(self, attempts: tuple[str, ...]) -> None:
        self.attempts = attempts
        super().__init__("Realtime STT connection failed.")


class RealtimeSttCandidateError(Exception):
    pass


class RealtimeWebSocket(Protocol):
    @staticmethod
    async def send(message: str | bytes) -> None:
        raise NotImplementedError

    @staticmethod
    async def recv() -> str | bytes:
        raise NotImplementedError

    @staticmethod
    async def close() -> None:
        raise NotImplementedError


class RealtimeWebSocketConnector(Protocol):
    def __call__(
        self,
        uri: str,
        *,
        additional_headers: Mapping[str, str],
        proxy: str | bool | None,
        ssl: SSLContext | None,
        open_timeout: float | None,
    ) -> AsyncContextManager[RealtimeWebSocket]:
        raise NotImplementedError


class RealtimeSttProxyService:
    def __init__(
        self,
        *,
        speech_config_service: SpeechConfigService,
        connector: RealtimeWebSocketConnector | None = None,
    ) -> None:
        self._speech_config_service = speech_config_service
        self._connector = websockets.connect if connector is None else connector
        self._candidate_cache: dict[
            tuple[str, str, str, str], RealtimeSttConnectionConfig
        ] = {}

    async def handle_client(self, websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            speech_config, profile_config = (
                self._speech_config_service.resolve_configured_profile()
            )
        except ValueError as exc:
            await _send_client_error(websocket, code="missing_config", message=str(exc))
            await websocket.close(code=1008)
            return

        try:
            await self._connect_and_proxy(websocket, speech_config, profile_config)
        except WebSocketDisconnect:
            return
        except RealtimeSttConnectionError as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="speech.stt.proxy_failed",
                message="Realtime STT proxy failed",
                payload={"attempts": list(exc.attempts)},
            )
            await _send_client_error(
                websocket,
                code="upstream_error",
                message="Realtime transcription failed.",
            )
            await _close_client_quietly(websocket)
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="speech.stt.proxy_failed",
                message="Realtime STT proxy failed",
                payload={"error": str(exc)},
            )
            await _send_client_error(
                websocket,
                code="upstream_error",
                message="Realtime transcription failed.",
            )
            await _close_client_quietly(websocket)

    async def _connect_and_proxy(
        self,
        websocket: WebSocket,
        speech_config: SpeechConfig,
        profile_config: ModelEndpointConfig,
    ) -> None:
        cache_key = build_realtime_stt_cache_key(profile_config)
        candidates = prioritize_cached_realtime_stt_candidate(
            build_realtime_stt_candidates(profile_config),
            self._candidate_cache.get(cache_key),
        )
        failed_attempts: list[str] = []
        for candidate in candidates:
            connected = False
            try:
                headers = build_model_request_headers(
                    profile_config,
                    extra_headers=(
                        {"OpenAI-Beta": "realtime=v1"}
                        if candidate.send_openai_beta_header
                        else {}
                    ),
                )
                async with self._connector(
                    candidate.uri,
                    additional_headers=headers,
                    proxy=resolve_websocket_proxy_url(candidate.uri),
                    ssl=build_websocket_ssl_context(candidate.uri),
                    open_timeout=profile_config.connect_timeout_seconds,
                ) as upstream:
                    initial_event = await _raise_for_initial_upstream_error(upstream)
                    await upstream.send(
                        dumps(
                            build_transcription_session_update(
                                speech_config,
                                profile_config,
                                candidate,
                            )
                        )
                    )
                    await _wait_for_upstream_ready(
                        upstream,
                        initial_event=initial_event,
                    )
                    self._candidate_cache[cache_key] = candidate
                    await _send_client_status(
                        websocket,
                        "ready",
                        sample_rate=(
                            candidate.input_audio_sample_rate
                            or REALTIME_AUDIO_SAMPLE_RATE
                        ),
                    )
                    connected = True
                    await self._proxy_until_closed(
                        websocket,
                        upstream,
                        stop_event_type=candidate.stop_event_type,
                    )
                    return
            except WebSocketDisconnect:
                raise
            except Exception as exc:
                if connected:
                    raise
                failed_attempts.append(candidate.uri)
                log_event(
                    LOGGER,
                    logging.DEBUG,
                    event="speech.stt.candidate_failed",
                    message="Realtime STT candidate failed",
                    payload={"uri": candidate.uri, "error": str(exc)},
                )
        raise RealtimeSttConnectionError(tuple(failed_attempts))

    @staticmethod
    async def _proxy_until_closed(
        websocket: WebSocket,
        upstream: RealtimeWebSocket,
        *,
        stop_event_type: str,
    ) -> None:
        client_to_upstream = asyncio.create_task(
            _forward_client_audio(
                websocket,
                upstream,
                stop_event_type=stop_event_type,
            ),
            name="speech-stt-client-to-upstream",
        )
        upstream_to_client = asyncio.create_task(
            _forward_upstream_events(websocket, upstream),
            name="speech-stt-upstream-to-client",
        )
        done, pending = await asyncio.wait(
            {client_to_upstream, upstream_to_client},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exception = task.exception()
            if exception is not None:
                raise exception


def build_realtime_transcription_url(base_url: str) -> str:
    parsed = urlparse(base_url.rstrip("/"))
    scheme = "wss" if parsed.scheme in {"https", "wss"} else "ws"
    path = parsed.path.rstrip("/") + "/realtime"
    return urlunparse((scheme, parsed.netloc, path, "", "intent=transcription", ""))


def build_realtime_query_model_url(base_url: str, model: str) -> str:
    parsed = urlparse(base_url.rstrip("/"))
    scheme = "wss" if parsed.scheme in {"https", "wss"} else "ws"
    return urlunparse(
        (
            scheme,
            parsed.netloc,
            "/api-ws/v1/realtime",
            "",
            f"model={quote(model, safe='')}",
            "",
        )
    )


def build_realtime_stt_url(profile_config: ModelEndpointConfig) -> str:
    return build_realtime_stt_candidates(profile_config)[0].uri


def build_realtime_stt_candidates(
    profile_config: ModelEndpointConfig,
) -> tuple[RealtimeSttConnectionConfig, ...]:
    template = profile_config.speech_realtime.websocket_url_template
    realtime_model = resolve_realtime_stt_model(profile_config)
    if template is not None:
        return (
            RealtimeSttConnectionConfig(
                uri=template.replace("{model}", quote(realtime_model, safe="")),
                model=realtime_model,
                session_update_type="transcription_session.update",
                input_audio_format=REALTIME_AUDIO_FORMAT,
                send_model_in_session_update=(
                    profile_config.speech_realtime.send_model_in_session_update
                ),
                stop_event_type=profile_config.speech_realtime.stop_event_type,
                send_openai_beta_header=should_send_openai_beta_header(profile_config),
            ),
        )

    candidates = [
        RealtimeSttConnectionConfig(
            uri=build_realtime_transcription_url(profile_config.base_url),
            model=realtime_model,
            session_update_type="transcription_session.update",
            input_audio_format=REALTIME_AUDIO_FORMAT,
            send_model_in_session_update=(
                profile_config.speech_realtime.send_model_in_session_update
            ),
            stop_event_type=profile_config.speech_realtime.stop_event_type,
            send_openai_beta_header=should_send_openai_beta_header(profile_config),
        )
    ]
    for model in build_realtime_stt_model_candidates(realtime_model):
        candidates.append(
            RealtimeSttConnectionConfig(
                uri=build_realtime_query_model_url(profile_config.base_url, model),
                model=model,
                session_update_type="session.update",
                input_audio_format=REALTIME_ASR_AUDIO_FORMAT,
                input_audio_sample_rate=REALTIME_ASR_AUDIO_SAMPLE_RATE,
                send_model_in_session_update=False,
                stop_event_type="session.finish",
                send_openai_beta_header=should_send_openai_beta_header(profile_config),
            )
        )
    return tuple(candidates)


def build_realtime_stt_model_candidates(model: str) -> tuple[str, ...]:
    candidates = [model]
    for family_variant in _build_realtime_family_model_candidates(model):
        if not family_variant.endswith("-realtime"):
            family_variant = f"{family_variant}-realtime"
        candidates.append(family_variant)
    if not model.endswith("-realtime"):
        candidates.append(f"{model}-realtime")
    return tuple(dict.fromkeys(candidates))


def _build_realtime_family_model_candidates(model: str) -> tuple[str, ...]:
    candidates: list[str] = []
    for source, target in (
        ("-omni-", "-asr-"),
        ("_omni_", "_asr_"),
        ("omni", "asr"),
    ):
        if source in model:
            candidates.append(model.replace(source, target, 1))
    return tuple(candidates)


def prioritize_cached_realtime_stt_candidate(
    candidates: tuple[RealtimeSttConnectionConfig, ...],
    cached_candidate: RealtimeSttConnectionConfig | None,
) -> tuple[RealtimeSttConnectionConfig, ...]:
    if cached_candidate is None or cached_candidate not in candidates:
        return candidates
    return (
        cached_candidate,
        *tuple(candidate for candidate in candidates if candidate != cached_candidate),
    )


def build_realtime_stt_cache_key(
    profile_config: ModelEndpointConfig,
) -> tuple[str, str, str, str]:
    return (
        profile_config.provider.value,
        profile_config.model,
        profile_config.base_url,
        profile_config.speech_realtime.model_dump_json(),
    )


def resolve_realtime_stt_model(profile_config: ModelEndpointConfig) -> str:
    return profile_config.speech_realtime.model or profile_config.model


def should_send_openai_beta_header(profile_config: ModelEndpointConfig) -> bool:
    override = profile_config.speech_realtime.send_openai_beta_header
    if override is not None:
        return override
    return profile_config.speech_realtime.websocket_url_template is None


def build_transcription_session_update(
    speech_config: SpeechConfig,
    profile_config: ModelEndpointConfig,
    connection_config: RealtimeSttConnectionConfig | None = None,
) -> dict[str, object]:
    input_audio_transcription: dict[str, object] = {}
    send_model = (
        connection_config.send_model_in_session_update
        if connection_config is not None
        else profile_config.speech_realtime.send_model_in_session_update
    )
    if send_model:
        input_audio_transcription["model"] = (
            connection_config.model
            if connection_config is not None
            else resolve_realtime_stt_model(profile_config)
        )
    if speech_config.language is not None:
        input_audio_transcription["language"] = speech_config.language
    if speech_config.prompt is not None:
        input_audio_transcription["prompt"] = speech_config.prompt

    input_audio_format = (
        connection_config.input_audio_format
        if connection_config is not None
        else REALTIME_AUDIO_FORMAT
    )
    session: dict[str, object] = {
        "input_audio_format": input_audio_format,
        "input_audio_transcription": input_audio_transcription,
        "turn_detection": {
            "type": "server_vad",
            "threshold": speech_config.vad_threshold,
            "prefix_padding_ms": speech_config.vad_prefix_padding_ms,
            "silence_duration_ms": speech_config.vad_silence_duration_ms,
        },
    }
    if (
        connection_config is not None
        and connection_config.session_update_type == "session.update"
    ):
        session["modalities"] = ["text"]
        session["sample_rate"] = (
            connection_config.input_audio_sample_rate or REALTIME_ASR_AUDIO_SAMPLE_RATE
        )
    if speech_config.noise_reduction != "disabled":
        session["input_audio_noise_reduction"] = {
            "type": speech_config.noise_reduction,
        }
    return {
        "type": (
            connection_config.session_update_type
            if connection_config is not None
            else "transcription_session.update"
        ),
        "session": session,
    }


async def _forward_client_audio(
    websocket: WebSocket,
    upstream: RealtimeWebSocket,
    *,
    stop_event_type: str,
) -> None:
    stop_sent = False
    while True:
        message = await websocket.receive()
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            await upstream.close()
            return
        bytes_payload = message.get("bytes")
        if isinstance(bytes_payload, bytes) and bytes_payload and not stop_sent:
            await upstream.send(
                dumps(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": b64encode(bytes_payload).decode("ascii"),
                    }
                )
            )
            continue
        text_payload = message.get("text")
        if isinstance(text_payload, str):
            if _is_stop_message(text_payload) and not stop_sent:
                stop_sent = True
                await upstream.send(dumps({"type": stop_event_type}))


async def _forward_upstream_events(
    websocket: WebSocket,
    upstream: RealtimeWebSocket,
) -> None:
    while True:
        try:
            raw_event = await upstream.recv()
        except websockets.ConnectionClosedOK:
            return
        if not isinstance(raw_event, str):
            continue
        event = _parse_json_object(raw_event)
        event_type = str(event.get("type") or "")
        if event_type.endswith(".delta"):
            text = _extract_transcript_delta(event)
            if text:
                await websocket.send_json(
                    {
                        "type": "delta",
                        "mode": "append",
                        "text": text,
                        "item_id": str(event.get("item_id") or event.get("item") or ""),
                    }
                )
            continue
        if event_type.endswith(".completed"):
            text = _extract_transcript_completed(event)
            if text:
                await websocket.send_json(
                    {
                        "type": "completed",
                        "text": text,
                        "item_id": str(event.get("item_id") or event.get("item") or ""),
                    }
                )
            continue
        if event_type == "error":
            await websocket.send_json(
                {
                    "type": "error",
                    "code": _extract_error_code(event),
                    "message": _extract_error_message(event),
                }
            )
            await upstream.close()
            return
        if event_type.endswith("input_audio_buffer.speech_started"):
            await websocket.send_json({"type": "speech", "status": "started"})
            continue
        if event_type.endswith("input_audio_buffer.speech_stopped"):
            await websocket.send_json({"type": "speech", "status": "stopped"})
            continue
        if event_type == "conversation.item.input_audio_transcription.text":
            text = _extract_transcript_completed(event)
            if text:
                await websocket.send_json(
                    {
                        "type": "delta",
                        "mode": "replace",
                        "text": text,
                        "item_id": str(event.get("item_id") or event.get("item") or ""),
                    }
                )
            continue
        if event_type == "session.finished":
            text = _extract_transcript_completed(event)
            if text:
                await websocket.send_json(
                    {
                        "type": "completed",
                        "text": text,
                        "item_id": str(event.get("item_id") or event.get("item") or ""),
                    }
                )
            await upstream.close()
            return


async def _raise_for_initial_upstream_error(
    upstream: RealtimeWebSocket,
) -> dict[str, object] | None:
    event = await _recv_upstream_event(
        upstream,
        timeout=REALTIME_CANDIDATE_EVENT_TIMEOUT_SECONDS,
    )
    if event is None:
        return None
    error_message = _find_upstream_error_message((event,))
    if error_message is not None:
        raise RealtimeSttCandidateError(error_message)
    return event


async def _wait_for_upstream_ready(
    upstream: RealtimeWebSocket,
    *,
    initial_event: dict[str, object] | None = None,
) -> None:
    deadline = asyncio.get_running_loop().time() + REALTIME_READY_TIMEOUT_SECONDS
    pending_event = initial_event
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise RealtimeSttCandidateError(
                "Realtime STT session did not become ready."
            )
        event = pending_event
        pending_event = None
        if event is None:
            event = await _recv_upstream_event(upstream, timeout=remaining)
        if event is None:
            raise RealtimeSttCandidateError(
                "Realtime STT session did not become ready."
            )
        event_type = str(event.get("type") or "")
        if event_type in REALTIME_READY_EVENT_TYPES:
            return
        error_message = _find_upstream_error_message((event,))
        if error_message is not None:
            raise RealtimeSttCandidateError(error_message)


async def _recv_upstream_event(
    upstream: RealtimeWebSocket,
    *,
    timeout: float,
) -> dict[str, object] | None:
    try:
        raw_event = await asyncio.wait_for(upstream.recv(), timeout=timeout)
    except TimeoutError:
        return None
    if not isinstance(raw_event, str):
        return {}
    return _parse_json_object(raw_event)


def _find_upstream_error_message(events: tuple[dict[str, object], ...]) -> str | None:
    for event in events:
        if str(event.get("type") or "") != "error":
            continue
        return _extract_error_message(event)
    return None


def _parse_json_object(payload: str) -> dict[str, object]:
    parsed = loads(payload)
    if isinstance(parsed, dict):
        return parsed
    return {}


def _extract_transcript_delta(event: Mapping[str, object]) -> str:
    for key in ("delta", "transcript", "text"):
        value = event.get(key)
        if isinstance(value, str):
            return value
    return ""


def _extract_transcript_completed(event: Mapping[str, object]) -> str:
    for key in ("transcript", "text"):
        value = event.get(key)
        if isinstance(value, str):
            return value
    return _extract_nested_transcript(event.get("item"))


def _extract_nested_transcript(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("transcript", "text"):
        child = value.get(key)
        if isinstance(child, str):
            return child
    content = value.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                transcript = _extract_nested_transcript(item)
                if transcript:
                    return transcript
    return ""


def _extract_error_code(event: Mapping[str, object]) -> str:
    error = event.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        if isinstance(code, str) and code:
            return code
    return "upstream_error"


def _extract_error_message(event: Mapping[str, object]) -> str:
    error = event.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    return "Realtime transcription failed."


def _is_stop_message(payload: str) -> bool:
    parsed = _parse_json_object(payload)
    return parsed.get("type") == "stop"


async def _send_client_status(
    websocket: WebSocket,
    status: str,
    *,
    sample_rate: int | None = None,
) -> None:
    payload: dict[str, object] = {"type": "status", "status": status}
    if sample_rate is not None:
        payload["sample_rate"] = sample_rate
    await websocket.send_json(payload)


async def _send_client_error(websocket: WebSocket, *, code: str, message: str) -> None:
    await websocket.send_json({"type": "error", "code": code, "message": message})


async def _close_client_quietly(websocket: WebSocket) -> None:
    try:
        await websocket.close()
    except RuntimeError:
        return
