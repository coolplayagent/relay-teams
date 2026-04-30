# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from json import dumps, loads
from ssl import SSLContext
from typing import cast

import pytest
from fastapi import WebSocket
import websockets

from relay_teams.providers.model_config import (
    ModelEndpointConfig,
    ProviderType,
    SpeechRealtimeConfig,
)
from relay_teams.speech.config_service import SpeechConfigService
from relay_teams.speech.models import SpeechConfig
from relay_teams.speech.realtime_stt import (
    REALTIME_AUDIO_SAMPLE_RATE,
    RealtimeSttProxyService,
    RealtimeSttCandidateError,
    _close_client_quietly,
    _extract_error_code,
    _extract_error_message,
    _extract_nested_transcript,
    _extract_transcript_completed,
    _extract_transcript_delta,
    _find_upstream_error_message,
    _forward_client_audio,
    _forward_upstream_events,
    _is_stop_message,
    _parse_json_object,
    _raise_for_initial_upstream_error,
    _recv_upstream_event,
    _send_client_status,
    _wait_for_upstream_ready,
    build_realtime_query_model_url,
    build_realtime_stt_candidates,
    build_realtime_stt_url,
    build_realtime_transcription_url,
    build_transcription_session_update,
    prioritize_cached_realtime_stt_candidate,
    should_send_openai_beta_header,
)


def test_build_realtime_transcription_url_from_openai_base_url() -> None:
    assert (
        build_realtime_transcription_url("https://api.openai.com/v1/")
        == "wss://api.openai.com/v1/realtime?intent=transcription"
    )


def test_realtime_url_builders_preserve_wss_base_url_scheme() -> None:
    assert (
        build_realtime_transcription_url("wss://api.example.test/v1/")
        == "wss://api.example.test/v1/realtime?intent=transcription"
    )
    assert (
        build_realtime_query_model_url("wss://api.example.test/v1/", "stt realtime")
        == "wss://api.example.test/api-ws/v1/realtime?model=stt%20realtime"
    )


def test_build_realtime_query_model_url_from_base_host() -> None:
    assert (
        build_realtime_query_model_url(
            "https://api.example.test/compatible-mode/v1/",
            "third party realtime",
        )
        == "wss://api.example.test/api-ws/v1/realtime?model=third%20party%20realtime"
    )


def test_default_realtime_candidates_include_generic_realtime_suffix() -> None:
    profile = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="third-party-omni-flash",
        base_url="https://api.example.test/compatible-mode/v1",
        api_key="test-key",
    )

    candidates = build_realtime_stt_candidates(profile)

    assert [candidate.uri for candidate in candidates] == [
        "wss://api.example.test/compatible-mode/v1/realtime?intent=transcription",
        "wss://api.example.test/api-ws/v1/realtime?model=third-party-omni-flash",
        "wss://api.example.test/api-ws/v1/realtime?model=third-party-asr-flash-realtime",
        "wss://api.example.test/api-ws/v1/realtime?model=third-party-omni-flash-realtime",
    ]
    assert candidates[0].send_model_in_session_update is True
    assert candidates[0].send_openai_beta_header is True
    assert candidates[1].send_model_in_session_update is False
    assert candidates[1].send_openai_beta_header is True
    assert candidates[1].stop_event_type == "session.finish"
    assert candidates[1].session_update_type == "session.update"
    assert candidates[1].input_audio_format == "pcm"
    assert candidates[1].input_audio_sample_rate == 16000


def test_realtime_model_override_keeps_query_model_fallback_candidates() -> None:
    profile = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="qwen3-plus",
        base_url="https://api.example.test/compatible-mode/v1",
        api_key="test-key",
        speech_realtime=SpeechRealtimeConfig(model="qwen3-omni-flash"),
    )

    candidates = build_realtime_stt_candidates(profile)

    assert [candidate.uri for candidate in candidates] == [
        "wss://api.example.test/compatible-mode/v1/realtime?intent=transcription",
        "wss://api.example.test/api-ws/v1/realtime?model=qwen3-omni-flash",
        "wss://api.example.test/api-ws/v1/realtime?model=qwen3-asr-flash-realtime",
        "wss://api.example.test/api-ws/v1/realtime?model=qwen3-omni-flash-realtime",
    ]
    assert [candidate.model for candidate in candidates] == [
        "qwen3-omni-flash",
        "qwen3-omni-flash",
        "qwen3-asr-flash-realtime",
        "qwen3-omni-flash-realtime",
    ]


