# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.agents.execution.prompt_modalities import (
    format_modality_list,
    request_input_modalities,
    validate_input_modalities_capabilities,
)
from relay_teams.media import MediaModality, MediaRefContentPart, TextContentPart
from relay_teams.providers.model_config import (
    ModelCapabilities,
    ModelEndpointConfig,
    ModelModalityMatrix,
)


def test_format_modality_list_formats_human_readable_lists() -> None:
    assert format_modality_list(()) == "media"
    assert format_modality_list(("Image",)) == "image"
    assert format_modality_list(("image", "audio")) == "image and audio"
    assert format_modality_list(("image", "audio", "video")) == (
        "image, audio, and video"
    )


def test_request_input_modalities_skips_text_parts() -> None:
    assert request_input_modalities(
        (
            TextContentPart(text="describe"),
            MediaRefContentPart(
                asset_id="asset-1",
                session_id="session-1",
                modality=MediaModality.IMAGE,
                mime_type="image/png",
                url="/api/sessions/session-1/media/asset-1/file",
            ),
        )
    ) == (MediaModality.IMAGE,)


def test_validate_input_modalities_capabilities_rejects_unsupported_modalities() -> (
    None
):
    config = ModelEndpointConfig(
        model="text-only",
        base_url="https://example.test/v1",
        api_key="secret",
        capabilities=ModelCapabilities(
            input=ModelModalityMatrix(text=True, image=False, audio=None),
            output=ModelModalityMatrix(text=True),
        ),
    )

    with pytest.raises(ValueError, match="does not support image input"):
        validate_input_modalities_capabilities(
            config=config,
            modalities=(MediaModality.IMAGE,),
        )
    with pytest.raises(ValueError, match="support for audio input is unknown"):
        validate_input_modalities_capabilities(
            config=config,
            modalities=(MediaModality.AUDIO,),
        )
