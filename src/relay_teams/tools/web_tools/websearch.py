# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from urllib.parse import urlencode, urlparse
from typing import Literal

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)
from pydantic_ai import Agent

from relay_teams.env.web_config_models import (
    DEFAULT_SEARXNG_INSTANCE_SEEDS,
    DEFAULT_SEARXNG_INSTANCE_URL,
    WebConfig,
    WebFallbackProvider,
    WebProvider,
)
from relay_teams.net.clients import create_async_http_client
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolExecutionError,
    ToolResultProjection,
    execute_tool,
)
from relay_teams.tools.web_tools.common import load_runtime_web_config

EXA_BASE_URL = "https://mcp.exa.ai"
EXA_PATH = "/mcp"
EXA_TOOL_NAME = "web_search_advanced_exa"
SEARXNG_SEARCH_PATH = "/search"
SEARXNG_SEARCH_TOOL_NAME = "search"
SEARXNG_PUBLIC_INSTANCES_URL = "https://searx.space/data/instances.json"
SEARXNG_INSTANCE_CACHE_TTL_SECONDS = 24 * 60 * 60
SEARXNG_INSTANCE_COOLDOWN_SECONDS = 30 * 60
SEARXNG_PUBLIC_INSTANCE_LIMIT = 20
DEFAULT_NUM_RESULTS = 8
DEFAULT_EXA_SEARCH_TYPE = "auto"
DEFAULT_TIMEOUT_SECONDS = 25.0
DEFAULT_TEXT_MAX_CHARACTERS = 300
DEFAULT_HIGHLIGHTS_PER_URL = 3
DEFAULT_HIGHLIGHT_SENTENCES = 2
DESCRIPTION = load_tool_description(__file__)

_SEARXNG_INSTANCE_CACHE: SearxngInstanceCache | None = None
_SEARXNG_INSTANCE_COOLDOWNS: dict[str, float] = {}


class WebSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    num_results: int = Field(default=DEFAULT_NUM_RESULTS, ge=1)
    allowed_domains: tuple[str, ...] | None = None
    blocked_domains: tuple[str, ...] | None = None

    @field_validator("query")
    @classmethod
    def _normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Missing query")
        return normalized

    @field_validator("allowed_domains", "blocked_domains", mode="before")
    @classmethod
    def _normalize_domains(
        cls,
        value: object,
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, (list, tuple, set, frozenset)):
            candidates = list(value)
        else:
            raise ValueError("Domain filters must be provided as an array of strings")

        normalized: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, str):
                raise ValueError("Domain filters must contain only strings")
            domain = _normalize_domain(candidate)
            if domain in seen:
                continue
            normalized.append(domain)
            seen.add(domain)
        return tuple(normalized) or None

    @model_validator(mode="after")
    def _validate_domain_filters(self) -> WebSearchRequest:
        if self.allowed_domains and self.blocked_domains:
            raise ValueError(
                "Cannot specify both allowed_domains and blocked_domains in the same request"
            )
        return self


class WebSearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    published_at: str | None = None
    author: str | None = None
    highlights: tuple[str, ...] = ()
    text: str | None = None
    summary: str | None = None


class WebSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    provider: WebProvider
    hits: tuple[WebSearchHit, ...] = ()
    duration_ms: int = Field(ge=0)


class SearchExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: WebProvider
    endpoint_host: str
    upstream_tool: str
    hits: tuple[WebSearchHit, ...] = ()
    upstream_search_time: float | None = None
    internal_data: dict[str, JsonValue] = Field(default_factory=dict)


class ExaSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    numResults: int
    type: Literal["auto", "fast", "neural"]
    includeDomains: tuple[str, ...] | None = None
    excludeDomains: tuple[str, ...] | None = None
    textMaxCharacters: int = DEFAULT_TEXT_MAX_CHARACTERS
    enableHighlights: bool = True
    highlightsNumSentences: int = DEFAULT_HIGHLIGHT_SENTENCES
    highlightsPerUrl: int = DEFAULT_HIGHLIGHTS_PER_URL
    highlightsQuery: str


class ExaSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jsonrpc: str = "2.0"
    id: int = 1
    method: str = "tools/call"
    params: dict[str, JsonValue]


class PreparedSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: WebProvider
    endpoint: str
    endpoint_host: str
    payload: dict[str, JsonValue]
    upstream_tool: str


class ExaSearchHitPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    title: str | None = None
    url: str | None = None
    published_at: str | None = Field(default=None, alias="publishedDate")
    author: str | None = None
    highlights: tuple[str, ...] = ()
    text: str | None = None
    summary: str | None = None


class ExaSearchPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: tuple[ExaSearchHitPayload, ...] = ()
    searchTime: float | None = None


class ExaRpcErrorPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: int | None = None
    message: str | None = None


class ExaRpcResponsePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    jsonrpc: str | None = None
    error: ExaRpcErrorPayload | None = None


class ExtractedSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hits: tuple[WebSearchHit, ...] = ()
    upstream_search_time: float | None = None


class SearxngSearchResultPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    title: str | None = None
    url: str | None = None
    content: str | None = None
    published_at: str | None = Field(default=None, alias="publishedDate")
    author: str | None = None


class SearxngSearchResponsePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: tuple[SearxngSearchResultPayload, ...] = ()


class SearxngCatalogHttpPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status_code: int | None = None


class SearxngCatalogTimedValue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: float | None = None


class SearxngCatalogTimingEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    success_percentage: float | None = None
    all: SearxngCatalogTimedValue | None = None


class SearxngCatalogTimingPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    initial: SearxngCatalogTimingEntry | None = None


class SearxngCatalogInstancePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    analytics: bool = False
    generator: str | None = None
    main: bool = False
    http: SearxngCatalogHttpPayload | None = None
    timing: SearxngCatalogTimingPayload | None = None


class SearxngCatalogPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    instances: dict[str, SearxngCatalogInstancePayload] = Field(default_factory=dict)


class SearxngInstanceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str = Field(min_length=1)
    endpoint_host: str = Field(min_length=1)
    source: str = Field(min_length=1)


