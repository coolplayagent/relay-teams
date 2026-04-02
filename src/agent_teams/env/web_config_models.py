# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

import httpx
from pydantic import BaseModel, ConfigDict, field_validator


class WebProvider(str, Enum):
    EXA = "exa"
    SEARXNG = "searxng"


class WebFallbackProvider(str, Enum):
    SEARXNG = "searxng"


class WebConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: WebProvider = WebProvider.EXA
    api_key: str | None = None
    fallback_provider: WebFallbackProvider | None = None
    searxng_instance_url: str | None = None

    @field_validator("api_key")
    @classmethod
    def _normalize_api_key(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("searxng_instance_url")
    @classmethod
    def _normalize_searxng_instance_url(cls, value: str | None) -> str | None:
        normalized = _normalize_optional_text(value)
        if normalized is None:
            return None
        parsed = httpx.URL(normalized)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("SearXNG instance URL must use http or https")
        if parsed.host is None:
            raise ValueError("SearXNG instance URL must include a hostname")
        normalized_path = parsed.path or "/"
        sanitized = parsed.copy_with(
            path=normalized_path,
            query=None,
            fragment=None,
            username=None,
            password=None,
        )
        return str(sanitized)


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized
