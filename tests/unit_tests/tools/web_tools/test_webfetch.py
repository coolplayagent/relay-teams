# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agent_teams.tools.web_tools import webfetch


def test_validate_web_url_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="http:// or https://"):
        webfetch.validate_web_url("file:///tmp/demo")


def test_normalize_timeout_seconds_caps_at_maximum() -> None:
    assert webfetch.normalize_timeout_seconds(None) == 30
    assert webfetch.normalize_timeout_seconds(999) == 120


def test_build_accept_header_changes_by_format() -> None:
    assert "text/markdown" in webfetch.build_accept_header("markdown")
    assert "text/plain" in webfetch.build_accept_header("text")
    assert "text/html" in webfetch.build_accept_header("html")


@pytest.mark.asyncio
async def test_fetch_url_retries_cloudflare_challenge() -> None:
    calls: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["User-Agent"])
        if len(calls) == 1:
            return httpx.Response(
                403,
                request=request,
                headers={"cf-mitigated": "challenge"},
            )
        return httpx.Response(
            200,
            request=request,
            text="<html><body>Hello</body></html>",
            headers={"content-type": "text/html"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        response = await webfetch.fetch_url(
            client=client,
            url="https://example.com",
            response_format="markdown",
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert calls == [webfetch.BROWSER_USER_AGENT, webfetch.FALLBACK_USER_AGENT]


def test_build_webfetch_projection_saves_binary_file(tmp_path: Path) -> None:
    projection = webfetch.build_webfetch_projection(
        workspace_dir=tmp_path,
        tool_call_id="call_1",
        requested_url="https://example.com/image.png",
        final_url="https://example.com/image.png",
        response_format="markdown",
        content_type="image/png",
        body=b"png",
    )

    assert projection.visible_data is not None
    data = projection.visible_data
    assert isinstance(data, dict)
    assert data["mime_type"] == "image/png"
    saved_path = Path(str(data["saved_path"]))
    assert saved_path.exists()
    assert saved_path.parent == tmp_path / "tmp" / "webfetch"


def test_build_webfetch_projection_truncates_large_text_output(tmp_path: Path) -> None:
    projection = webfetch.build_webfetch_projection(
        workspace_dir=tmp_path,
        tool_call_id="call_2",
        requested_url="https://example.com/page",
        final_url="https://example.com/page",
        response_format="text",
        content_type="text/plain",
        body=("a" * (webfetch.MAX_TEXT_OUTPUT_CHARS + 10)).encode("utf-8"),
    )

    assert projection.visible_data is not None
    data = projection.visible_data
    assert isinstance(data, dict)
    assert data["truncated"] is True
    assert Path(str(data["saved_path"])).exists()
