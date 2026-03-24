# -*- coding: utf-8 -*-
from __future__ import annotations

import httpx
from typing import cast
import pytest

from agent_teams.tools.web_tools import websearch


def test_build_exa_search_request_uses_hardcoded_defaults() -> None:
    payload = websearch.build_exa_search_request(
        query="latest ai news",
        num_results=None,
        livecrawl=None,
        search_type=None,
        context_max_characters=None,
    )

    params = cast(dict[str, object], payload["params"])
    assert params["name"] == "web_search_exa"
    arguments = cast(dict[str, object], params["arguments"])
    assert arguments["query"] == "latest ai news"
    assert arguments["numResults"] == 8
    assert arguments["livecrawl"] == "fallback"
    assert arguments["type"] == "auto"


def test_build_exa_search_url_appends_optional_api_key() -> None:
    assert websearch.build_exa_search_url(api_key=None) == "https://mcp.exa.ai/mcp"
    assert (
        websearch.build_exa_search_url(api_key="secret")
        == "https://mcp.exa.ai/mcp?exaApiKey=secret"
    )


def test_extract_search_output_reads_first_sse_text_block() -> None:
    response_text = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"answer"}]}}\n'
    )

    assert websearch.extract_search_output(response_text) == "answer"


@pytest.mark.asyncio
async def test_fetch_exa_search_response_wraps_http_errors() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request, text="boom")

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(RuntimeError, match="Search error \\(500\\): boom"):
            await websearch.fetch_exa_search_response(
                client=client,
                endpoint="https://mcp.exa.ai/mcp",
                payload={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {},
                },
            )
    finally:
        await client.aclose()
