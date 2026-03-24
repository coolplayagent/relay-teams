# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agent_teams.tools.web_tools import common, webfetch


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


def test_convert_html_to_markdown_preserves_nested_links_and_absolutizes_urls() -> None:
    markdown = common.convert_html_to_markdown(
        """
        <html>
            <body>
                <h1><a href="/guide">Guide</a></h1>
                <ul>
                    <li><a href="/docs/start"><strong>Start here</strong></a></li>
                </ul>
                <p>See the <a href="topics/advanced">advanced guide</a>.</p>
                <img src="/assets/logo.png" alt="Logo">
                <script>window.alert('ignore');</script>
            </body>
        </html>
        """,
        base_url="https://example.com/reference/index.html",
    )

    assert "# [Guide](https://example.com/guide)" in markdown
    assert "- [**Start here**](https://example.com/docs/start)" in markdown
    assert "[advanced guide](https://example.com/reference/topics/advanced)" in markdown
    assert "![Logo](https://example.com/assets/logo.png)" in markdown
    assert "ignore" not in markdown
    assert "\n\n\n" not in markdown


def test_build_webfetch_projection_uses_absolute_markdown_links(tmp_path: Path) -> None:
    projection = webfetch.build_webfetch_projection(
        workspace_dir=tmp_path,
        tool_call_id="call_3",
        requested_url="https://example.com/docs",
        final_url="https://example.com/docs/getting-started",
        response_format="markdown",
        content_type="text/html",
        body=b'<html><body><p><a href="../install">Install</a></p></body></html>',
    )

    assert projection.visible_data is not None
    data = projection.visible_data
    assert isinstance(data, dict)
    assert data["output"] == "[Install](https://example.com/install)"
