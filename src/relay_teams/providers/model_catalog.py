# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from json import dumps, loads
from pathlib import Path
from time import time
from typing import Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field

from relay_teams.env.proxy_env import ProxyEnvConfig
from relay_teams.logger import get_logger
from relay_teams.media import MediaModality
from relay_teams.net.clients import create_sync_http_client
from relay_teams.providers.model_capabilities import resolve_model_capabilities
from relay_teams.providers.model_config import ModelCapabilities, ProviderType

DEFAULT_MODEL_CATALOG_SOURCE_URL = "https://models.dev/api.json"
DEFAULT_MODEL_CATALOG_TTL_SECONDS = 300
MODEL_CATALOG_CACHE_FILE_NAME = "model-catalog-cache.json"
_MODEL_CATALOG_TIMEOUT_SECONDS = 30.0
_MODEL_CATALOG_FETCH_ATTEMPTS = 2

LOGGER = get_logger(__name__)


class ModelCatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    family: Optional[str] = None
    release_date: Optional[str] = None
    last_updated: Optional[str] = None
    context_window: Optional[int] = Field(default=None, ge=1)
    output_limit: Optional[int] = Field(default=None, ge=1)
    attachment: bool = False
    reasoning: bool = False
    temperature: bool = False
    tool_call: bool = False
    status: Optional[str] = None
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    input_modalities: tuple[MediaModality, ...] = ()


class ModelCatalogProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    api: Optional[str] = None
    doc: Optional[str] = None
    env: tuple[str, ...] = ()
    models: tuple[ModelCatalogModel, ...] = ()


class ModelCatalogResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    source_url: str = Field(min_length=1)
    fetched_at: Optional[datetime] = None
    cache_age_seconds: Optional[int] = Field(default=None, ge=0)
    stale: bool = False
    providers: tuple[ModelCatalogProvider, ...] = ()
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class _ModelCatalogCacheEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str = Field(min_length=1)
    fetched_at: datetime
    providers: tuple[ModelCatalogProvider, ...]


