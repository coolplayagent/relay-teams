# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from typing import override

from relay_teams.media import ContentPart, MediaModality
from relay_teams.media import content_parts_to_text
from relay_teams.sessions.runs.run_models import (
    MediaGenerationConfig,
    RunKind,
    RunThinkingConfig,
)


class ProviderCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_modalities: tuple[MediaModality, ...] = ()
    conversation_output_modalities: tuple[MediaModality, ...] = ()
    native_generation_modalities: tuple[MediaModality, ...] = ()
    async_generation_modalities: tuple[MediaModality, ...] = ()


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    trace_id: str
    task_id: str
    session_id: str
    workspace_id: str
    conversation_id: str = ""
    instance_id: str
    role_id: str
    system_prompt: str
    user_prompt: str | None
    input: tuple[ContentPart, ...] = ()
    session_mode: str = "normal"
    run_kind: RunKind = RunKind.CONVERSATION
    generation_config: MediaGenerationConfig | None = None
    thinking: RunThinkingConfig = RunThinkingConfig()

    @property
    def prompt_text(self) -> str:
        if self.user_prompt is not None and self.user_prompt.strip():
            return self.user_prompt.strip()
        return content_parts_to_text(self.input)


class LLMProvider:
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    async def generate(self, _request: LLMRequest) -> str:
        raise NotImplementedError

    async def generate_image(self, _request: LLMRequest) -> tuple[ContentPart, ...]:
        raise RuntimeError("Image generation is not supported by this provider")

    async def generate_audio(self, _request: LLMRequest) -> tuple[ContentPart, ...]:
        raise RuntimeError("Audio generation is not supported by this provider")

    async def generate_video(self, _request: LLMRequest) -> tuple[ContentPart, ...]:
        raise RuntimeError("Video generation is not supported by this provider")


class EchoProvider(LLMProvider):
    @override
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            input_modalities=(
                MediaModality.IMAGE,
                MediaModality.AUDIO,
                MediaModality.VIDEO,
            ),
            conversation_output_modalities=(),
            native_generation_modalities=(),
            async_generation_modalities=(),
        )

    @override
    async def generate(self, request: LLMRequest) -> str:
        return f"ECHO: {request.prompt_text}"


class MisconfiguredProvider(LLMProvider):
    def __init__(self, message: str) -> None:
        self._message = message

    @override
    async def generate(self, _request: LLMRequest) -> str:
        raise RuntimeError(self._message)

    @override
    async def generate_image(self, _request: LLMRequest) -> tuple[ContentPart, ...]:
        raise RuntimeError(self._message)

    @override
    async def generate_audio(self, _request: LLMRequest) -> tuple[ContentPart, ...]:
        raise RuntimeError(self._message)

    @override
    async def generate_video(self, _request: LLMRequest) -> tuple[ContentPart, ...]:
        raise RuntimeError(self._message)