def test_realtime_header_override_keeps_query_model_fallback_candidates() -> None:
    profile = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="third-party-omni-flash",
        base_url="https://api.example.test/compatible-mode/v1",
        api_key="test-key",
        speech_realtime=SpeechRealtimeConfig(send_openai_beta_header=False),
    )

    candidates = build_realtime_stt_candidates(profile)

    assert [candidate.uri for candidate in candidates] == [
        "wss://api.example.test/compatible-mode/v1/realtime?intent=transcription",
        "wss://api.example.test/api-ws/v1/realtime?model=third-party-omni-flash",
        "wss://api.example.test/api-ws/v1/realtime?model=third-party-asr-flash-realtime",
        "wss://api.example.test/api-ws/v1/realtime?model=third-party-omni-flash-realtime",
    ]
    assert [candidate.send_openai_beta_header for candidate in candidates] == [
        False,
        False,
        False,
        False,
    ]


def test_realtime_candidate_suffix_is_not_duplicated() -> None:
    profile = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="third-party-realtime",
        base_url="https://api.example.test/v1",
        api_key="test-key",
    )

    candidates = build_realtime_stt_candidates(profile)

    assert [candidate.model for candidate in candidates] == [
        "third-party-realtime",
        "third-party-realtime",
    ]


def test_cached_realtime_candidate_is_tried_first() -> None:
    profile = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="third-party",
        base_url="https://api.example.test/v1",
        api_key="test-key",
    )
    candidates = build_realtime_stt_candidates(profile)

    prioritized = prioritize_cached_realtime_stt_candidate(candidates, candidates[2])

    assert prioritized[0] == candidates[2]
    assert set(prioritized) == set(candidates)


def test_build_transcription_session_update_uses_configured_model() -> None:
    profile = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="gpt-4o-mini-transcribe",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
    )
    config = SpeechConfig(
        stt_profile_name="stt",
        language="zh-CN",
        prompt="domain terms",
        vad_threshold=0.4,
        noise_reduction="far_field",
    )

    event = build_transcription_session_update(config, profile)

    assert event["type"] == "transcription_session.update"
    session = event["session"]
    assert isinstance(session, dict)
    transcription = session["input_audio_transcription"]
    assert isinstance(transcription, dict)
    assert transcription["model"] == "gpt-4o-mini-transcribe"
    assert transcription["language"] == "zh-CN"
    assert transcription["prompt"] == "domain terms"
    turn_detection = session["turn_detection"]
    assert isinstance(turn_detection, dict)
    assert turn_detection["threshold"] == 0.4


def test_build_transcription_session_update_uses_connection_candidate() -> None:
    profile = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="third-party",
        base_url="https://api.example.test/v1",
        api_key="test-key",
    )
    candidate = build_realtime_stt_candidates(profile)[2]

    event = build_transcription_session_update(SpeechConfig(), profile, candidate)

    session = event["session"]
    assert isinstance(session, dict)
    transcription = session["input_audio_transcription"]
    assert isinstance(transcription, dict)
    assert "model" not in transcription


def test_custom_realtime_config_overrides_url_model_and_beta_header() -> None:
    profile = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="third-party-stt",
        base_url="https://api.example.test/v1",
        api_key="test-key",
        speech_realtime=SpeechRealtimeConfig(
            websocket_url_template="wss://realtime.example.test/stream?model={model}",
            model="third-party-stt realtime",
            send_openai_beta_header=False,
        ),
    )

    event = build_transcription_session_update(SpeechConfig(), profile)
    session = event["session"]
    assert isinstance(session, dict)
    transcription = session["input_audio_transcription"]
    assert isinstance(transcription, dict)
    assert transcription["model"] == "third-party-stt realtime"
    assert build_realtime_stt_url(profile) == (
        "wss://realtime.example.test/stream?model=third-party-stt%20realtime"
    )
    assert should_send_openai_beta_header(profile) is False


def test_custom_realtime_config_can_omit_model_from_session_update() -> None:
    profile = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="third-party-stt",
        base_url="https://api.example.test/v1",
        api_key="test-key",
        speech_realtime=SpeechRealtimeConfig(send_model_in_session_update=False),
    )

    event = build_transcription_session_update(SpeechConfig(), profile)

    session = event["session"]
    assert isinstance(session, dict)
    transcription = session["input_audio_transcription"]
    assert isinstance(transcription, dict)
    assert "model" not in transcription


class _FakeClientWebSocket:
    def __init__(self) -> None:
        self.sent_json: list[dict[str, object]] = []
        self.closed = False

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent_json.append(payload)

    async def accept(self) -> None:
        return

    async def close(self, code: int = 1000) -> None:
        self.closed = True

    async def receive(self) -> dict[str, object]:
        return {"type": "websocket.disconnect"}


