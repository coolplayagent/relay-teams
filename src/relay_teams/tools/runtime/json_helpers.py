# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
import json

from pydantic import BaseModel, JsonValue

from relay_teams.media import ContentPart, TextContentPart, UserPromptContent
from relay_teams.tools.runtime.context import ToolContext


def normalize_json_object(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, JsonValue] = {}
    for key, item in value.items():
        normalized[str(key)] = normalize_json_value(item)
    return normalized


# noinspection PyTypeHints
def _tool_return_content(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_content_parts: tuple[ContentPart, ...],
) -> UserPromptContent:
    if not tool_content_parts:
        return ""
    if all(isinstance(part, TextContentPart) for part in tool_content_parts):
        return "\n\n".join(
            part.text
            for part in tool_content_parts
            if isinstance(part, TextContentPart)
        ).strip()
    media_asset_service = ctx.deps.media_asset_service
    if media_asset_service is None:
        raise ValueError(
            f"Tool {tool_name} returned media content without media asset support."
        )
    provider_content = media_asset_service.to_provider_user_prompt_content(
        parts=tool_content_parts
    )
    hydrated_content = media_asset_service.hydrate_user_prompt_content(
        content=provider_content
    )
    if isinstance(hydrated_content, str):
        return hydrated_content
    return tuple(hydrated_content)


def safe_json(value: object) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    if len(text) > 500:
        return text[:500] + "...(truncated)"
    return text


def normalize_json_value(value: object) -> JsonValue:
    if isinstance(value, Enum):
        return normalize_json_value(value.value)
    if isinstance(value, BaseModel):
        return normalize_json_value(value.model_dump(mode="json"))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [normalize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [normalize_json_value(item) for item in value]
    if isinstance(value, dict):
        return normalize_json_object(value)
    return str(value)
