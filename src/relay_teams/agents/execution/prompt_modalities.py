# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Sequence

from relay_teams.media import (
    InlineMediaContentPart,
    MediaModality,
    MediaRefContentPart,
    TextContentPart,
    UserPromptContent,
    normalize_user_prompt_content,
)
from relay_teams.providers.model_config import ModelEndpointConfig


def format_modality_list(modalities: Sequence[str]) -> str:
    normalized = [str(modality or "").strip().lower() for modality in modalities]
    items = [item for item in normalized if item]
    if not items:
        return "media"
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def request_input_modalities(
    parts: tuple[
        TextContentPart | MediaRefContentPart | InlineMediaContentPart,
        ...,
    ],
) -> tuple[MediaModality, ...]:
    modalities: list[MediaModality] = []
    for part in parts:
        if isinstance(part, TextContentPart):
            continue
        modalities.append(part.modality)
    return tuple(modalities)


# noinspection PyTypeHints
def user_prompt_content_modalities(
    content: UserPromptContent,
) -> tuple[MediaModality, ...]:
    modalities: list[MediaModality] = []
    collect_prompt_content_modalities(
        normalize_user_prompt_content(content), modalities
    )
    return tuple(modalities)


def collect_prompt_content_modalities(
    content: object,
    modalities: list[MediaModality],
) -> None:
    if isinstance(content, list):
        for item in content:
            collect_prompt_content_modalities(item, modalities)
        return
    if not isinstance(content, dict):
        return
    raw_modality = str(content.get("modality") or "").strip().lower()
    if not raw_modality:
        return
    try:
        modality = MediaModality(raw_modality)
    except ValueError:
        return
    modalities.append(modality)


# noinspection PyTypeHints
def input_modality_support(
    *,
    config: ModelEndpointConfig,
    modality: MediaModality,
) -> bool | None:
    input_capabilities = config.capabilities.input
    if modality == MediaModality.IMAGE:
        return input_capabilities.image
    if modality == MediaModality.AUDIO:
        return input_capabilities.audio
    return input_capabilities.video


# noinspection PyTypeHints
def validate_input_modalities_capabilities(
    *,
    config: ModelEndpointConfig,
    modalities: Sequence[MediaModality],
) -> None:
    if not modalities:
        return
    unsupported: list[str] = []
    unknown: list[str] = []
    for modality in modalities:
        support = input_modality_support(config=config, modality=modality)
        if support is True:
            continue
        if support is False:
            if modality.value not in unsupported:
                unsupported.append(modality.value)
            continue
        if modality.value not in unknown:
            unknown.append(modality.value)
    if unsupported:
        raise ValueError(
            "This model does not support "
            f"{format_modality_list(unsupported)} input. "
            "Remove the attachment or switch to a compatible model."
        )
    if unknown:
        raise ValueError(
            "This model's support for "
            f"{format_modality_list(unknown)} input is unknown. "
            "Remove the attachment or switch to a model with explicit multimodal support."
        )