class _FakeStopClientWebSocket:
    def __init__(self) -> None:
        self.messages = [
            {"type": "websocket.receive", "text": dumps({"type": "stop"})},
            {"type": "websocket.disconnect"},
        ]

    async def receive(self) -> dict[str, object]:
        return dict(self.messages.pop(0))


class _FakeAudioClientWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = [
            {"type": "websocket.receive", "bytes": b"abc"},
            {"type": "websocket.receive", "text": dumps({"type": "stop"})},
            {"type": "websocket.receive", "bytes": b"ignored"},
            {"type": "websocket.disconnect"},
        ]

    async def receive(self) -> dict[str, object]:
        return dict(self.messages.pop(0))


class _RuntimeCloseClientWebSocket(_FakeClientWebSocket):
    async def close(self, code: int = 1000) -> None:
        raise RuntimeError("already closed")


class _FakeUpstream:
    def __init__(
        self, events: Sequence[str | bytes | BaseException] | None = None
    ) -> None:
        self.events = list(events or [])
        self.sent: list[str | bytes] = []
        self.closed = False

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        if self.events:
            event = self.events.pop(0)
            if isinstance(event, BaseException):
                raise event
            return event
        await asyncio.sleep(3600)
        return ""

    async def close(self) -> None:
        self.closed = True


class _FakeSpeechConfigService:
    def __init__(self, profile: ModelEndpointConfig) -> None:
        self.profile = profile

    def resolve_configured_profile(self) -> tuple[SpeechConfig, ModelEndpointConfig]:
        return SpeechConfig(stt_profile_name="stt"), self.profile


class _MissingSpeechConfigService:
    def resolve_configured_profile(self) -> tuple[SpeechConfig, ModelEndpointConfig]:
        raise ValueError("missing speech config")


class _FakeConnectorContext:
    def __init__(self, upstream: _FakeUpstream) -> None:
        self.upstream = upstream

    async def __aenter__(self) -> _FakeUpstream:
        return self.upstream

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        return


class _FallbackConnector:
    def __init__(
        self,
        failing_uri: str,
        events: Sequence[str | bytes | BaseException] | None = None,
    ) -> None:
        self.failing_uri = failing_uri
        self.uris: list[str] = []
        self.upstream = _FakeUpstream(
            events
            or [
                dumps({"type": "session.created"}),
                dumps({"type": "session.updated"}),
            ]
        )

    def __call__(
        self,
        uri: str,
        *,
        additional_headers: Mapping[str, str],
        proxy: str | bool | None,
        ssl: SSLContext | None,
        open_timeout: float | None,
    ) -> _FakeConnectorContext:
        self.uris.append(uri)
        if uri == self.failing_uri:
            raise OSError("handshake failed")
        return _FakeConnectorContext(self.upstream)


class _AllFailConnector:
    def __init__(self) -> None:
        self.uris: list[str] = []

    def __call__(
        self,
        uri: str,
        *,
        additional_headers: Mapping[str, str],
        proxy: str | bool | None,
        ssl: SSLContext | None,
        open_timeout: float | None,
    ) -> _FakeConnectorContext:
        self.uris.append(uri)
        raise OSError("no route")


@pytest.mark.asyncio
async def test_forward_client_audio_uses_configured_stop_event() -> None:
    upstream = _FakeUpstream()

    await _forward_client_audio(
        cast(WebSocket, _FakeStopClientWebSocket()),
        upstream,
        stop_event_type="session.finish",
    )

    assert upstream.sent == [dumps({"type": "session.finish"})]
    assert upstream.closed is True


@pytest.mark.asyncio
async def test_forward_client_audio_sends_audio_and_ignores_audio_after_stop() -> None:
    upstream = _FakeUpstream()

    await _forward_client_audio(
        cast(WebSocket, _FakeAudioClientWebSocket()),
        upstream,
        stop_event_type="session.finish",
    )

    assert loads(cast(str, upstream.sent[0])) == {
        "type": "input_audio_buffer.append",
        "audio": "YWJj",
    }
    assert loads(cast(str, upstream.sent[1])) == {"type": "session.finish"}
    assert len(upstream.sent) == 2
    assert upstream.closed is True


