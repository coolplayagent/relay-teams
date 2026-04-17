# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Awaitable, Callable, cast

import httpx
from pydantic_ai import Agent
import pytest

from relay_teams.env.web_config_models import (
    DEFAULT_SEARXNG_INSTANCE_URL,
    WebConfig,
    WebFallbackProvider,
    WebProvider,
)
from relay_teams.tools.runtime import ToolDeps, ToolExecutionError
from relay_teams.tools.web_tools import websearch


def test_web_search_request_normalizes_domain_filters() -> None:
    request = websearch.WebSearchRequest(
        query=" latest ai news ",
        allowed_domains=("https://Docs.Python.org/3/", "docs.python.org"),
    )

    assert request.query == "latest ai news"
    assert request.allowed_domains == ("docs.python.org",)


def test_web_search_request_rejects_mixed_domain_filters() -> None:
    with pytest.raises(ValueError, match="allowed_domains and blocked_domains"):
        websearch.WebSearchRequest(
            query="latest ai news",
            allowed_domains=("docs.python.org",),
            blocked_domains=("example.com",),
        )


def test_build_exa_search_request_uses_advanced_tool_defaults() -> None:
    payload = websearch.build_exa_search_request(
        query="latest ai news",
        num_results=8,
    )

    params = cast(dict[str, object], payload["params"])
    assert params["name"] == "web_search_advanced_exa"
    arguments = cast(dict[str, object], params["arguments"])
    assert arguments["query"] == "latest ai news"
    assert arguments["numResults"] == 8
    assert arguments["type"] == "auto"
    assert arguments["textMaxCharacters"] == 300
    assert arguments["enableHighlights"] is True
    assert arguments["highlightsNumSentences"] == 2
    assert arguments["highlightsPerUrl"] == 3
    assert arguments["highlightsQuery"] == "latest ai news"


def test_build_provider_search_request_uses_exa_provider_adapter() -> None:
    prepared = websearch.build_provider_search_request(
        config=WebConfig(provider=WebProvider.EXA, exa_api_key="secret"),
        request=websearch.WebSearchRequest(
            query="latest ai news",
            allowed_domains=("https://docs.python.org/3/",),
        ),
    )

    assert prepared.provider == WebProvider.EXA
    assert (
        prepared.endpoint
        == "https://mcp.exa.ai/mcp?exaApiKey=secret&tools=web_search_advanced_exa"
    )
    assert prepared.endpoint_host == "mcp.exa.ai"
    assert prepared.upstream_tool == "web_search_advanced_exa"
    params = cast(dict[str, object], prepared.payload["params"])
    arguments = cast(dict[str, object], params["arguments"])
    assert arguments["includeDomains"] == ["docs.python.org"]
    assert "excludeDomains" not in arguments


def test_build_search_result_projection_keeps_sanitized_internal_metadata() -> None:
    projection = websearch.build_search_result_projection(
        query="latest ai news",
        result=websearch.SearchExecutionResult(
            provider=WebProvider.SEARXNG,
            endpoint_host="search.example.test",
            upstream_tool="search",
            hits=(
                websearch.WebSearchHit(
                    title="Python Docs",
                    url="https://docs.python.org",
                ),
            ),
            internal_data={
                "fallback_from": "exa",
                "primary_error_type": "rate_limited",
                "searxng_instance_url": "https://search.example.test/",
            },
        ),
        duration_ms=42,
    )

    assert projection.visible_data == {
        "query": "latest ai news",
        "provider": "searxng",
        "hits": [
            {
                "title": "Python Docs",
                "url": "https://docs.python.org",
                "published_at": None,
                "author": None,
                "highlights": [],
                "text": None,
                "summary": None,
            }
        ],
        "duration_ms": 42,
    }
    assert projection.internal_data == {
        "endpoint_host": "search.example.test",
        "upstream_tool": "search",
        "fallback_from": "exa",
        "primary_error_type": "rate_limited",
        "searxng_instance_url": "https://search.example.test/",
    }


def test_build_exa_search_url_appends_optional_api_key_and_tools() -> None:
    assert websearch.build_exa_search_url(api_key=None) == "https://mcp.exa.ai/mcp"
    assert (
        websearch.build_exa_search_url(
            api_key="secret",
            enabled_tools=("web_search_advanced_exa",),
        )
        == "https://mcp.exa.ai/mcp?exaApiKey=secret&tools=web_search_advanced_exa"
    )


