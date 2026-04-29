# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.providers.model_config import (
    ModelCapabilities,
    ModelEndpointConfig,
    ModelModalityMatrix,
    ProviderType,
    SpeechRealtimeConfig,
)
from relay_teams.speech import SpeechConfigService, SpeechConfigUpdate
from relay_teams.speech.config_service import is_supported_realtime_stt_model
from relay_teams.speech.models import SpeechConfig


def test_speech_config_defaults_to_unconfigured(tmp_path: Path) -> None:
    service = SpeechConfigService(config_dir=tmp_path, get_profiles=lambda: {})

    payload = service.get_config_payload()

    assert payload["configured"] is False
    assert payload["stt_profile_name"] is None


def test_speech_config_ignores_non_object_config_file(tmp_path: Path) -> None:
    (tmp_path / "speech.json").write_text("[]", encoding="utf-8")
    service = SpeechConfigService(config_dir=tmp_path, get_profiles=lambda: {})

    config = service.get_config()

    assert config.stt_profile_name is None


def test_speech_config_ignores_malformed_config_file(tmp_path: Path) -> None:
    (tmp_path / "speech.json").write_text("{", encoding="utf-8")
    service = SpeechConfigService(config_dir=tmp_path, get_profiles=lambda: {})

    config = service.get_config()

    assert config.stt_profile_name is None


