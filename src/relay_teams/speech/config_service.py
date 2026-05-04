# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from json import JSONDecodeError, dumps, loads
import logging
from pathlib import Path

from pydantic import JsonValue, ValidationError

from relay_teams.logger import get_logger, log_event
from relay_teams.providers.model_config import ModelEndpointConfig, ProviderType
from relay_teams.speech.models import (
    SUPPORTED_REALTIME_STT_MODELS,
    SpeechConfig,
    SpeechConfigUpdate,
)

import asyncio

LOGGER = get_logger(__name__)


class SpeechConfigService:
    def __init__(
        self,
        *,
        config_dir: Path,
        get_profiles: Callable[[], dict[str, ModelEndpointConfig]],
    ) -> None:
        self._config_file = config_dir / "speech.json"
        self._get_profiles = get_profiles

    def get_config(self) -> SpeechConfig:
        if not self._config_file.exists():
            return SpeechConfig()
        try:
            payload = loads(self._config_file.read_text(encoding="utf-8"))
        except (JSONDecodeError, OSError, UnicodeError) as exc:
            self._log_invalid_config(error=exc)
            return SpeechConfig()
        if not isinstance(payload, dict):
            return SpeechConfig()
        try:
            return SpeechConfig.model_validate(payload)
        except ValidationError as exc:
            self._log_invalid_config(error=exc)
            return SpeechConfig()

    def save_config(self, config: SpeechConfigUpdate) -> SpeechConfig:
        self.validate_config(config)
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        payload = config.model_dump(mode="json", exclude_none=True)
        self._config_file.write_text(
            dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return self.get_config()

    def validate_config(self, config: SpeechConfig) -> None:
        if config.stt_profile_name is None:
            return
        self.resolve_stt_profile(config.stt_profile_name)

    def resolve_configured_profile(self) -> tuple[SpeechConfig, ModelEndpointConfig]:
        config = self.get_config()
        if config.stt_profile_name is None:
            raise ValueError("Speech STT profile is not configured.")
        return config, self.resolve_stt_profile(config.stt_profile_name)

    def resolve_stt_profile(self, profile_name: str) -> ModelEndpointConfig:
        profiles = self._get_profiles()
        profile = profiles.get(profile_name)
        if profile is None:
            raise ValueError(f"Unknown STT model profile: {profile_name}")
        if profile.provider != ProviderType.OPENAI_COMPATIBLE:
            raise ValueError(
                "STT model profile must use the openai_compatible provider."
            )
        if not is_supported_realtime_stt_profile(profile):
            raise ValueError(f"Unsupported realtime STT model: {profile.model}")
        return profile

    def get_config_payload(self) -> dict[str, JsonValue]:
        config = self.get_config()
        payload = config.model_dump(mode="json")
        payload["supported_models"] = sorted(SUPPORTED_REALTIME_STT_MODELS)
        payload["configured"] = self._has_resolvable_stt_profile(config)
        return payload

    def _has_resolvable_stt_profile(self, config: SpeechConfig) -> bool:
        if config.stt_profile_name is None:
            return False
        try:
            self.resolve_stt_profile(config.stt_profile_name)
        except ValueError:
            return False
        return True

    def _log_invalid_config(self, *, error: Exception) -> None:
        log_event(
            LOGGER,
            logging.WARNING,
            event="speech.config.invalid",
            message="Ignoring invalid persisted speech config",
            payload={
                "path": str(self._config_file),
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )

    async def get_config_payload_async(self) -> dict[str, JsonValue]:

        return await asyncio.to_thread(self.get_config_payload)

    async def save_config_async(self, config: SpeechConfigUpdate) -> SpeechConfig:

        return await asyncio.to_thread(self.save_config, config)


def is_supported_realtime_stt_model(model_name: str) -> bool:
    normalized = model_name.strip()
    if normalized == "gpt-4o-transcribe-diarize":
        return False
    if normalized in SUPPORTED_REALTIME_STT_MODELS:
        return True
    return normalized.startswith("gpt-4o-mini-transcribe-")


def is_supported_realtime_stt_profile(profile: ModelEndpointConfig) -> bool:
    realtime_model = profile.speech_realtime.model
    effective_model = realtime_model if realtime_model is not None else profile.model
    if effective_model.strip() == "gpt-4o-transcribe-diarize":
        return False
    if realtime_model is not None:
        return True
    if is_supported_realtime_stt_model(profile.model):
        return True
    return profile.capabilities.input.audio is True