def test_extract_search_response_reads_json_text_block() -> None:
    response_text = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"{\\"results\\":[{\\"title\\":\\"Python Docs\\",\\"url\\":\\"https://docs.python.org\\",\\"publishedDate\\":\\"2026-03-30\\",\\"author\\":\\"Python\\",\\"highlights\\":[\\"Official docs\\"],\\"summary\\":\\"Reference\\"}],\\"searchTime\\":1.25}","_meta":{"searchTime":1.25}}]}}\n'
    )

    extracted = websearch.extract_search_response(response_text)

    assert extracted.upstream_search_time == 1.25
    assert len(extracted.hits) == 1
    hit = extracted.hits[0]
    assert hit.title == "Python Docs"
    assert hit.url == "https://docs.python.org"
    assert hit.published_at == "2026-03-30"
    assert hit.author == "Python"
    assert hit.highlights == ("Official docs",)
    assert hit.summary == "Reference"


def test_extract_search_response_falls_back_to_legacy_text_blocks() -> None:
    response_text = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"Title: Python Docs\\nURL: https://docs.python.org\\nPublished: 2026-03-30\\nAuthor: Python\\nHighlights:\\nOfficial docs\\nReference material\\n\\n---\\n\\nTitle: Example\\nURL: https://example.com\\nPublished: N/A\\nAuthor: N/A\\nText: Example body"}]}}\n'
    )

    extracted = websearch.extract_search_response(response_text)

    assert len(extracted.hits) == 2
    assert extracted.hits[0].highlights == (
        "Official docs",
        "Reference material",
    )
    assert extracted.hits[1].text == "Example body"
    assert extracted.hits[1].published_at is None
    assert extracted.hits[1].author is None


def test_build_searxng_hits_filters_domains_and_result_count() -> None:
    hits = websearch.build_searxng_hits(
        response_payload=websearch.SearxngSearchResponsePayload(
            results=(
                websearch.SearxngSearchResultPayload(
                    title="Allowed",
                    url="https://docs.python.org/3/",
                    content="Docs",
                ),
                websearch.SearxngSearchResultPayload(
                    title="Blocked",
                    url="https://example.com",
                    content="Nope",
                ),
                websearch.SearxngSearchResultPayload(
                    title="Subdomain",
                    url="https://blog.docs.python.org/post",
                    content="Post",
                ),
            )
        ),
        request=websearch.WebSearchRequest(
            query="python",
            num_results=1,
            allowed_domains=("docs.python.org",),
        ),
    )

    assert hits == (
        websearch.WebSearchHit(
            title="Allowed",
            url="https://docs.python.org/3/",
            text="Docs",
        ),
    )


def test_select_public_searxng_instances_filters_and_sorts_candidates() -> None:
    payload = websearch.SearxngCatalogPayload(
        instances={
            "https://slow.example/": websearch.SearxngCatalogInstancePayload(
                generator="searxng",
                main=True,
                analytics=False,
                http=websearch.SearxngCatalogHttpPayload(status_code=200),
                timing=websearch.SearxngCatalogTimingPayload(
                    initial=websearch.SearxngCatalogTimingEntry(
                        all=websearch.SearxngCatalogTimedValue(value=0.8)
                    )
                ),
            ),
            "https://fast.example/": websearch.SearxngCatalogInstancePayload(
                generator="searxng",
                main=True,
                analytics=False,
                http=websearch.SearxngCatalogHttpPayload(status_code=200),
                timing=websearch.SearxngCatalogTimingPayload(
                    initial=websearch.SearxngCatalogTimingEntry(
                        all=websearch.SearxngCatalogTimedValue(value=0.2)
                    )
                ),
            ),
            "https://backup.example/": websearch.SearxngCatalogInstancePayload(
                generator="searxng",
                main=False,
                analytics=False,
                http=websearch.SearxngCatalogHttpPayload(status_code=200),
                timing=websearch.SearxngCatalogTimingPayload(
                    initial=websearch.SearxngCatalogTimingEntry(
                        all=websearch.SearxngCatalogTimedValue(value=0.1)
                    )
                ),
            ),
            "https://analytics.example/": websearch.SearxngCatalogInstancePayload(
                generator="searxng",
                main=True,
                analytics=True,
                http=websearch.SearxngCatalogHttpPayload(status_code=200),
            ),
        }
    )

    selected = websearch.select_public_searxng_instances(payload)

    assert selected == [
        websearch.SearxngInstanceCandidate(
            base_url="https://fast.example/",
            endpoint_host="fast.example",
            source="public_pool",
        ),
        websearch.SearxngInstanceCandidate(
            base_url="https://slow.example/",
            endpoint_host="slow.example",
            source="public_pool",
        ),
        websearch.SearxngInstanceCandidate(
            base_url="https://backup.example/",
            endpoint_host="backup.example",
            source="public_pool",
        ),
    ]


