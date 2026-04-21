from __future__ import annotations

from relay_teams.providers.model_capabilities import resolve_model_capabilities
from relay_teams.providers.model_config import ProviderType


def test_resolve_model_capabilities_prefers_explicit_modalities_over_heuristics() -> (
    None
):
    capabilities = resolve_model_capabilities(
        provider=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
        metadata={
            "modalities": {
                "input": ["text"],
                "output": ["text"],
            }
        },
    )

    assert capabilities.input.text is True
    assert capabilities.input.image is False
    assert capabilities.output.text is True


def test_resolve_model_capabilities_marks_unknown_when_no_signal_exists() -> None:
    capabilities = resolve_model_capabilities(
        provider=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://example.test/v1",
        model_name="custom-chat-model",
    )

    assert capabilities.input.text is True
    assert capabilities.input.image is None
    assert capabilities.output.text is True


def test_resolve_model_capabilities_projects_legacy_input_modalities() -> None:
    capabilities = resolve_model_capabilities(
        provider=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://example.test/v1",
        model_name="custom-chat-model",
        metadata={"input_modalities": []},
    )

    assert capabilities.input.text is True
    assert capabilities.input.image is False
    assert capabilities.input.audio is False
    assert capabilities.input.video is False


def test_resolve_model_capabilities_infers_explicit_multimodal_families() -> None:
    capabilities = resolve_model_capabilities(
        provider=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
    )

    assert capabilities.input.text is True
    assert capabilities.input.image is True


def test_resolve_model_capabilities_preserves_explicit_image_opt_out() -> None:
    capabilities = resolve_model_capabilities(
        provider=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
        metadata={
            "capabilities": {
                "input": {
                    "text": True,
                    "image": False,
                },
                "output": {
                    "text": True,
                },
            }
        },
    )

    assert capabilities.input.text is True
    assert capabilities.input.image is False
    assert capabilities.output.text is True


def test_resolve_model_capabilities_uses_heuristics_when_image_override_is_null() -> (
    None
):
    capabilities = resolve_model_capabilities(
        provider=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
        metadata={
            "capabilities": {
                "input": {
                    "text": True,
                    "image": None,
                },
                "output": {
                    "text": True,
                },
            }
        },
    )

    assert capabilities.input.text is True
    assert capabilities.input.image is True
    assert capabilities.output.text is True
