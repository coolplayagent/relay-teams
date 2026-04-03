# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    computed_field,
    field_validator,
    model_validator,
)

DEFAULT_SEARXNG_INSTANCE_SEEDS = (
    "https://search.mdosch.de/",
    "https://search.seddens.net/",
    "https://search.wdpserver.com/",
)
DEFAULT_SEARXNG_INSTANCE_URL = DEFAULT_SEARXNG_INSTANCE_SEEDS[0]


class WebProvider(str, Enum):
    EXA = "exa"
    SEARXNG = "searxng"


class WebFallbackProvider(str, Enum):
    DISABLED = "disabled"
    SEARXNG = "searxng"


class WebConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: WebProvider = WebProvider.EXA
    exa_api_key: str | None = None
    fallback_provider: WebFallbackProvider | None = None
    searxng_instance_url: str | None = None

    @computed_field(return_type=tuple[str, ...])
    @property
    def searxng_instance_seeds(self) -> tuple[str, ...]:
        return DEFAULT_SEARXNG_INSTANCE_SEEDS

    @field_validator("provider")
    @classmethod
    def _validate_primary_provider(cls, value: WebProvider) -> WebProvider:
        if value != WebProvider.EXA:
            raise ValueError("Primary web provider must be exa")
        return value

    @field_validator("exa_api_key")
    @classmethod
    def _normalize_api_keys(cls, value: str | None) -> str | None:
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

    @model_validator(mode="after")
    def _apply_default_fallback_settings(self) -> WebConfig:
        if self.fallback_provider is None:
            self.fallback_provider = WebFallbackProvider.SEARXNG
        if self.searxng_instance_url is None:
            self.searxng_instance_url = DEFAULT_SEARXNG_INSTANCE_URL
        return self

    def get_api_key_for_provider(self, provider: WebProvider) -> str | None:
        if provider == WebProvider.EXA:
            return self.exa_api_key
        return None


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized
