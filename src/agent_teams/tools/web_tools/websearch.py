# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import time
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

from agent_teams.env.web_config_models import WebConfig
from agent_teams.env.web_config_models import WebProvider
from agent_teams.net.clients import create_async_http_client
from agent_teams.tools._description_loader import load_tool_description
from agent_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolExecutionError,
    ToolResultProjection,
    execute_tool,
)
from agent_teams.tools.web_tools.common import load_runtime_web_config

EXA_BASE_URL = "https://mcp.exa.ai"
EXA_PATH = "/mcp"
EXA_TOOL_NAME = "web_search_advanced_exa"
DEFAULT_NUM_RESULTS = 8
DEFAULT_EXA_SEARCH_TYPE = "auto"
DEFAULT_TIMEOUT_SECONDS = 25.0
DEFAULT_TEXT_MAX_CHARACTERS = 300
DEFAULT_HIGHLIGHTS_PER_URL = 3
DEFAULT_HIGHLIGHT_SENTENCES = 2
DESCRIPTION = load_tool_description(__file__)


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


class ExtractedSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hits: tuple[WebSearchHit, ...] = ()
    upstream_search_time: float | None = None


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
            allowed_domain_filters = (
                tuple(allowed_domains) if allowed_domains is not None else None
            )
            blocked_domain_filters = (
                tuple(blocked_domains) if blocked_domains is not None else None
            )
            request = WebSearchRequest(
                query=query,
                num_results=num_results or DEFAULT_NUM_RESULTS,
                allowed_domains=allowed_domain_filters,
                blocked_domains=blocked_domain_filters,
            )
            prepared_request = build_provider_search_request(
                config=config,
                request=request,
            )
            async with create_async_http_client(
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
                follow_redirects=True,
            ) as client:
                response_text = await fetch_exa_search_response(
                    client=client,
                    endpoint=prepared_request.endpoint,
                    payload=prepared_request.payload,
                )

            extracted = extract_search_response(response_text)
            duration_ms = int((time.perf_counter() - started) * 1000)
            return build_search_result_projection(
                query=request.query,
                provider=prepared_request.provider,
                hits=extracted.hits,
                duration_ms=duration_ms,
                endpoint_host=prepared_request.endpoint_host,
                upstream_tool=prepared_request.upstream_tool,
                upstream_search_time=extracted.upstream_search_time,
            )

        allowed_domains_summary: list[JsonValue] | None = None
        if allowed_domains is not None:
            allowed_domains_summary = [
                _normalize_json_value(domain) for domain in allowed_domains
            ]
        blocked_domains_summary: list[JsonValue] | None = None
        if blocked_domains is not None:
            blocked_domains_summary = [
                _normalize_json_value(domain) for domain in blocked_domains
            ]

        return await execute_tool(
            ctx,
            tool_name="websearch",
            args_summary={
                "query": query,
                "num_results": num_results,
                "allowed_domains": allowed_domains_summary,
                "blocked_domains": blocked_domains_summary,
            },
            action=_action,
        )


def build_provider_search_request(
    *,
    config: WebConfig,
    request: WebSearchRequest,
) -> PreparedSearchRequest:
    if config.provider == WebProvider.EXA:
        endpoint = build_exa_search_url(
            api_key=config.api_key,
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
    provider: WebProvider,
    hits: tuple[WebSearchHit, ...],
    duration_ms: int,
    endpoint_host: str,
    upstream_tool: str,
    upstream_search_time: float | None,
) -> ToolResultProjection:
    result = WebSearchResponse(
        query=query,
        provider=provider,
        hits=hits,
        duration_ms=duration_ms,
    )
    internal_data: dict[str, JsonValue] = {
        "endpoint_host": endpoint_host,
        "upstream_tool": upstream_tool,
    }
    if upstream_search_time is not None:
        internal_data["upstream_search_time"] = upstream_search_time
    return ToolResultProjection(
        visible_data=result.model_dump(mode="json"),
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
            message=_search_status_error_message(response),
            retryable=response.status_code in {429} or response.status_code >= 500,
            details={
                "provider": WebProvider.EXA.value,
                "endpoint_host": endpoint_host,
                "status_code": response.status_code,
            },
        )
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


def _search_status_error_message(response: httpx.Response) -> str:
    detail = response.text.strip()
    base = f"Exa web search returned HTTP {response.status_code}"
    if detail:
        return f"{base}: {detail}"
    return base


def extract_search_response(response_text: str) -> ExtractedSearchResponse:
    for raw_line in response_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        event = _parse_search_event(payload)
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
                    return parsed.model_copy(
                        update={
                            "upstream_search_time": float(search_time),
                        }
                    )
                return parsed
    return ExtractedSearchResponse()


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
