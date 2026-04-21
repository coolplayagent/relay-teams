from __future__ import annotations

from collections.abc import Iterable, Mapping

from relay_teams.media import MediaModality
from relay_teams.providers.model_config import (
    ModelCapabilities,
    ModelModalityMatrix,
    ProviderType,
)

_IMAGE_MODALITY_MARKERS = (
    "gpt-4.1",
    "gpt-4.5",
    "gpt-4o",
    "gemini",
    "claude-3",
    "claude-sonnet-4",
    "claude-opus-4",
    "glm-4v",
    "glm-4.1v",
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5-vl",
    "qvq",
    "internvl",
    "minicpm-v",
    "llava",
    "pixtral",
    "kimi-vl",
    "vision",
    "multimodal",
    "omni",
)
_CAPABILITY_FIELD_NAMES = ("text", "image", "audio", "video", "pdf")


def resolve_model_capabilities(
    *,
    provider: ProviderType,
    base_url: str,
    model_name: str,
    metadata: object | None = None,
) -> ModelCapabilities:
    defaults = ModelCapabilities(
        input=ModelModalityMatrix(text=True),
        output=ModelModalityMatrix(text=True),
    )
    explicit, has_signal = extract_model_capabilities_from_payload(metadata)
    heuristic = infer_model_capabilities(
        provider=provider,
        base_url=base_url,
        model_name=model_name,
    )
    if has_signal:
        return _merge_capabilities(_merge_capabilities(explicit, heuristic), defaults)
    return _merge_capabilities(heuristic, defaults)


def resolve_model_input_modalities(
    *,
    provider: ProviderType,
    base_url: str,
    model_name: str,
    metadata: object | None = None,
) -> tuple[MediaModality, ...]:
    return resolve_model_capabilities(
        provider=provider,
        base_url=base_url,
        model_name=model_name,
        metadata=metadata,
    ).supported_input_modalities()


def extract_model_capabilities_from_payload(
    payload: object | None,
) -> tuple[ModelCapabilities, bool]:
    if not isinstance(payload, Mapping):
        return ModelCapabilities(), False
    return _extract_capabilities_from_mapping(payload)


def extract_input_modalities_from_payload(
    payload: object | None,
) -> tuple[MediaModality, ...]:
    capabilities, has_signal = extract_model_capabilities_from_payload(payload)
    if not has_signal:
        return ()
    return capabilities.supported_input_modalities()


def infer_model_capabilities(
    *,
    provider: ProviderType,
    base_url: str,
    model_name: str,
) -> ModelCapabilities:
    normalized_model = model_name.strip().lower()
    normalized_base_url = base_url.strip().lower()
    if provider == ProviderType.ECHO:
        return ModelCapabilities(
            input=ModelModalityMatrix(
                text=True,
                image=True,
                audio=True,
                video=True,
            ),
            output=ModelModalityMatrix(text=True),
        )
    if not normalized_model:
        return ModelCapabilities()
    if any(marker in normalized_model for marker in _IMAGE_MODALITY_MARKERS):
        return ModelCapabilities(
            input=ModelModalityMatrix(
                text=True,
                image=True,
            ),
            output=ModelModalityMatrix(text=True),
        )
    if "vision" in normalized_model or normalized_model.endswith("-vl"):
        return ModelCapabilities(
            input=ModelModalityMatrix(
                text=True,
                image=True,
            ),
            output=ModelModalityMatrix(text=True),
        )
    if "gemini" in normalized_base_url and normalized_model:
        return ModelCapabilities(
            input=ModelModalityMatrix(
                text=True,
                image=True,
            ),
            output=ModelModalityMatrix(text=True),
        )
    return ModelCapabilities()


def _extract_capabilities_from_mapping(
    payload: Mapping[str, object],
) -> tuple[ModelCapabilities, bool]:
    current = ModelCapabilities()
    found_signal = False

    direct_input, input_found = _extract_modalities_from_named_section(
        payload,
        field_names=(
            "input_modalities",
            "inputModalities",
            "supported_input_modalities",
            "supportedInputModalities",
        ),
    )
    if input_found:
        current = current.model_copy(update={"input": direct_input})
        found_signal = True

    direct_modalities, modalities_found = _extract_modalities_block(
        payload.get("modalities")
    )
    if modalities_found:
        current = _merge_capabilities(direct_modalities, current)
        found_signal = True

    direct_capabilities, direct_capabilities_found = _extract_io_capabilities(payload)
    if direct_capabilities_found:
        current = _merge_capabilities(direct_capabilities, current)
        found_signal = True

    for nested_key in ("capabilities", "capability", "metadata", "limits"):
        nested = payload.get(nested_key)
        if not isinstance(nested, Mapping):
            continue
        nested_capabilities, nested_found = _extract_capabilities_from_mapping(nested)
        if not nested_found:
            continue
        current = _merge_capabilities(current, nested_capabilities)
        found_signal = True

    return current, found_signal


def _extract_modalities_block(value: object) -> tuple[ModelCapabilities, bool]:
    if isinstance(value, Mapping):
        input_matrix = _extract_modalities_matrix(
            value.get("input") or value.get("inputs")
        )
        output_matrix = _extract_modalities_matrix(
            value.get("output") or value.get("outputs")
        )
        found = _matrix_has_signal(input_matrix) or _matrix_has_signal(output_matrix)
        return (
            ModelCapabilities(
                input=input_matrix,
                output=output_matrix,
            ),
            found,
        )
    matrix = _extract_modalities_matrix(value)
    return ModelCapabilities(input=matrix), _matrix_has_signal(matrix)


