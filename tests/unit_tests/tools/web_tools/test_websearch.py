# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import cast

import httpx
import pytest

from agent_teams.env.web_config_models import WebConfig, WebProvider
from agent_teams.tools.runtime import ToolExecutionError
from agent_teams.tools.web_tools import websearch


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


def test_build_provider_search_request_uses_provider_adapter() -> None:
    prepared = websearch.build_provider_search_request(
        config=WebConfig(provider=WebProvider.EXA, api_key="secret"),
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


def test_build_search_result_projection_only_keeps_sanitized_internal_metadata() -> (
    None
):
    projection = websearch.build_search_result_projection(
        query="latest ai news",
        provider=WebProvider.EXA,
        hits=(
            websearch.WebSearchHit(
                title="Python Docs",
                url="https://docs.python.org",
            ),
        ),
        duration_ms=42,
        endpoint_host="mcp.exa.ai",
        upstream_tool="web_search_advanced_exa",
        upstream_search_time=1.25,
    )

    assert projection.visible_data == {
        "query": "latest ai news",
        "provider": "exa",
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
        "endpoint_host": "mcp.exa.ai",
        "upstream_tool": "web_search_advanced_exa",
        "upstream_search_time": 1.25,
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


def test_extract_search_response_scans_later_sse_frames_for_real_results() -> None:
    response_text = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"{\\"results\\":"}]}}\n'
        "event: message\n"
        'data: {"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"{\\"results\\":[{\\"title\\":\\"Python Docs\\",\\"url\\":\\"https://docs.python.org\\"}],\\"searchTime\\":1.25}","_meta":{"searchTime":1.25}}]}}\n'
    )

    extracted = websearch.extract_search_response(response_text)

    assert extracted.upstream_search_time == 1.25
    assert len(extracted.hits) == 1
    assert extracted.hits[0].title == "Python Docs"
    assert extracted.hits[0].url == "https://docs.python.org"


def test_extract_search_response_skips_invalid_sse_frames_before_valid_results() -> (
    None
):
    response_text = (
        "event: message\n"
        "data: [DONE]\n"
        "event: message\n"
        'data: {"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"{\\"results\\":[{\\"title\\":\\"Python Docs\\",\\"url\\":\\"https://docs.python.org\\"}],\\"searchTime\\":1.25}","_meta":{"searchTime":1.25}}]}}\n'
    )

    extracted = websearch.extract_search_response(response_text)

    assert extracted.upstream_search_time == 1.25
    assert len(extracted.hits) == 1
    assert extracted.hits[0].title == "Python Docs"
    assert extracted.hits[0].url == "https://docs.python.org"


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