@pytest.mark.asyncio
async def test_forward_upstream_events_handles_delta_and_completed_events() -> None:
    client = _FakeClientWebSocket()
    upstream = _FakeUpstream(
        [
            b"ignored",
            dumps({"type": "response.audio_transcript.delta", "delta": "hel"}),
            dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item": {"content": [{"transcript": "hello"}]},
                    "item_id": "item-2",
                }
            ),
            dumps({"type": "error"}),
        ]
    )

    await _forward_upstream_events(cast(WebSocket, client), upstream)

    assert client.sent_json == [
        {"type": "delta", "mode": "append", "text": "hel", "item_id": ""},
        {"type": "completed", "text": "hello", "item_id": "item-2"},
        {
            "type": "error",
            "code": "upstream_error",
            "message": "Realtime transcription failed.",
        },
    ]


@pytest.mark.asyncio
async def test_forward_upstream_events_handles_interim_text_event_and_stops_after_error() -> (
    None
):
    client = _FakeClientWebSocket()
    upstream = _FakeUpstream(
        [
            dumps(
                {
                    "type": "conversation.item.input_audio_transcription.text",
                    "text": "hello",
                    "item_id": "item-1",
                }
            ),
            dumps(
                {
                    "type": "error",
                    "error": {"code": "bad_request", "message": "bad stream"},
                }
            ),
        ]
    )

    await _forward_upstream_events(cast(WebSocket, client), upstream)

    assert client.sent_json == [
        {"type": "delta", "mode": "replace", "text": "hello", "item_id": "item-1"},
        {"type": "error", "code": "bad_request", "message": "bad stream"},
    ]
    assert upstream.closed is True


@pytest.mark.asyncio
async def test_forward_upstream_events_handles_session_finished_transcript() -> None:
    client = _FakeClientWebSocket()
    upstream = _FakeUpstream(
        [
            dumps(
                {
                    "type": "session.finished",
                    "transcript": "done",
                    "item_id": "item-1",
                }
            ),
        ]
    )

    await _forward_upstream_events(cast(WebSocket, client), upstream)

    assert client.sent_json == [
        {"type": "completed", "text": "done", "item_id": "item-1"},
    ]
    assert upstream.closed is True


@pytest.mark.asyncio
async def test_forward_upstream_events_treats_clean_close_as_terminal() -> None:
    client = _FakeClientWebSocket()
    upstream = _FakeUpstream(
        [
            dumps({"type": "response.audio_transcript.delta", "delta": "hel"}),
            websockets.ConnectionClosedOK(None, None),
        ]
    )

    await _forward_upstream_events(cast(WebSocket, client), upstream)

    assert client.sent_json == [
        {"type": "delta", "mode": "append", "text": "hel", "item_id": ""},
    ]
    assert upstream.closed is False


@pytest.mark.asyncio
async def test_forward_upstream_events_forwards_speech_activity() -> None:
    client = _FakeClientWebSocket()
    upstream = _FakeUpstream(
        [
            dumps({"type": "input_audio_buffer.speech_started"}),
            dumps({"type": "input_audio_buffer.speech_stopped"}),
            dumps({"type": "error", "error": {"message": "done"}}),
        ]
    )

    await _forward_upstream_events(cast(WebSocket, client), upstream)

    assert client.sent_json == [
        {"type": "speech", "status": "started"},
        {"type": "speech", "status": "stopped"},
        {"type": "error", "code": "upstream_error", "message": "done"},
    ]


@pytest.mark.asyncio
async def test_proxy_tries_next_realtime_candidate_when_handshake_fails() -> None:
    profile = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="third-party",
        base_url="https://api.example.test/v1",
        api_key="test-key",
    )
    candidates = build_realtime_stt_candidates(profile)
    connector = _FallbackConnector(candidates[0].uri)
    service = RealtimeSttProxyService(
        speech_config_service=cast(
            SpeechConfigService, _FakeSpeechConfigService(profile)
        ),
        connector=connector,
    )
    client = _FakeClientWebSocket()

    await service.handle_client(cast(WebSocket, client))

    assert connector.uris == [candidates[0].uri, candidates[1].uri]
    assert client.sent_json == [
        {"type": "status", "status": "ready", "sample_rate": 16000},
    ]
    assert len(connector.upstream.sent) == 1


@pytest.mark.asyncio
async def test_proxy_preserves_initial_upstream_ready_event() -> None:
    profile = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="third-party",
        base_url="https://api.example.test/v1",
        api_key="test-key",
    )
    connector = _FallbackConnector(
        "",
        events=[dumps({"type": "session.updated"})],
    )
    candidates = build_realtime_stt_candidates(profile)
    service = RealtimeSttProxyService(
        speech_config_service=cast(
            SpeechConfigService, _FakeSpeechConfigService(profile)
        ),
        connector=connector,
    )
    client = _FakeClientWebSocket()

    await service.handle_client(cast(WebSocket, client))

    assert connector.uris == [candidates[0].uri]
    assert client.sent_json == [
        {
            "type": "status",
            "status": "ready",
            "sample_rate": candidates[0].input_audio_sample_rate
            or REALTIME_AUDIO_SAMPLE_RATE,
        },
    ]
    assert len(connector.upstream.sent) == 1