def test_speech_config_ignores_unreadable_config_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "speech.json"
    config_file.write_text("{}", encoding="utf-8")
    original_read_text = Path.read_text

    def fail_read_text(
        self: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if self == config_file:
            raise OSError("permission denied")
        return original_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", fail_read_text)
    service = SpeechConfigService(config_dir=tmp_path, get_profiles=lambda: {})

    config = service.get_config()

    assert config.stt_profile_name is None


def test_speech_config_ignores_non_utf8_config_file(tmp_path: Path) -> None:
    (tmp_path / "speech.json").write_bytes(b"\xff")
    service = SpeechConfigService(config_dir=tmp_path, get_profiles=lambda: {})

    config = service.get_config()

    assert config.stt_profile_name is None


def test_speech_config_ignores_invalid_config_file(tmp_path: Path) -> None:
    (tmp_path / "speech.json").write_text(
        '{"stt_profile_name": "stt", "unexpected": true}',
        encoding="utf-8",
    )
    service = SpeechConfigService(config_dir=tmp_path, get_profiles=lambda: {})

    config = service.get_config()

    assert config.stt_profile_name is None


def test_speech_config_validate_allows_missing_profile(tmp_path: Path) -> None:
    service = SpeechConfigService(config_dir=tmp_path, get_profiles=lambda: {})

    service.validate_config(SpeechConfig())


def test_speech_config_requires_configured_profile(tmp_path: Path) -> None:
    service = SpeechConfigService(config_dir=tmp_path, get_profiles=lambda: {})

    with pytest.raises(ValueError, match="Speech STT profile is not configured"):
        service.resolve_configured_profile()


def test_speech_config_saves_supported_stt_profile(tmp_path: Path) -> None:
    service = SpeechConfigService(
        config_dir=tmp_path,
        get_profiles=lambda: {
            "stt": ModelEndpointConfig(
                provider=ProviderType.OPENAI_COMPATIBLE,
                model="gpt-4o-mini-transcribe",
                base_url="https://api.openai.com/v1",
                api_key="test-key",
            )
        },
    )

    saved = service.save_config(
        SpeechConfigUpdate(stt_profile_name=" stt ", language=" zh-CN ")
    )

    assert saved.stt_profile_name == "stt"
    assert saved.language == "zh-CN"
    assert service.get_config().stt_profile_name == "stt"


def test_speech_config_rejects_unknown_profile(tmp_path: Path) -> None:
    service = SpeechConfigService(config_dir=tmp_path, get_profiles=lambda: {})

    with pytest.raises(ValueError, match="Unknown STT model profile"):
        service.save_config(SpeechConfigUpdate(stt_profile_name="missing"))


def test_speech_config_payload_marks_missing_profile_unconfigured(
    tmp_path: Path,
) -> None:
    (tmp_path / "speech.json").write_text(
        '{"stt_profile_name": "missing"}',
        encoding="utf-8",
    )
    service = SpeechConfigService(config_dir=tmp_path, get_profiles=lambda: {})

    payload = service.get_config_payload()

    assert payload["stt_profile_name"] == "missing"
    assert payload["configured"] is False


def test_speech_config_rejects_diarize_model(tmp_path: Path) -> None:
    service = SpeechConfigService(
        config_dir=tmp_path,
        get_profiles=lambda: {
            "diarize": ModelEndpointConfig(
                provider=ProviderType.OPENAI_COMPATIBLE,
                model="gpt-4o-transcribe-diarize",
                base_url="https://api.openai.com/v1",
                api_key="test-key",
            )
        },
    )

    with pytest.raises(ValueError, match="Unsupported realtime STT model"):
        service.save_config(SpeechConfigUpdate(stt_profile_name="diarize"))


def test_speech_config_rejects_non_openai_compatible_profile(tmp_path: Path) -> None:
    service = SpeechConfigService(
        config_dir=tmp_path,
        get_profiles=lambda: {
            "anthropic": ModelEndpointConfig(
                provider=ProviderType.ANTHROPIC,
                model="claude-sonnet-4-5",
                base_url="https://api.anthropic.com",
                api_key="test-key",
            )
        },
    )

    with pytest.raises(ValueError, match="openai_compatible provider"):
        service.save_config(SpeechConfigUpdate(stt_profile_name="anthropic"))


def test_diarize_model_is_not_supported_realtime_stt_model() -> None:
    assert is_supported_realtime_stt_model("gpt-4o-transcribe-diarize") is False


def test_speech_config_accepts_profile_marked_as_stt(tmp_path: Path) -> None:
    service = SpeechConfigService(
        config_dir=tmp_path,
        get_profiles=lambda: {
            "third-party-stt": ModelEndpointConfig(
                provider=ProviderType.OPENAI_COMPATIBLE,
                model="mimo-v2.5-stt",
                base_url="https://api.example.test/v1",
                api_key="test-key",
                capabilities=ModelCapabilities(
                    input=ModelModalityMatrix(audio=True),
                    output=ModelModalityMatrix(text=True, audio=False),
                ),
            )
        },
    )

    saved = service.save_config(SpeechConfigUpdate(stt_profile_name="third-party-stt"))

    assert saved.stt_profile_name == "third-party-stt"


def test_speech_config_accepts_profile_with_realtime_model_override(
    tmp_path: Path,
) -> None:
    service = SpeechConfigService(
        config_dir=tmp_path,
        get_profiles=lambda: {
            "ali": ModelEndpointConfig(
                provider=ProviderType.OPENAI_COMPATIBLE,
                model="qwen3-plus",
                base_url="https://dashscope.example.test/compatible-mode/v1",
                api_key="test-key",
                speech_realtime=SpeechRealtimeConfig(model="qwen3-omni-flash-realtime"),
            )
        },
    )

    saved = service.save_config(SpeechConfigUpdate(stt_profile_name="ali"))

    assert saved.stt_profile_name == "ali"
    assert service.get_config_payload()["configured"] is True


def test_speech_config_rejects_profile_marked_as_tts(tmp_path: Path) -> None:
    service = SpeechConfigService(
        config_dir=tmp_path,
        get_profiles=lambda: {
            "tts": ModelEndpointConfig(
                provider=ProviderType.OPENAI_COMPATIBLE,
                model="mimo-v2.5-tts",
                base_url="https://api.example.test/v1",
                api_key="test-key",
                capabilities=ModelCapabilities(
                    input=ModelModalityMatrix(audio=False),
                    output=ModelModalityMatrix(audio=True),
                ),
            )
        },
    )

    with pytest.raises(ValueError, match="Unsupported realtime STT model"):
        service.save_config(SpeechConfigUpdate(stt_profile_name="tts"))
