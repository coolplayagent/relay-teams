# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import cast

import httpx
import pytest

from agent_teams.tools.runtime import ToolExecutionError
from agent_teams.tools.web_tools import common, webfetch


def _dict_list(value: object) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], value)


ATOM_FEED = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom Feed</title>
  <link href="https://example.com/articles/" rel="alternate"/>
  <link href="/atom.xml" rel="self"/>
  <updated>2026-03-27T00:35:01+00:00</updated>
  <entry>
    <title>First entry</title>
    <link href="/posts/first" rel="alternate"/>
    <published>2026-03-26T10:00:00+00:00</published>
    <updated>2026-03-26T10:00:00+00:00</updated>
    <summary type="html">&lt;p&gt;Hello &lt;strong&gt;world&lt;/strong&gt;.&lt;/p&gt;</summary>
  </entry>
  <entry>
    <title>Second entry</title>
    <link href="https://example.com/posts/second" rel="alternate"/>
    <published>2026-03-25T10:00:00+00:00</published>
    <updated>2026-03-25T10:00:00+00:00</updated>
    <summary>Plain text summary</summary>
  </entry>
</feed>
"""

RSS_FEED = """\
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Example RSS Feed</title>
    <link>https://example.com/</link>
    <atom:link href="https://example.com/feed.xml" rel="self" type="application/rss+xml"/>
    <lastBuildDate>Thu, 27 Mar 2026 00:35:01 GMT</lastBuildDate>
    <item>
      <title>RSS item</title>
      <link>/rss-item</link>
      <pubDate>Thu, 27 Mar 2026 00:35:01 GMT</pubDate>
      <description>&lt;p&gt;Item &lt;em&gt;summary&lt;/em&gt;&lt;/p&gt;</description>
    </item>
  </channel>
</rss>
"""

OPML_DOCUMENT = """\
<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head>
    <title>Reader Sources</title>
  </head>
  <body>
    <outline text="AI">
      <outline
        type="rss"
        text="Simon Willison"
        title="simonwillison.net"
        xmlUrl="https://simonwillison.net/atom/everything/"
        htmlUrl="https://simonwillison.net/"
      />
    </outline>
    <outline
      type="rss"
      text="Example Feed"
      title="Example Feed"
      xmlUrl="/feeds/example.xml"
      htmlUrl="/"
    />
  </body>