class SearxngInstanceCache(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fetched_at_monotonic: float
    candidates: tuple[SearxngInstanceCandidate, ...]


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def websearch(
        ctx: ToolContext,
        query: str,
        num_results: int | None = None,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
    ) -> dict[str, JsonValue]:
        """Search the web and return structured search hits."""

        async def _action() -> ToolResultProjection:
            config = load_runtime_web_config()
            started = time.perf_counter()
            request = WebSearchRequest(
                query=query,
                num_results=num_results or DEFAULT_NUM_RESULTS,
                allowed_domains=(
                    tuple(allowed_domains) if allowed_domains is not None else None
                ),
                blocked_domains=(
                    tuple(blocked_domains) if blocked_domains is not None else None
                ),
            )
            async with create_async_http_client(
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
                follow_redirects=True,
            ) as client:
                result = await execute_search(
                    client=client,
                    config=config,
                    request=request,
                )

            duration_ms = int((time.perf_counter() - started) * 1000)
            return build_search_result_projection(
                query=request.query,
                result=result,
                duration_ms=duration_ms,
            )

        return await execute_tool(
            ctx,
            tool_name="websearch",
            args_summary={
                "query": query,
                "num_results": num_results,
                "allowed_domains": _normalize_domain_summary(allowed_domains),
                "blocked_domains": _normalize_domain_summary(blocked_domains),
            },
            action=_action,
        )


async def execute_search(
    *,
    client: httpx.AsyncClient,
    config: WebConfig,
    request: WebSearchRequest,
) -> SearchExecutionResult:
    try:
        return await execute_provider_search(
            client=client,
            provider=config.provider,
            config=config,
            request=request,
        )
    except ToolExecutionError as primary_exc:
        if not should_fallback_from_exa(config=config, primary_error=primary_exc):
            raise
        try:
            fallback_result = await execute_provider_search(
                client=client,
                provider=WebProvider.SEARXNG,
                config=config,
                request=request,
            )
        except ToolExecutionError as fallback_exc:
            raise build_failed_fallback_error(
                primary_error=primary_exc,
                fallback_error=fallback_exc,
            ) from primary_exc
        return fallback_result.model_copy(
            update={
                "internal_data": {
                    **fallback_result.internal_data,
                    "fallback_from": WebProvider.EXA.value,
                    "primary_error_type": primary_exc.error_type,
                }
            }
        )


def should_fallback_from_exa(
    *,
    config: WebConfig,
    primary_error: ToolExecutionError,
) -> bool:
    return (
        config.provider == WebProvider.EXA
        and config.fallback_provider == WebFallbackProvider.SEARXNG
        and is_exa_fallback_error(primary_error)
    )


def is_exa_fallback_error(primary_error: ToolExecutionError) -> bool:
    status_code = primary_error.details.get("status_code")
    if primary_error.error_type == "rate_limited":
        return True
    if status_code == 402:
        return True
    if status_code == 403:
        return _contains_exa_quota_hint(str(primary_error))
    return False


def _contains_exa_quota_hint(message: str) -> bool:
    lowered_message = message.lower()
    return any(
        marker in lowered_message
        for marker in (
            "quota",
            "credit",
            "billing",
            "rate limit",
            "too many requests",
            "limit reached",
            "exhausted",
        )
    )


def build_failed_fallback_error(
    *,
    primary_error: ToolExecutionError,
    fallback_error: ToolExecutionError,
) -> ToolExecutionError:
    details = dict(primary_error.details)
    details["fallback_error_type"] = fallback_error.error_type
    fallback_endpoint_host = fallback_error.details.get("endpoint_host")
    if isinstance(fallback_endpoint_host, str) and fallback_endpoint_host:
        details["fallback_endpoint_host"] = fallback_endpoint_host
    fallback_attempt_count = fallback_error.details.get("attempt_count")
    if isinstance(fallback_attempt_count, int):
        details["fallback_attempt_count"] = fallback_attempt_count
    return ToolExecutionError(
        error_type=primary_error.error_type,
        message=str(primary_error),
        retryable=primary_error.retryable,
        details=details,
    )


async def execute_provider_search(
    *,
    client: httpx.AsyncClient,
    provider: WebProvider,
    config: WebConfig,
    request: WebSearchRequest,
) -> SearchExecutionResult:
    if provider == WebProvider.EXA:
        prepared_request = build_provider_search_request(
            config=config.model_copy(update={"provider": provider}),
            request=request,
        )
        response_text = await fetch_exa_search_response(
            client=client,
            endpoint=prepared_request.endpoint,
            payload=prepared_request.payload,
        )
        extracted = extract_search_response(response_text)
        return SearchExecutionResult(
            provider=provider,
            endpoint_host=prepared_request.endpoint_host,
            upstream_tool=prepared_request.upstream_tool,
            hits=extracted.hits,
            upstream_search_time=extracted.upstream_search_time,
        )
    if provider == WebProvider.SEARXNG:
        return await execute_searxng_search(
            client=client,
            config=config,
            request=request,
        )
    raise ValueError(f"Unsupported web provider: {provider.value}")


async def execute_searxng_search(
    *,
    client: httpx.AsyncClient,
    config: WebConfig,
    request: WebSearchRequest,
) -> SearchExecutionResult:
    candidates = await resolve_searxng_instance_candidates(client=client, config=config)
    if not candidates:
        raise ToolExecutionError(
            error_type="upstream_unavailable",
            message="No SearXNG instances are available",
            retryable=True,
            details={"provider": WebProvider.SEARXNG.value},
        )

    available_candidates = select_available_searxng_candidates(candidates)
    last_error: ToolExecutionError | None = None
    attempt_count = 0
    for candidate in available_candidates:
        attempt_count += 1
        try:
            response_payload = await fetch_searxng_search_response(
                client=client,
                base_url=candidate.base_url,
                query=request.query,
            )
            hits = build_searxng_hits(
                response_payload=response_payload,
                request=request,
            )
            return SearchExecutionResult(
                provider=WebProvider.SEARXNG,
                endpoint_host=candidate.endpoint_host,
                upstream_tool=SEARXNG_SEARCH_TOOL_NAME,
                hits=hits,
                internal_data={
                    "searxng_instance_url": candidate.base_url,
                    "instance_source": candidate.source,
                },
            )
        except ToolExecutionError as exc:
            mark_searxng_candidate_failure(candidate)
            last_error = ToolExecutionError(
                error_type=exc.error_type,
                message=str(exc),
                retryable=exc.retryable,
                details={**exc.details, "attempt_count": attempt_count},
            )

    if last_error is not None:
        raise last_error
    raise ToolExecutionError(
        error_type="upstream_unavailable",
        message="SearXNG search failed without returning an error",
        retryable=True,
        details={
            "provider": WebProvider.SEARXNG.value,
            "attempt_count": attempt_count,
        },
    )


async def resolve_searxng_instance_candidates(
    *,
    client: httpx.AsyncClient,
    config: WebConfig,
) -> tuple[SearxngInstanceCandidate, ...]:
    configured_candidates: tuple[SearxngInstanceCandidate, ...] = ()
    if config.searxng_instance_url:
        configured_source = (
            "configured"
            if config.searxng_instance_url != DEFAULT_SEARXNG_INSTANCE_URL
            else "default"
        )
        configured_candidates = (
            build_searxng_instance_candidate(
                base_url=config.searxng_instance_url,
                source=configured_source,
            ),
        )
    public_candidates = await fetch_public_searxng_instance_candidates(client=client)
    seed_candidates = tuple(
        build_searxng_instance_candidate(base_url=base_url, source="seed")
        for base_url in DEFAULT_SEARXNG_INSTANCE_SEEDS
    )
    if configured_candidates and configured_candidates[0].source == "configured":
        return deduplicate_searxng_candidates(configured_candidates)
    return deduplicate_searxng_candidates(
        configured_candidates + public_candidates + seed_candidates
    )


def deduplicate_searxng_candidates(
    candidates: Sequence[SearxngInstanceCandidate],
) -> tuple[SearxngInstanceCandidate, ...]:
    deduplicated: list[SearxngInstanceCandidate] = []
    seen_base_urls: set[str] = set()
    for candidate in candidates:
        if candidate.base_url in seen_base_urls:
            continue
        deduplicated.append(candidate)
        seen_base_urls.add(candidate.base_url)
    return tuple(deduplicated)


def select_available_searxng_candidates(
    candidates: Sequence[SearxngInstanceCandidate],
) -> tuple[SearxngInstanceCandidate, ...]:
    now = time.monotonic()
    available = tuple(
        candidate
        for candidate in candidates
        if _SEARXNG_INSTANCE_COOLDOWNS.get(candidate.endpoint_host, 0.0) <= now
    )
    if available:
        return available
    return tuple(candidates)


async def fetch_public_searxng_instance_candidates(
    *,
    client: httpx.AsyncClient,
) -> tuple[SearxngInstanceCandidate, ...]:
    cached = get_cached_searxng_instance_candidates()
    if cached is not None:
        return cached

    endpoint_host = httpx.URL(SEARXNG_PUBLIC_INSTANCES_URL).host or ""
    try:
        response = await client.get(
            SEARXNG_PUBLIC_INSTANCES_URL,
            headers={"Accept": "application/json"},
        )
    except (httpx.TimeoutException, httpx.RequestError):
        if _SEARXNG_INSTANCE_CACHE is not None:
            return _SEARXNG_INSTANCE_CACHE.candidates
        return ()

    if response.status_code >= 400:
        _SEARXNG_INSTANCE_COOLDOWNS[endpoint_host] = (
            time.monotonic() + SEARXNG_INSTANCE_COOLDOWN_SECONDS
        )
        if _SEARXNG_INSTANCE_CACHE is not None:
            return _SEARXNG_INSTANCE_CACHE.candidates
        return ()

    try:
        payload = SearxngCatalogPayload.model_validate_json(response.text)
    except Exception:
        if _SEARXNG_INSTANCE_CACHE is not None:
            return _SEARXNG_INSTANCE_CACHE.candidates
        return ()

    candidates = tuple(select_public_searxng_instances(payload))
    set_cached_searxng_instance_candidates(candidates)
    return candidates


def get_cached_searxng_instance_candidates() -> (
    tuple[SearxngInstanceCandidate, ...] | None
):
    cache = _SEARXNG_INSTANCE_CACHE
    if cache is None:
        return None
    age_seconds = time.monotonic() - cache.fetched_at_monotonic
    if age_seconds >= SEARXNG_INSTANCE_CACHE_TTL_SECONDS:
        return None
    return cache.candidates


def set_cached_searxng_instance_candidates(
    candidates: tuple[SearxngInstanceCandidate, ...],
) -> None:
    global _SEARXNG_INSTANCE_CACHE
    _SEARXNG_INSTANCE_CACHE = SearxngInstanceCache(
        fetched_at_monotonic=time.monotonic(),
        candidates=candidates,
    )


def select_public_searxng_instances(
    payload: SearxngCatalogPayload,
) -> list[SearxngInstanceCandidate]:
    ranked: list[tuple[int, float, SearxngInstanceCandidate]] = []
    for base_url, raw_instance in payload.instances.items():
        if raw_instance.generator != "searxng":
            continue
        if raw_instance.analytics:
            continue
        if raw_instance.http is None or raw_instance.http.status_code != 200:
            continue
        initial_latency = (
            raw_instance.timing.initial.all.value
            if raw_instance.timing is not None
            and raw_instance.timing.initial is not None
            and raw_instance.timing.initial.all is not None
            and raw_instance.timing.initial.all.value is not None
            else 9999.0
        )
        ranked.append(
            (
                0 if raw_instance.main else 1,
                initial_latency,
                build_searxng_instance_candidate(
                    base_url=base_url,
                    source="public_pool",
                ),
            )
        )
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [candidate for _, _, candidate in ranked[:SEARXNG_PUBLIC_INSTANCE_LIMIT]]


def build_searxng_instance_candidate(
    *,
    base_url: str,
    source: str,
) -> SearxngInstanceCandidate:
    normalized_base_url = normalize_searxng_base_url(base_url)
    endpoint_host = httpx.URL(normalized_base_url).host or ""
    return SearxngInstanceCandidate(
        base_url=normalized_base_url,
        endpoint_host=endpoint_host,
        source=source,
    )


def normalize_searxng_base_url(base_url: str) -> str:
    parsed = httpx.URL(base_url.strip())
    normalized_path = parsed.path or "/"
    return str(
        parsed.copy_with(
            path=normalized_path,
            query=None,
            fragment=None,
            username=None,
            password=None,
        )
    )


def mark_searxng_candidate_failure(candidate: SearxngInstanceCandidate) -> None:
    _SEARXNG_INSTANCE_COOLDOWNS[candidate.endpoint_host] = (
        time.monotonic() + SEARXNG_INSTANCE_COOLDOWN_SECONDS
    )


async def fetch_searxng_search_response(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    query: str,
) -> SearxngSearchResponsePayload:
    endpoint = str(httpx.URL(base_url).join(SEARXNG_SEARCH_PATH))
    endpoint_host = httpx.URL(endpoint).host or ""
    try:
        response = await client.get(
            endpoint,
            headers={"Accept": "application/json"},
            params={"q": query, "format": "json"},
        )
    except httpx.TimeoutException as exc:
        raise ToolExecutionError(
            error_type="network_timeout",
            message=f"SearXNG web search timed out: {endpoint_host}",
            retryable=True,
            details={
                "provider": WebProvider.SEARXNG.value,
                "endpoint_host": endpoint_host,
            },
        ) from exc
    except httpx.RequestError as exc:
        raise ToolExecutionError(
            error_type="network_error",
            message=f"SearXNG web search request failed: {exc}",
            retryable=True,
            details={
                "provider": WebProvider.SEARXNG.value,
                "endpoint_host": endpoint_host,
            },
        ) from exc

    if response.status_code >= 400:
        raise ToolExecutionError(
            error_type=_search_status_error_type(response.status_code),
            message=_search_status_error_message(
                provider_label="SearXNG",
                response=response,
            ),
            retryable=response.status_code in {429} or response.status_code >= 500,
            details={
                "provider": WebProvider.SEARXNG.value,
                "endpoint_host": endpoint_host,
                "status_code": response.status_code,
            },
        )

    try:
        return SearxngSearchResponsePayload.model_validate_json(response.text)
    except Exception as exc:
        raise ToolExecutionError(
            error_type="upstream_error",
            message=f"SearXNG web search returned an invalid JSON response: {endpoint_host}",
            retryable=False,
            details={
                "provider": WebProvider.SEARXNG.value,
                "endpoint_host": endpoint_host,
            },
        ) from exc


def build_searxng_hits(
    *,
    response_payload: SearxngSearchResponsePayload,
    request: WebSearchRequest,
) -> tuple[WebSearchHit, ...]:
    projected_hits = tuple(
        hit
        for raw_hit in response_payload.results
        if (hit := project_searxng_search_hit(raw_hit)) is not None
    )
    return filter_web_search_hits(
        hits=projected_hits,
        allowed_domains=request.allowed_domains,
        blocked_domains=request.blocked_domains,
        num_results=request.num_results,
    )


def project_searxng_search_hit(
    raw_hit: SearxngSearchResultPayload,
) -> WebSearchHit | None:
    url = _normalize_optional_text(raw_hit.url)
    if url is None:
        return None
    title = _normalize_optional_text(raw_hit.title) or url
    return WebSearchHit(
        title=title,
        url=url,
        published_at=_normalize_optional_text(raw_hit.published_at),
        author=_normalize_optional_text(raw_hit.author),
        text=_normalize_optional_text(raw_hit.content),
    )


def filter_web_search_hits(
    *,
    hits: Sequence[WebSearchHit],
    allowed_domains: tuple[str, ...] | None,
    blocked_domains: tuple[str, ...] | None,
    num_results: int,
) -> tuple[WebSearchHit, ...]:
    filtered: list[WebSearchHit] = []
    for hit in hits:
        hit_host = (urlparse(hit.url).hostname or "").strip().lower().strip(".")
        if allowed_domains is not None and not _matches_domain_filters(
            hit_host,
            allowed_domains,
        ):
            continue
        if blocked_domains is not None and _matches_domain_filters(
            hit_host,
            blocked_domains,
        ):
            continue
        filtered.append(hit)
        if len(filtered) >= num_results:
            break
    return tuple(filtered)


def _matches_domain_filters(host: str, filters: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in filters)


def build_provider_search_request(
    *,
    config: WebConfig,
    request: WebSearchRequest,
) -> PreparedSearchRequest:
    if config.provider == WebProvider.EXA:
        endpoint = build_exa_search_url(
            api_key=config.get_api_key_for_provider(WebProvider.EXA),
            enabled_tools=(EXA_TOOL_NAME,),
        )
        return PreparedSearchRequest(
            provider=config.provider,
            endpoint=endpoint,
            endpoint_host=httpx.URL(endpoint).host or "",
            payload=build_exa_search_request(
                query=request.query,
                num_results=request.num_results,
                allowed_domains=request.allowed_domains,
                blocked_domains=request.blocked_domains,
            ),
            upstream_tool=EXA_TOOL_NAME,
        )
    raise ValueError(f"Unsupported web provider: {config.provider.value}")


def build_search_result_projection(
    *,
    query: str,
    result: SearchExecutionResult,
    duration_ms: int,
) -> ToolResultProjection:
    response = WebSearchResponse(
        query=query,
        provider=result.provider,
        hits=result.hits,
        duration_ms=duration_ms,
    )
    internal_data: dict[str, JsonValue] = {
        "endpoint_host": result.endpoint_host,
        "upstream_tool": result.upstream_tool,
    }
    if result.upstream_search_time is not None:
        internal_data["upstream_search_time"] = result.upstream_search_time
    internal_data.update(result.internal_data)
    return ToolResultProjection(
        visible_data=response.model_dump(mode="json"),
        internal_data=internal_data,
    )


def build_exa_search_request(
    *,
    query: str,
    num_results: int,
    allowed_domains: tuple[str, ...] | None = None,
    blocked_domains: tuple[str, ...] | None = None,
) -> dict[str, JsonValue]:
    arguments = ExaSearchArguments(
        query=query,
        numResults=num_results,
        type=DEFAULT_EXA_SEARCH_TYPE,
        includeDomains=allowed_domains,
        excludeDomains=blocked_domains,
        highlightsQuery=query,
    )
    request = ExaSearchRequest(
        params={
            "name": EXA_TOOL_NAME,
            "arguments": arguments.model_dump(mode="json", exclude_none=True),
        }
    )
    return request.model_dump(mode="json")


def build_exa_search_url(
    *,
    api_key: str | None,
    enabled_tools: tuple[str, ...] = (),
) -> str:
    base_url = f"{EXA_BASE_URL}{EXA_PATH}"
    query_params: list[tuple[str, str]] = []
    if api_key:
        query_params.append(("exaApiKey", api_key))
    if enabled_tools:
        query_params.append(("tools", ",".join(enabled_tools)))
    if not query_params:
        return base_url
    return f"{base_url}?{urlencode(query_params)}"


async def fetch_exa_search_response(
    *,
    client: httpx.AsyncClient,
    endpoint: str,
    payload: dict[str, JsonValue],
) -> str:
    endpoint_host = httpx.URL(endpoint).host or ""
    try:
        response = await client.post(
            endpoint,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    except httpx.TimeoutException as exc:
        raise ToolExecutionError(
            error_type="network_timeout",
            message="Exa web search timed out",
            retryable=True,
            details={
                "provider": WebProvider.EXA.value,
                "endpoint_host": endpoint_host,
            },
        ) from exc
    except httpx.RequestError as exc:
        raise ToolExecutionError(
            error_type="network_error",
            message=f"Exa web search request failed: {exc}",
            retryable=True,
            details={
                "provider": WebProvider.EXA.value,
                "endpoint_host": endpoint_host,
            },
        ) from exc

    if response.status_code >= 400:
        raise ToolExecutionError(
            error_type=_search_status_error_type(response.status_code),
            message=_search_status_error_message(
                provider_label="Exa",
                response=response,
            ),
            retryable=response.status_code in {429} or response.status_code >= 500,
            details={
                "provider": WebProvider.EXA.value,
                "endpoint_host": endpoint_host,
                "status_code": response.status_code,
            },
        )
    rpc_error = _parse_exa_rpc_error_response(response.text)
    if rpc_error is not None:
        raise rpc_error
    return response.text


def _search_status_error_type(status_code: int) -> str:
    if status_code == 401:
        return "auth_error"
    if status_code == 403:
        return "source_access_denied"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "upstream_unavailable"
    return "upstream_error"


def _parse_exa_rpc_error_response(response_text: str) -> ToolExecutionError | None:
    try:
        payload = ExaRpcResponsePayload.model_validate_json(response_text)
    except Exception:
        return None
    if payload.error is None:
        return None
    message = _normalize_optional_text(payload.error.message) or "Exa web search failed"
    error_type = (
        "rate_limited" if _contains_exa_quota_hint(message) else "upstream_error"
    )
    return ToolExecutionError(
        error_type=error_type,
        message=f"Exa web search returned JSON-RPC error: {message}",
        retryable=error_type == "rate_limited",
        details={
            "provider": WebProvider.EXA.value,
            "rpc_error_code": payload.error.code,
        },
    )


def _search_status_error_message(
    *,
    provider_label: str,
    response: httpx.Response,
) -> str:
    detail = response.text.strip()
    base = f"{provider_label} web search returned HTTP {response.status_code}"
    if detail:
        return f"{base}: {detail}"
    return base


def extract_search_response(response_text: str) -> ExtractedSearchResponse:
    latest_candidate: ExtractedSearchResponse | None = None
    for raw_line in response_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        try:
            event = _parse_search_event(payload)
        except Exception:
            continue
        result = event.get("result")
        if not isinstance(result, dict):
            continue
        content = result.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parsed = _parse_search_content(text.strip())
                meta = item.get("_meta")
                search_time = meta.get("searchTime") if isinstance(meta, dict) else None
                if parsed.upstream_search_time is None and isinstance(
                    search_time, (int, float)
                ):
                    parsed = parsed.model_copy(
                        update={"upstream_search_time": float(search_time)}
                    )
                if parsed.hits or parsed.upstream_search_time is not None:
                    latest_candidate = _merge_extracted_search_response(
                        latest_candidate,
                        parsed,
                    )
    if latest_candidate is not None:
        return latest_candidate
    return ExtractedSearchResponse()


def _merge_extracted_search_response(
    current: ExtractedSearchResponse | None,
    candidate: ExtractedSearchResponse,
) -> ExtractedSearchResponse:
    if current is None:
        return candidate
    return ExtractedSearchResponse(
        hits=candidate.hits if candidate.hits else current.hits,
        upstream_search_time=(
            candidate.upstream_search_time
            if candidate.upstream_search_time is not None
            else current.upstream_search_time
        ),
    )


def _parse_search_content(content: str) -> ExtractedSearchResponse:
    try:
        payload = ExaSearchPayload.model_validate_json(content)
    except Exception:
        return ExtractedSearchResponse(hits=_parse_legacy_search_hits(content))
    return ExtractedSearchResponse(
        hits=tuple(
            hit
            for raw_hit in payload.results
            if (hit := _project_search_hit(raw_hit)) is not None
        ),
        upstream_search_time=payload.searchTime,
    )


def _project_search_hit(raw_hit: ExaSearchHitPayload) -> WebSearchHit | None:
    url = _normalize_optional_text(raw_hit.url)
    if url is None:
        return None
    title = _normalize_optional_text(raw_hit.title) or url
    return WebSearchHit(
        title=title,
        url=url,
        published_at=_normalize_optional_text(raw_hit.published_at),
        author=_normalize_optional_text(raw_hit.author),
        highlights=tuple(
            highlight
            for highlight in (
                _normalize_optional_text(item) for item in raw_hit.highlights
            )
            if highlight is not None
        ),
        text=_normalize_optional_text(raw_hit.text),
        summary=_normalize_optional_text(raw_hit.summary),
    )


def _parse_legacy_search_hits(content: str) -> tuple[WebSearchHit, ...]:
    hits: list[WebSearchHit] = []
    for block in re.split(r"\n\s*---\s*\n", content):
        hit = _parse_legacy_search_hit(block.strip())
        if hit is not None:
            hits.append(hit)
    return tuple(hits)


def _parse_legacy_search_hit(block: str) -> WebSearchHit | None:
    if not block:
        return None
    title = ""
    url = ""
    published_at: str | None = None
    author: str | None = None
    highlights: tuple[str, ...] = ()
    text: str | None = None
    summary: str | None = None

    current_label: str | None = None
    current_lines: list[str] = []

    def flush_current() -> None:
        nonlocal highlights, text, summary, current_label, current_lines
        if current_label == "highlights":
            highlights = tuple(line for line in _normalize_text_lines(current_lines))
        elif current_label == "text":
            text = _join_optional_text(current_lines)
        elif current_label == "summary":
            summary = _join_optional_text(current_lines)
        current_label = None
        current_lines = []

    for line in block.splitlines():
        if line.startswith("Title:"):
            flush_current()
            title = line.partition(":")[2].strip()
            continue
        if line.startswith("URL:"):
            flush_current()
            url = line.partition(":")[2].strip()
            continue
        if line.startswith("Published:"):
            flush_current()
            published_at = _normalize_optional_text(line.partition(":")[2])
            continue
        if line.startswith("Author:"):
            flush_current()
            author = _normalize_optional_text(line.partition(":")[2])
            continue
        if line.startswith("Highlights:"):
            flush_current()
            current_label = "highlights"
            remainder = line.partition(":")[2].strip()
            current_lines = [remainder] if remainder else []
            continue
        if line.startswith("Text:"):
            flush_current()
            current_label = "text"
            remainder = line.partition(":")[2].strip()
            current_lines = [remainder] if remainder else []
            continue
        if line.startswith("Summary:"):
            flush_current()
            current_label = "summary"
            remainder = line.partition(":")[2].strip()
            current_lines = [remainder] if remainder else []
            continue
        if current_label is not None:
            current_lines.append(line)
    flush_current()

    normalized_url = _normalize_optional_text(url)
    if normalized_url is None:
        return None
    normalized_title = _normalize_optional_text(title) or normalized_url
    return WebSearchHit(
        title=normalized_title,
        url=normalized_url,
        published_at=published_at,
        author=author,
        highlights=highlights,
        text=text,
        summary=summary,
    )


def _parse_search_event(payload: str) -> dict[str, JsonValue]:
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError("Invalid SSE payload from web search backend")
    normalized: dict[str, JsonValue] = {}
    for key, value in data.items():
        if isinstance(key, str):
            normalized[key] = _normalize_json_value(value)
    return normalized


def _normalize_json_value(value: object) -> JsonValue:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            normalized[str(key)] = _normalize_json_value(item)
        return normalized
    return str(value)


def _normalize_domain(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("Domain filters cannot be blank")
    parsed = urlparse(normalized if "://" in normalized else f"https://{normalized}")
    domain = (parsed.hostname or "").strip().lower().strip(".")
    if not domain:
        raise ValueError(f"Invalid domain filter: {value!r}")
    return domain


def _normalize_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.lower() in {"n/a", "none", "null"}:
        return None
    return normalized


def _normalize_text_lines(lines: list[str]) -> tuple[str, ...]:
    return tuple(
        normalized
        for normalized in (_normalize_optional_text(line) for line in lines)
        if normalized is not None
    )


def _join_optional_text(lines: list[str]) -> str | None:
    normalized_lines = _normalize_text_lines(lines)
    if not normalized_lines:
        return None
    return "\n".join(normalized_lines)


def _normalize_domain_summary(
    domains: list[str] | None,
) -> list[JsonValue] | None:
    if domains is None:
        return None
    return [_normalize_json_value(domain) for domain in domains]