class ModelCatalogService:
    def __init__(
        self,
        *,
        config_dir: Path,
        get_proxy_config: Callable[[], ProxyEnvConfig],
        source_url: str = DEFAULT_MODEL_CATALOG_SOURCE_URL,
        ttl_seconds: int = DEFAULT_MODEL_CATALOG_TTL_SECONDS,
    ) -> None:
        self._config_dir = config_dir
        self._get_proxy_config = get_proxy_config
        self._source_url = source_url
        self._ttl_seconds = ttl_seconds

    def get_catalog(self, *, refresh: bool = False) -> ModelCatalogResult:
        cached = self._load_cache()
        if cached is not None and not refresh:
            return self._result_from_cache(
                cached,
                ok=True,
                stale=self._is_stale(cached),
            )

        fetched = self._fetch_catalog()
        if fetched.ok:
            envelope = _ModelCatalogCacheEnvelope(
                source_url=self._source_url,
                fetched_at=fetched.fetched_at or datetime.now(timezone.utc),
                providers=fetched.providers,
            )
            try:
                self._write_cache(envelope)
            except OSError as exc:
                LOGGER.warning(
                    "Failed to write model catalog cache.",
                    extra={
                        "event": "providers.model_catalog.cache_write_failed",
                        "cache_path": str(self._cache_path()),
                        "error": str(exc),
                    },
                )
            return self._result_from_cache(envelope, ok=True, stale=False)

        if cached is None:
            return fetched
        return self._result_from_cache(
            cached,
            ok=False,
            stale=True,
            error_code=fetched.error_code,
            error_message=fetched.error_message,
        )

    def _fetch_catalog(self) -> ModelCatalogResult:
        last_error: Optional[ModelCatalogResult] = None
        for _attempt in range(_MODEL_CATALOG_FETCH_ATTEMPTS):
            result = self._fetch_catalog_once()
            if result.ok:
                return result
            last_error = result
            if result.error_code not in {"network_timeout", "network_error"}:
                return result
        if last_error is not None:
            return last_error
        return self._error_result(
            error_code="network_error",
            error_message="Failed to fetch model catalog.",
        )

    def _fetch_catalog_once(self) -> ModelCatalogResult:
        try:
            with create_sync_http_client(
                proxy_config=self._get_proxy_config(),
                timeout_seconds=_MODEL_CATALOG_TIMEOUT_SECONDS,
                connect_timeout_seconds=_MODEL_CATALOG_TIMEOUT_SECONDS,
                follow_redirects=True,
            ) as client:
                response = client.get(self._source_url)
        except httpx.TimeoutException as exc:
            return self._error_result(
                error_code="network_timeout",
                error_message=str(exc) or "Timed out fetching model catalog.",
            )
        except httpx.RequestError as exc:
            return self._error_result(
                error_code="network_error",
                error_message=str(exc) or "Failed to fetch model catalog.",
            )

        if response.status_code >= 400:
            return self._error_result(
                error_code="http_error",
                error_message=(
                    f"Model catalog source returned HTTP {response.status_code}."
                ),
            )

        try:
            payload: object = response.json()
        except ValueError:
            return self._error_result(
                error_code="invalid_response",
                error_message="Model catalog source returned invalid JSON.",
            )

        providers = _parse_catalog_payload(payload)
        if not providers:
            return self._error_result(
                error_code="invalid_response",
                error_message="Model catalog source returned no providers.",
            )
        return ModelCatalogResult(
            ok=True,
            source_url=self._source_url,
            fetched_at=datetime.now(timezone.utc),
            providers=providers,
        )

    def _load_cache(self) -> Optional[_ModelCatalogCacheEnvelope]:
        cache_path = self._cache_path()
        if not cache_path.exists():
            return None
        try:
            payload: object = loads(cache_path.read_text(encoding="utf-8"))
            return _ModelCatalogCacheEnvelope.model_validate(payload)
        except Exception as exc:
            LOGGER.warning(
                "Ignoring invalid model catalog cache.",
                extra={
                    "event": "providers.model_catalog.invalid_cache",
                    "cache_path": str(cache_path),
                    "error": str(exc),
                },
            )
            return None

    def _write_cache(self, envelope: _ModelCatalogCacheEnvelope) -> None:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path().write_text(
            dumps(envelope.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    def _cache_path(self) -> Path:
        return self._config_dir / MODEL_CATALOG_CACHE_FILE_NAME

    def _is_stale(self, envelope: _ModelCatalogCacheEnvelope) -> bool:
        fetched_at = envelope.fetched_at
        return time() - fetched_at.timestamp() >= self._ttl_seconds

    def _result_from_cache(
        self,
        envelope: _ModelCatalogCacheEnvelope,
        *,
        ok: bool,
        stale: bool,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> ModelCatalogResult:
        age_seconds = max(0, int(time() - envelope.fetched_at.timestamp()))
        return ModelCatalogResult(
            ok=ok,
            source_url=envelope.source_url,
            fetched_at=envelope.fetched_at,
            cache_age_seconds=age_seconds,
            stale=stale or age_seconds >= self._ttl_seconds,
            providers=envelope.providers,
            error_code=error_code,
            error_message=error_message,
        )

    def _error_result(
        self,
        *,
        error_code: str,
        error_message: str,
    ) -> ModelCatalogResult:
        return ModelCatalogResult(
            ok=False,
            source_url=self._source_url,
            error_code=error_code,
            error_message=error_message,
        )


def _parse_catalog_payload(payload: object) -> tuple[ModelCatalogProvider, ...]:
    if not isinstance(payload, Mapping):
        return ()
    providers: list[ModelCatalogProvider] = []
    for raw_provider_id, raw_provider_payload in payload.items():
        if not isinstance(raw_provider_id, str):
            continue
        provider_id = raw_provider_id.strip()
        if not provider_id or not isinstance(raw_provider_payload, Mapping):
            continue
        provider = _parse_provider(provider_id, raw_provider_payload)
        if provider is not None and provider.models:
            providers.append(provider)
    providers.sort(key=lambda item: (item.name.casefold(), item.id.casefold()))
    return tuple(providers)


def _parse_provider(
    provider_id: str,
    payload: Mapping[str, object],
) -> Optional[ModelCatalogProvider]:
    name = _string_field(payload.get("name")) or provider_id
    models_payload = payload.get("models")
    if not isinstance(models_payload, Mapping):
        return None
    models: list[ModelCatalogModel] = []
    for raw_model_id, raw_model_payload in models_payload.items():
        if not isinstance(raw_model_id, str) or not isinstance(
            raw_model_payload, Mapping
        ):
            continue
        model = _parse_model(provider_id, raw_model_id, raw_model_payload)
        if model is not None:
            models.append(model)
    models.sort(key=lambda item: (item.name.casefold(), item.id.casefold()))
    return ModelCatalogProvider(
        id=provider_id,
        name=name,
        api=_string_field(payload.get("api")),
        doc=_string_field(payload.get("doc")),
        env=_string_tuple(payload.get("env")),
        models=tuple(models),
    )


def _parse_model(
    provider_id: str,
    model_id: str,
    payload: Mapping[str, object],
) -> Optional[ModelCatalogModel]:
    normalized_model_id = model_id.strip()
    if not normalized_model_id:
        return None
    model_name = _string_field(payload.get("name")) or normalized_model_id
    context_window, output_limit = _extract_limits(payload.get("limit"))
    capabilities = resolve_model_capabilities(
        provider=ProviderType.OPENAI_COMPATIBLE,
        base_url="",
        model_name=normalized_model_id,
        metadata=_metadata_payload(provider_id=provider_id, payload=payload),
    )
    return ModelCatalogModel(
        id=normalized_model_id,
        name=model_name,
        family=_string_field(payload.get("family")),
        release_date=_string_field(payload.get("release_date")),
        last_updated=_string_field(payload.get("last_updated")),
        context_window=context_window,
        output_limit=output_limit,
        attachment=_bool_field(payload.get("attachment")),
        reasoning=_bool_field(payload.get("reasoning")),
        temperature=_bool_field(payload.get("temperature")),
        tool_call=_bool_field(payload.get("tool_call")),
        status=_string_field(payload.get("status")),
        capabilities=capabilities,
        input_modalities=capabilities.supported_input_modalities(),
    )


def _metadata_payload(
    *,
    provider_id: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    metadata: dict[str, object] = {"provider_id": provider_id}
    for key in ("modalities", "input_modalities", "capabilities"):
        value = payload.get(key)
        if _is_json_value(value):
            metadata[key] = value
    return metadata


def _extract_limits(value: object) -> tuple[Optional[int], Optional[int]]:
    if not isinstance(value, Mapping):
        return None, None
    return _positive_int(value.get("context")), _positive_int(value.get("output"))


def _string_field(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    entries: list[str] = []
    seen: set[str] = set()
    for item in value:
        normalized = _string_field(item)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        entries.append(normalized)
    return tuple(entries)


def _positive_int(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _bool_field(value: object) -> bool:
    return value is True


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item) for key, item in value.items()
        )
    return False
