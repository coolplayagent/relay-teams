from __future__ import annotations

from collections.abc import Mapping, Sequence

from relay_teams.providers.model_config import (
    ModelCapabilities,
    ModelInputCapabilities,
    ModelOutputCapabilities,
)


_INPUT_MODALITY_KEYS = (
    "input_modalities",
    "inputModalities",
    "supported_input_modalities",
    "supportedInputModalities",
)
_OUTPUT_MODALITY_KEYS = (
    "output_modalities",
    "outputModalities",
    "supported_output_modalities",
    "supportedOutputModalities",
)
_NESTED_CAPABILITY_KEYS = ("capabilities", "capability", "metadata")


def extract_model_capabilities_from_payload(
    payload: object,
) -> ModelCapabilities | None:
    if not isinstance(payload, Mapping):
        return None

    input_capabilities = ModelInputCapabilities()
    output_capabilities = ModelOutputCapabilities()

    _apply_modalities_from_payload(
        source=payload,
        keys=_INPUT_MODALITY_KEYS,
        target=input_capabilities,
    )
    _apply_modalities_from_payload(
        source=payload,
        keys=_OUTPUT_MODALITY_KEYS,
        target=output_capabilities,
    )

    modalities = payload.get("modalities")
    if isinstance(modalities, Mapping):
        _apply_modality_mapping(modalities.get("input"), input_capabilities)
        _apply_modality_mapping(modalities.get("output"), output_capabilities)

    _apply_named_capability_mapping(payload.get("input"), input_capabilities)
    _apply_named_capability_mapping(payload.get("output"), output_capabilities)

    for nested_key in _NESTED_CAPABILITY_KEYS:
        nested = payload.get(nested_key)
        if not isinstance(nested, Mapping):
            continue
        nested_capabilities = extract_model_capabilities_from_payload(nested)
        if nested_capabilities is None:
            continue
        input_capabilities = merge_input_capabilities(
            input_capabilities,
            nested_capabilities.input,
        )
        output_capabilities = merge_output_capabilities(
            output_capabilities,
            nested_capabilities.output,
        )

    if (
        input_capabilities.text is None
        and input_capabilities.image is None
        and output_capabilities.text is None
    ):
        return None

    return ModelCapabilities(input=input_capabilities, output=output_capabilities)


def merge_model_capabilities(
    primary: ModelCapabilities | None,
    fallback: ModelCapabilities | None,
) -> ModelCapabilities | None:
    if primary is None:
        return fallback
    if fallback is None:
        return primary
    return ModelCapabilities(
        input=merge_input_capabilities(primary.input, fallback.input),
        output=merge_output_capabilities(primary.output, fallback.output),
    )


def merge_input_capabilities(
    primary: ModelInputCapabilities,
    fallback: ModelInputCapabilities,
) -> ModelInputCapabilities:
    return ModelInputCapabilities(
        text=primary.text if primary.text is not None else fallback.text,
        image=primary.image if primary.image is not None else fallback.image,
    )


def merge_output_capabilities(
    primary: ModelOutputCapabilities,
    fallback: ModelOutputCapabilities,
) -> ModelOutputCapabilities:
    return ModelOutputCapabilities(
        text=primary.text if primary.text is not None else fallback.text,
    )


def _apply_modalities_from_payload(
    *,
    source: Mapping[object, object],
    keys: tuple[str, ...],
    target: ModelInputCapabilities | ModelOutputCapabilities,
) -> None:
    for key in keys:
        _apply_modality_mapping(source.get(key), target)


def _apply_modality_mapping(
    value: object,
    target: ModelInputCapabilities | ModelOutputCapabilities,
) -> None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        modalities = {
            str(entry).strip().lower()
            for entry in value
            if isinstance(entry, str) and entry.strip()
        }
        if "text" in modalities:
            target.text = True
        if "image" in modalities and isinstance(target, ModelInputCapabilities):
            target.image = True
        if (
            modalities
            and "image" not in modalities
            and isinstance(target, ModelInputCapabilities)
        ):
            target.image = False
        return
    _apply_named_capability_mapping(value, target)


def _apply_named_capability_mapping(
    value: object,
    target: ModelInputCapabilities | ModelOutputCapabilities,
) -> None:
    if not isinstance(value, Mapping):
        return
    text_value = _coerce_optional_bool(value.get("text"))
    if text_value is not None:
        target.text = text_value
    if isinstance(target, ModelInputCapabilities):
        image_value = _coerce_optional_bool(value.get("image"))
        if image_value is not None:
            target.image = image_value


def _coerce_optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None