@pytest.mark.asyncio
async def test_resolve_searxng_instance_candidates_prefers_default_then_public_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch_public_candidates(
        *,
        client: httpx.AsyncClient,
    ) -> tuple[websearch.SearxngInstanceCandidate, ...]:
        _ = client
        return (
            websearch.SearxngInstanceCandidate(
                base_url=DEFAULT_SEARXNG_INSTANCE_URL,
                endpoint_host="search.mdosch.de",
                source="public_pool",
            ),
            websearch.SearxngInstanceCandidate(
                base_url="https://fallback.example/",
                endpoint_host="fallback.example",
                source="public_pool",
            ),
        )

    monkeypatch.setattr(
        websearch,
        "fetch_public_searxng_instance_candidates",
        _fake_fetch_public_candidates,
    )

    client = httpx.AsyncClient(trust_env=False)
    try:
        candidates = await websearch.resolve_searxng_instance_candidates(
            client=client,
            config=WebConfig(),
        )
    finally:
        await client.aclose()

    assert candidates[0] == websearch.SearxngInstanceCandidate(
        base_url=DEFAULT_SEARXNG_INSTANCE_URL,
        endpoint_host="search.mdosch.de",
        source="default",
    )
    assert candidates[1] == websearch.SearxngInstanceCandidate(
        base_url="https://fallback.example/",
        endpoint_host="fallback.example",
        source="public_pool",
    )
    assert [candidate.base_url for candidate in candidates].count(
        DEFAULT_SEARXNG_INSTANCE_URL
    ) == 1


@pytest.mark.asyncio
async def test_resolve_searxng_instance_candidates_locks_to_explicit_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch_public_candidates(
        *,
        client: httpx.AsyncClient,
    ) -> tuple[websearch.SearxngInstanceCandidate, ...]:
        _ = client
        return (
            websearch.SearxngInstanceCandidate(
                base_url="https://fallback.example/",
                endpoint_host="fallback.example",
                source="public_pool",
            ),
        )

    monkeypatch.setattr(
        websearch,
        "fetch_public_searxng_instance_candidates",
        _fake_fetch_public_candidates,
    )

    client = httpx.AsyncClient(trust_env=False)
    try:
        candidates = await websearch.resolve_searxng_instance_candidates(
            client=client,
            config=WebConfig(searxng_instance_url="https://search.example.test/"),
        )
    finally:
        await client.aclose()

    assert candidates == (
        websearch.SearxngInstanceCandidate(
            base_url="https://search.example.test/",
            endpoint_host="search.example.test",
            source="configured",
        ),
    )


@pytest.mark.asyncio
async def test_fetch_exa_search_response_classifies_http_errors() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request, text="boom")

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(ToolExecutionError) as exc_info:
            await websearch.fetch_exa_search_response(
                client=client,
                endpoint="https://mcp.exa.ai/mcp?tools=web_search_advanced_exa",
                payload={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {},
                },
            )
    finally:
        await client.aclose()

    assert exc_info.value.error_type == "upstream_unavailable"
    assert exc_info.value.retryable is True
    assert exc_info.value.details == {
        "provider": "exa",
        "endpoint_host": "mcp.exa.ai",
        "status_code": 500,
    }