</opml>
"""


def test_validate_web_url_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="http:// or https://"):
        webfetch.validate_web_url("file:///tmp/demo")


def test_normalize_timeout_seconds_caps_at_maximum() -> None:
    assert webfetch.normalize_timeout_seconds(None) == 30
    assert webfetch.normalize_timeout_seconds(999) == 120


def test_normalize_item_limit_caps_at_maximum() -> None:
    assert webfetch.normalize_item_limit(1) == 1
    assert webfetch.normalize_item_limit(999) == webfetch.MAX_ITEM_LIMIT


def test_build_accept_header_changes_by_format() -> None:
    assert "text/markdown" in webfetch.build_accept_header("markdown")
    assert "text/plain" in webfetch.build_accept_header("text")
    assert "text/html" in webfetch.build_accept_header("html")


def test_is_textual_content_type_supports_feed_media_types() -> None:
    assert webfetch.is_textual_content_type("application/rss+xml") is True
    assert webfetch.is_textual_content_type("application/atom+xml") is True
    assert webfetch.is_textual_content_type("application/opml+xml") is True
    assert webfetch.is_binary_response("application/rss+xml") is False


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


@pytest.mark.asyncio
async def test_fetch_url_raises_anti_bot_challenge_after_retry() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            request=request,
            headers={"cf-mitigated": "challenge"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(ToolExecutionError) as exc_info:
            await webfetch.fetch_url(
                client=client,
                url="https://example.com",
                response_format="markdown",
            )
    finally:
        await client.aclose()

    assert exc_info.value.error_type == "anti_bot_challenge"
    assert exc_info.value.retryable is False
    assert exc_info.value.details == {
        "url_host": "example.com",
        "status_code": 403,
        "mitigation": "cloudflare_challenge",
    }


@pytest.mark.asyncio
async def test_fetch_url_classifies_upstream_status_errors() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request, text="unavailable")

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(ToolExecutionError) as exc_info:
            await webfetch.fetch_url(
                client=client,
                url="https://example.com",
                response_format="markdown",
            )
    finally:
        await client.aclose()

    assert exc_info.value.error_type == "upstream_unavailable"
    assert exc_info.value.retryable is True
    assert exc_info.value.details == {
        "url_host": "example.com",
        "status_code": 503,
    }


def test_build_webfetch_projection_saves_binary_file(tmp_path: Path) -> None:
    projection = webfetch.build_webfetch_projection(
        workspace_dir=tmp_path,
        tool_call_id="call_1",
        requested_url="https://example.com/image.png",
        final_url="https://example.com/image.png",
        response_format="markdown",
        content_type="image/png",
        body=b"png",
        extract=webfetch.WebFetchExtractMode.NONE,
        item_limit=webfetch.DEFAULT_ITEM_LIMIT,
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
        extract=webfetch.WebFetchExtractMode.NONE,
        item_limit=webfetch.DEFAULT_ITEM_LIMIT,
    )

    assert projection.visible_data is not None
    data = projection.visible_data
    assert isinstance(data, dict)
    assert data["truncated"] is True
    assert Path(str(data["saved_path"])).exists()


def test_build_webfetch_projection_parses_atom_feed(tmp_path: Path) -> None:
    projection = webfetch.build_webfetch_projection(
        workspace_dir=tmp_path,
        tool_call_id="call_3",
        requested_url="https://example.com/atom.xml",
        final_url="https://example.com/atom.xml",
        response_format="markdown",
        content_type="application/atom+xml",
        body=ATOM_FEED.encode("utf-8"),
        extract=webfetch.WebFetchExtractMode.FEED,
        item_limit=1,
    )

    assert projection.visible_data is not None
    data = projection.visible_data
    assert isinstance(data, dict)
    assert data["kind"] == "feed"
    assert data["title"] == "Example Atom Feed"
    assert data["feed_url"] == "https://example.com/atom.xml"
    assert data["site_url"] == "https://example.com/articles/"
    assert data["count"] == 1
    assert data["total_count"] == 2
    assert data["truncated"] is True
    assert data["output"] == 'Parsed feed "Example Atom Feed" with 1 of 2 entries.'
    entries = _dict_list(data["entries"])
    assert entries[0]["link"] == "https://example.com/posts/first"
    assert entries[0]["summary"] == "Hello world."


def test_build_webfetch_projection_parses_rss_feed(tmp_path: Path) -> None:
    projection = webfetch.build_webfetch_projection(
        workspace_dir=tmp_path,
        tool_call_id="call_4",
        requested_url="https://example.com/feed.xml",
        final_url="https://example.com/feed.xml",
        response_format="markdown",
        content_type="application/rss+xml",
        body=RSS_FEED.encode("utf-8"),
        extract=webfetch.WebFetchExtractMode.FEED,
        item_limit=5,
    )

    assert projection.visible_data is not None
    data = projection.visible_data
    assert isinstance(data, dict)
    assert data["title"] == "Example RSS Feed"
    assert data["feed_url"] == "https://example.com/feed.xml"
    assert data["site_url"] == "https://example.com/"
    entries = _dict_list(data["entries"])
    assert entries[0]["link"] == "https://example.com/rss-item"
    assert entries[0]["summary"] == "Item summary"


def test_build_webfetch_projection_parses_opml_document(tmp_path: Path) -> None:
    projection = webfetch.build_webfetch_projection(
        workspace_dir=tmp_path,
        tool_call_id="call_5",
        requested_url="https://example.com/feeds.opml",
        final_url="https://example.com/feeds.opml",
        response_format="markdown",
        content_type="text/xml",
        body=OPML_DOCUMENT.encode("utf-8"),
        extract=webfetch.WebFetchExtractMode.OPML,
        item_limit=10,
    )

    assert projection.visible_data is not None
    data = projection.visible_data
    assert isinstance(data, dict)
    assert data["kind"] == "opml"
    assert data["title"] == "Reader Sources"
    assert data["count"] == 2
    feeds = _dict_list(data["feeds"])
    assert feeds[0]["xml_url"] == "https://simonwillison.net/atom/everything/"
    assert feeds[0]["group_path"] == ["AI"]
    assert feeds[1]["xml_url"] == "https://example.com/feeds/example.xml"
    assert feeds[1]["html_url"] == "https://example.com/"


def test_build_webfetch_projection_requires_matching_extract_mode(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="not an OPML document"):
        webfetch.build_webfetch_projection(
            workspace_dir=tmp_path,
            tool_call_id="call_6",
            requested_url="https://example.com/feed.xml",
            final_url="https://example.com/feed.xml",
            response_format="markdown",
            content_type="application/rss+xml",
            body=RSS_FEED.encode("utf-8"),
            extract=webfetch.WebFetchExtractMode.OPML,
            item_limit=10,
        )


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
        tool_call_id="call_7",
        requested_url="https://example.com/docs",
        final_url="https://example.com/docs/getting-started",
        response_format="markdown",
        content_type="text/html",
        body=b'<html><body><p><a href="../install">Install</a></p></body></html>',
        extract=webfetch.WebFetchExtractMode.NONE,
        item_limit=webfetch.DEFAULT_ITEM_LIMIT,
    )

    assert projection.visible_data is not None
    data = projection.visible_data
    assert isinstance(data, dict)
    assert data["output"] == "[Install](https://example.com/install)"