def _extract_io_capabilities(
    payload: Mapping[str, object],
) -> tuple[ModelCapabilities, bool]:
    input_matrix = _extract_boolean_matrix(payload.get("input"))
    output_matrix = _extract_boolean_matrix(payload.get("output"))
    current = ModelCapabilities(
        input=input_matrix,
        output=output_matrix,
    )
    found = _matrix_has_signal(input_matrix) or _matrix_has_signal(output_matrix)

    explicit_boolean_matrix, explicit_boolean_found = _extract_boolean_capabilities(
        payload
    )
    if explicit_boolean_found:
        current = _merge_capabilities(explicit_boolean_matrix, current)
        found = True
    return current, found


def _extract_modalities_from_named_section(
    payload: Mapping[str, object],
    *,
    field_names: tuple[str, ...],
) -> tuple[ModelModalityMatrix, bool]:
    for field_name in field_names:
        if field_name not in payload:
            continue
        return _extract_media_only_matrix(payload.get(field_name)), True
    return ModelModalityMatrix(), False


def _extract_media_only_matrix(value: object) -> ModelModalityMatrix:
    tokens = _collect_modality_tokens(value)
    if not tokens:
        return ModelModalityMatrix(
            image=False,
            audio=False,
            video=False,
        )
    return ModelModalityMatrix(
        image="image" in tokens,
        audio="audio" in tokens,
        video="video" in tokens,
    )


def _extract_modalities_matrix(value: object) -> ModelModalityMatrix:
    tokens = _collect_modality_tokens(value)
    if not tokens:
        return ModelModalityMatrix()
    return ModelModalityMatrix(
        text="text" in tokens,
        image="image" in tokens,
        audio="audio" in tokens,
        video="video" in tokens,
        pdf="pdf" in tokens,
    )


def _extract_boolean_matrix(value: object) -> ModelModalityMatrix:
    if not isinstance(value, Mapping):
        return ModelModalityMatrix()
    recognized: dict[str, bool | None] = {}
    for field_name in _CAPABILITY_FIELD_NAMES:
        raw_value = value.get(field_name)
        if isinstance(raw_value, bool):
            recognized[field_name] = raw_value
    return ModelModalityMatrix(**recognized)


def _extract_boolean_capabilities(
    payload: Mapping[str, object],
) -> tuple[ModelCapabilities, bool]:
    input_payload: dict[str, bool | None] = {}
    for field_name, aliases in _boolean_modality_aliases().items():
        match = _extract_boolean_alias_value(payload, aliases)
        if match is None:
            continue
        input_payload[field_name] = match
    if not input_payload:
        return ModelCapabilities(), False
    return ModelCapabilities(input=ModelModalityMatrix(**input_payload)), True


def _extract_boolean_alias_value(
    payload: Mapping[str, object],
    aliases: tuple[str, ...],
) -> bool | None:
    for alias in aliases:
        if alias not in payload:
            continue
        raw_value = payload.get(alias)
        if isinstance(raw_value, bool):
            return raw_value
    return None


def _boolean_modality_aliases() -> dict[str, tuple[str, ...]]:
    return {
        "image": (
            "vision",
            "image",
            "image_input",
            "imageInput",
            "supports_image_input",
            "supportsImageInput",
        ),
        "audio": (
            "audio",
            "audio_input",
            "audioInput",
            "supports_audio_input",
            "supportsAudioInput",
        ),
        "video": (
            "video",
            "video_input",
            "videoInput",
            "supports_video_input",
            "supportsVideoInput",
        ),
        "pdf": (
            "pdf",
            "pdf_input",
            "pdfInput",
            "supports_pdf_input",
            "supportsPdfInput",
        ),
    }


def _collect_modality_tokens(value: object) -> set[str]:
    tokens: set[str] = set()
    for token in _iter_modality_tokens(value):
        tokens.add(token)
    return tokens


def _iter_modality_tokens(value: object) -> Iterable[str]:
    if isinstance(value, str):
        parsed = _parse_modality_token(value)
        if parsed is not None:
            yield parsed
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_modality_tokens(item)
        return
    if isinstance(value, Mapping):
        for nested in value.values():
            yield from _iter_modality_tokens(nested)


def _parse_modality_token(token: str) -> str | None:
    normalized = token.strip().lower()
    if normalized in {"text", "texts"}:
        return "text"
    if normalized in {"image", "images", "vision"}:
        return "image"
    if normalized in {"audio", "speech"}:
        return "audio"
    if normalized in {"video", "videos"}:
        return "video"
    if normalized in {"pdf", "document"}:
        return "pdf"
    return None


def _matrix_has_signal(matrix: ModelModalityMatrix) -> bool:
    return any(
        value is not None
        for value in (
            matrix.text,
            matrix.image,
            matrix.audio,
            matrix.video,
            matrix.pdf,
        )
    )


def _merge_capabilities(
    primary: ModelCapabilities,
    fallback: ModelCapabilities,
) -> ModelCapabilities:
    return ModelCapabilities(
        input=_merge_matrix(primary.input, fallback.input),
        output=_merge_matrix(primary.output, fallback.output),
    )


def _merge_matrix(
    primary: ModelModalityMatrix,
    fallback: ModelModalityMatrix,
) -> ModelModalityMatrix:
    return ModelModalityMatrix(
        text=primary.text if primary.text is not None else fallback.text,
        image=primary.image if primary.image is not None else fallback.image,
        audio=primary.audio if primary.audio is not None else fallback.audio,
        video=primary.video if primary.video is not None else fallback.video,
        pdf=primary.pdf if primary.pdf is not None else fallback.pdf,
    )