@pytest.mark.asyncio
async def test_handle_client_reports_missing_speech_config() -> None:
    service = RealtimeSttProxyService(
        speech_config_service=cast(SpeechConfigService, _MissingSpeechConfigService()),
        connector=_AllFailConnector(),
    )
    client = _FakeClientWebSocket()

    await service.handle_client(cast(WebSocket, client))

    assert client.sent_json == [
        {
            "type": "error",
            "code": "missing_config",
            "message": "missing speech config",
        }
    ]
    assert client.closed is True


@pytest.mark.asyncio
async def test_handle_client_reports_upstream_connection_failure() -> None:
    profile = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="third-party",
        base_url="https://api.example.test/v1",
        api_key="test-key",
    )
    connector = _AllFailConnector()
    service = RealtimeSttProxyService(
        speech_config_service=cast(
            SpeechConfigService, _FakeSpeechConfigService(profile)
        ),
        connector=connector,
    )
    client = _FakeClientWebSocket()

    await service.handle_client(cast(WebSocket, client))

    assert connector.uris == [
        candidate.uri for candidate in build_realtime_stt_candidates(profile)
    ]
    assert client.sent_json == [
        {
            "type": "error",
            "code": "upstream_error",
            "message": "Realtime transcription failed.",
        }
    ]
    assert client.closed is True


@pytest.mark.asyncio
async def test_upstream_ready_helpers_handle_timeout_ready_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "relay_teams.speech.realtime_stt.REALTIME_READY_TIMEOUT_SECONDS",
        0.001,
    )
    empty_upstream = _FakeUpstream()
    assert await _recv_upstream_event(empty_upstream, timeout=0.001) is None
    assert await _recv_upstream_event(_FakeUpstream([b"binary"]), timeout=0.1) == {}
    assert await _recv_upstream_event(_FakeUpstream(["[]"]), timeout=0.1) == {}
    assert await _raise_for_initial_upstream_error(_FakeUpstream([])) is None
    initial_ready_event = await _raise_for_initial_upstream_error(
        _FakeUpstream([dumps({"type": "session.updated"})])
    )
    await _wait_for_upstream_ready(_FakeUpstream([]), initial_event=initial_ready_event)
    with pytest.raises(RealtimeSttCandidateError, match="bad stream"):
        await _raise_for_initial_upstream_error(
            _FakeUpstream(
                [dumps({"type": "error", "error": {"message": "bad stream"}})]
            )
        )
    await _wait_for_upstream_ready(
        _FakeUpstream(
            [dumps({"type": "session.created"}), dumps({"type": "session.updated"})]
        )
    )
    with pytest.raises(RealtimeSttCandidateError, match="become ready"):
        await _wait_for_upstream_ready(_FakeUpstream([]))
    with pytest.raises(RealtimeSttCandidateError, match="bad ready"):
        await _wait_for_upstream_ready(
            _FakeUpstream([dumps({"type": "error", "error": {"message": "bad ready"}})])
        )


@pytest.mark.asyncio
async def test_send_status_and_quiet_close_helpers() -> None:
    client = _FakeClientWebSocket()

    await _send_client_status(cast(WebSocket, client), "ready")
    await _close_client_quietly(cast(WebSocket, _RuntimeCloseClientWebSocket()))

    assert client.sent_json == [{"type": "status", "status": "ready"}]


def test_realtime_payload_helpers_cover_default_paths() -> None:
    assert _parse_json_object("[]") == {}
    assert _extract_transcript_delta({"other": "value"}) == ""
    assert _extract_transcript_completed({"item": {"text": "nested"}}) == "nested"
    assert _extract_transcript_completed({"item": "bad"}) == ""
    assert _extract_nested_transcript({"content": ["bad", {"text": "deep"}]}) == "deep"
    assert _extract_error_code({"error": {"code": "bad_request"}}) == "bad_request"
    assert _extract_error_code({"error": "bad"}) == "upstream_error"
    assert _extract_error_message({"error": {"message": "bad stream"}}) == "bad stream"
    assert _extract_error_message({"error": "bad"}) == "Realtime transcription failed."
    assert _find_upstream_error_message(({"type": "status"},)) is None
    assert _is_stop_message(dumps({"type": "stop"})) is True
