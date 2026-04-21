from __future__ import annotations

import json
from hashlib import sha256
from pathlib import PurePosixPath
from urllib.parse import urlparse

from pydantic import JsonValue
from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    ImageUrl,
    UserContent,
    VideoUrl,
)

from relay_teams.media.models import MediaModality

UserPromptContent = str | tuple[UserContent, ...]


def merge_user_prompt_content(
    content: UserPromptContent,
    appendix: str,
) -> UserPromptContent:
    suffix = str(appendix or "").strip()
    if not suffix:
        return content
    if isinstance(content, str):
        base = content.strip()
        if not base:
            return suffix
        return f"{base}\n\n{suffix}"
    return (*content, suffix)


def user_prompt_content_to_text(content: object) -> str:
    normalized = normalize_user_prompt_content(content)
    fragments = _content_fragments(normalized)
    return "\n\n".join(fragment for fragment in fragments if fragment).strip()


def user_prompt_content_key(content: object) -> str:
    normalized = normalize_user_prompt_content(content)
    return json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def normalize_user_prompt_content(content: object) -> JsonValue:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, (tuple, list)):
        normalized_items = [_normalize_user_content_item(item) for item in content]
        return [item for item in normalized_items if item is not None]
    return _normalize_user_content_item(content)


def _normalize_user_content_item(item: object) -> JsonValue | None:
    if isinstance(item, str):
        text = item.strip()
        return text or None

    if isinstance(item, ImageUrl):
        return _normalize_url_item(
            modality=MediaModality.IMAGE,
            url=str(item.url),
            media_type=_user_content_media_type(item),
        )
    if isinstance(item, AudioUrl):
        return _normalize_url_item(
            modality=MediaModality.AUDIO,
            url=str(item.url),
            media_type=_user_content_media_type(item),
        )
    if isinstance(item, VideoUrl):
        return _normalize_url_item(
            modality=MediaModality.VIDEO,
            url=str(item.url),
            media_type=_user_content_media_type(item),
        )
    if isinstance(item, BinaryContent):
        media_type = _user_content_media_type(item)
        modality = _modality_from_media_type(media_type)
        payload = bytes(item.data)
        return {
            "kind": "binary",
            "modality": modality.value,
            "media_type": media_type,
            "size_bytes": len(payload),
            "sha256": sha256(payload).hexdigest(),
        }

    if not isinstance(item, dict):
        return None

    kind = str(item.get("kind") or "").strip().lower()
    if kind in {"image-url", "audio-url", "video-url"}:
        media_type = str(item.get("media_type") or item.get("mediaType") or "").strip()
        modality = (
            MediaModality.IMAGE
            if kind == "image-url"
            else MediaModality.AUDIO
            if kind == "audio-url"
            else MediaModality.VIDEO
        )
        return _normalize_url_item(
            modality=modality,
            url=str(item.get("url") or "").strip(),
            media_type=media_type,
        )
    if kind == "binary":
        media_type = str(item.get("media_type") or item.get("mediaType") or "").strip()
        raw_data = item.get("data")
        if not isinstance(raw_data, str) or not media_type:
            return None
        return {
            "kind": "binary",
            "modality": _modality_from_media_type(media_type).value,
            "media_type": media_type,
            "size_bytes": len(raw_data),
            "sha256": sha256(raw_data.encode("utf-8")).hexdigest(),
        }
    return None


def _normalize_url_item(
    *,
    modality: MediaModality,
    url: str,
    media_type: str,
) -> JsonValue | None:
    normalized_url = url.strip()
    if not normalized_url:
        return None
    return {
        "kind": f"{modality.value}-url",
        "modality": modality.value,
        "url": normalized_url,
        "media_type": media_type.strip(),
        "label": _url_label(normalized_url, fallback=modality.value),
    }


def _content_fragments(content: JsonValue) -> list[str]:
    if isinstance(content, str):
        return [content] if content else []
    if isinstance(content, list):
        fragments: list[str] = []
        for item in content:
            fragments.extend(_content_fragments(item))
        return fragments
    if not isinstance(content, dict):
        return []
    kind = str(content.get("kind") or "").strip().lower()
    modality = str(content.get("modality") or "").strip().lower()
    if kind.endswith("-url") or kind == "binary":
        label = str(content.get("label") or modality or kind or "media").strip()
        rendered_kind = modality or kind.replace("-url", "")
        return [f"[{rendered_kind}: {label}]"]
    return []


def _url_label(url: str, *, fallback: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or url
    name = PurePosixPath(path).name.strip()
    return name or fallback


def _user_content_media_type(item: object) -> str:
    for attribute in ("media_type", "_media_type"):
        value = getattr(item, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _modality_from_media_type(media_type: str) -> MediaModality:
    normalized = str(media_type or "").strip().lower()
    if normalized.startswith("audio/"):
        return MediaModality.AUDIO
    if normalized.startswith("video/"):
        return MediaModality.VIDEO
    return MediaModality.IMAGE