@pytest.mark.asyncio
async def test_execute_search_falls_back_to_searxng_after_exa_rate_limit() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        if "mcp.exa.ai" in str(request.url):
            return httpx.Response(429, request=request, text="limit reached")
        return httpx.Response(
            200,
            request=request,
            json={
                "results": [
                    {
                        "title": "Python Docs",
                        "url": "https://docs.python.org/3/",
                        "content": "Reference",
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        result = await websearch.execute_search(
            client=client,
            config=WebConfig(
                provider=WebProvider.EXA,
                fallback_provider=WebFallbackProvider.SEARXNG,
                searxng_instance_url="https://search.example.test/",
            ),
            request=websearch.WebSearchRequest(query="python"),
        )
    finally:
        await client.aclose()

    assert result.provider == WebProvider.SEARXNG
    assert result.endpoint_host == "search.example.test"
    assert result.internal_data == {
        "searxng_instance_url": "https://search.example.test/",
        "instance_source": "configured",
        "fallback_from": "exa",
        "primary_error_type": "rate_limited",
    }
    assert result.hits == (
        websearch.WebSearchHit(
            title="Python Docs",
            url="https://docs.python.org/3/",
            text="Reference",
        ),
    )


@pytest.mark.asyncio
async def test_execute_search_defaults_to_searxng_fallback_after_exa_rate_limit() -> (
    None
):
    async def _handler(request: httpx.Request) -> httpx.Response:
        if "mcp.exa.ai" in str(request.url):
            return httpx.Response(429, request=request, text="limit reached")
        return httpx.Response(
            200,
            request=request,
            json={
                "results": [
                    {
                        "title": "Python Docs",
                        "url": "https://docs.python.org/3/",
                        "content": "Reference",
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        result = await websearch.execute_search(
            client=client,
            config=WebConfig(
                provider=WebProvider.EXA,
                searxng_instance_url="https://search.example.test/",
            ),
            request=websearch.WebSearchRequest(query="python"),
        )
    finally:
        await client.aclose()

    assert result.provider == WebProvider.SEARXNG
    assert result.endpoint_host == "search.example.test"
    assert result.internal_data == {
        "searxng_instance_url": "https://search.example.test/",
        "instance_source": "configured",
        "fallback_from": "exa",
        "primary_error_type": "rate_limited",
    }


@pytest.mark.asyncio
async def test_execute_search_falls_back_after_exa_json_rpc_rate_limit_error() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        if "mcp.exa.ai" in str(request.url):
            return httpx.Response(
                200,
                request=request,
                json={
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32000,
                        "message": "You've hit Exa's free MCP rate limit. To continue using without limits, create your own Exa API key.",
                    },
                    "id": None,
                },
            )
        return httpx.Response(
            200,
            request=request,
            json={
                "results": [
                    {
                        "title": "Python Docs",
                        "url": "https://docs.python.org/3/",
                        "content": "Reference",
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        result = await websearch.execute_search(
            client=client,
            config=WebConfig(
                provider=WebProvider.EXA,
                fallback_provider=WebFallbackProvider.SEARXNG,
                searxng_instance_url="https://search.example.test/",
            ),
            request=websearch.WebSearchRequest(query="python"),
        )
    finally:
        await client.aclose()

    assert result.provider == WebProvider.SEARXNG
    assert result.endpoint_host == "search.example.test"
    assert result.internal_data == {
        "searxng_instance_url": "https://search.example.test/",
        "instance_source": "configured",
        "fallback_from": "exa",
        "primary_error_type": "rate_limited",
    }


@pytest.mark.asyncio
async def test_execute_search_preserves_primary_error_when_fallback_also_fails() -> (
    None
):
    async def _handler(request: httpx.Request) -> httpx.Response:
        if "mcp.exa.ai" in str(request.url):
            return httpx.Response(429, request=request, text="limit reached")
        return httpx.Response(503, request=request, text="searxng down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(ToolExecutionError) as exc_info:
            await websearch.execute_search(
                client=client,
                config=WebConfig(
                    provider=WebProvider.EXA,
                    fallback_provider=WebFallbackProvider.SEARXNG,
                    searxng_instance_url="https://search.example.test/",
                ),
                request=websearch.WebSearchRequest(query="python"),
            )
    finally:
        await client.aclose()

    assert exc_info.value.error_type == "rate_limited"
    assert exc_info.value.details == {
        "provider": "exa",
        "endpoint_host": "mcp.exa.ai",
        "status_code": 429,
        "fallback_error_type": "upstream_unavailable",
        "fallback_endpoint_host": "search.example.test",
        "fallback_attempt_count": 1,
    }


@pytest.mark.asyncio
async def test_execute_search_does_not_fallback_for_non_quota_exa_errors() -> None:
    request_hosts: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        request_hosts.append(request.url.host or "")
        if "mcp.exa.ai" in str(request.url):
            return httpx.Response(500, request=request, text="exa down")
        return httpx.Response(200, request=request, json={"results": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(ToolExecutionError) as exc_info:
            await websearch.execute_search(
                client=client,
                config=WebConfig(
                    provider=WebProvider.EXA,
                    fallback_provider=WebFallbackProvider.SEARXNG,
                    searxng_instance_url="https://search.example.test/",
                ),
                request=websearch.WebSearchRequest(query="python"),
            )
    finally:
        await client.aclose()

    assert exc_info.value.error_type == "upstream_unavailable"
    assert exc_info.value.details == {
        "provider": "exa",
        "endpoint_host": "mcp.exa.ai",
        "status_code": 500,
    }
    assert request_hosts == ["mcp.exa.ai"]


@pytest.mark.asyncio
async def test_execute_search_does_not_fallback_when_explicitly_disabled() -> None:
    request_hosts: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        request_hosts.append(request.url.host or "")
        if "mcp.exa.ai" in str(request.url):
            return httpx.Response(429, request=request, text="limit reached")
        return httpx.Response(200, request=request, json={"results": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(ToolExecutionError) as exc_info:
            await websearch.execute_search(
                client=client,
                config=WebConfig(
                    provider=WebProvider.EXA,
                    fallback_provider=WebFallbackProvider.DISABLED,
                    searxng_instance_url="https://search.example.test/",
                ),
                request=websearch.WebSearchRequest(query="python"),
            )
    finally:
        await client.aclose()

    assert exc_info.value.error_type == "rate_limited"
    assert exc_info.value.details == {
        "provider": "exa",
        "endpoint_host": "mcp.exa.ai",
        "status_code": 429,
    }
    assert request_hosts == ["mcp.exa.ai"]


def test_is_exa_fallback_error_accepts_quota_style_403_errors() -> None:
    assert websearch.is_exa_fallback_error(
        ToolExecutionError(
            error_type="source_access_denied",
            message="Exa web search returned HTTP 403: quota exceeded",
            retryable=False,
            details={"status_code": 403},
        )
    )


class _FakeAgent:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., object]] = {}

    def tool(
        self, *, description: str
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        del description

        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            self.tools[func.__name__] = func
            return func

        return decorator


@pytest.mark.asyncio
async def test_register_websearch_uses_default_proxy_env_when_hook_runtime_env_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent = _FakeAgent()
    websearch.register(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]], fake_agent.tools["websearch"]
    )

    captured: dict[str, object] = {}

    class _FakeAsyncClient:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            _ = (exc_type, exc, tb)
            return False

    def _fake_create_async_http_client(**kwargs: object) -> _FakeAsyncClient:
        captured.update(kwargs)
        return _FakeAsyncClient()

    async def _fake_execute_search(**kwargs: object) -> object:
        _ = kwargs
        return websearch.SearchExecutionResult(
            provider=websearch.WebProvider.SEARXNG,
            endpoint_host="example.test",
            upstream_tool="search",
            hits=(),
            internal_data={},
        )

    async def _fake_execute_tool(ctx, **kwargs: object) -> dict[str, object]:
        _ = ctx
        action = cast(
            Callable[..., Awaitable[dict[str, object]]],
            kwargs["action"],
        )
        raw_args = cast(dict[str, object], kwargs["raw_args"])
        tool_args = {
            name: raw_args[name]
            for name in inspect.signature(action).parameters
            if name in raw_args
        }
        return await action(**tool_args)

    monkeypatch.setattr(
        websearch, "create_async_http_client", _fake_create_async_http_client
    )
    monkeypatch.setattr(websearch, "execute_search", _fake_execute_search)
    monkeypatch.setattr(websearch, "execute_tool_call", _fake_execute_tool)
    monkeypatch.setattr(websearch, "load_runtime_web_config", lambda: WebConfig())

    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            hook_runtime_env={},
        ),
    )

    _ = await tool(ctx, query="python")

    assert "merged_env" not in captured
