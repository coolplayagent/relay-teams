# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Awaitable, Callable, cast

import httpx
from pydantic_ai import Agent
import pytest

from relay_teams.computer import ComputerActionRisk
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.tools.runtime import ToolApprovalPolicy, ToolDeps, ToolExecutionError
from relay_teams.tools.web_tools import common, webfetch
from relay_teams.tools.web_tools.preapproved import is_preapproved_webfetch_url


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

LAST_MODIFIED = "Mon, 03 Nov 2025 15:03:56 GMT"


class _InterruptingStream(httpx.AsyncByteStream):
    def __init__(
        self,
        *,
        data: bytes,
        error: httpx.RequestError,
    ) -> None:
        self._data = data
        self._error = error

    async def __aiter__(self):
        if self._data:
            yield self._data
        raise self._error

    async def aclose(self) -> None:
        return None


class _StaticStream(httpx.AsyncByteStream):
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self):
        if self._data:
            yield self._data

    async def aclose(self) -> None:
        return None


def _build_shared_store(tmp_path: Path) -> SharedStateRepository:
    return SharedStateRepository(tmp_path / "shared_state.db")


def _make_binary_bytes(size: int) -> bytes:
    repeat = (size // 16) + 1
    return (b"0123456789abcdef" * repeat)[:size]


def _parse_range_header(value: str, total_size: int) -> tuple[int, int]:
    assert value.startswith("bytes=")
    start_text, end_text = value[6:].split("-", 1)
    start = int(start_text)
    end = int(end_text) if end_text else total_size - 1
    return start, end


def _build_binary_transport(
    *,
    data: bytes,
    etag: str | None = '"etag-1"',
    last_modified: str | None = LAST_MODIFIED,
    ignore_range_probe: bool = False,
    reject_range_probe_status: int | None = None,
    full_range_returns_200: bool = False,
    fail_once_ranges: dict[str, int] | None = None,
    fail_once_range_statuses: dict[str, int] | None = None,
    request_log: list[str] | None = None,
    if_range_log: list[str] | None = None,
) -> httpx.MockTransport:
    remaining_failures = {} if fail_once_ranges is None else dict(fail_once_ranges)
    remaining_status_failures = (
        {} if fail_once_range_statuses is None else dict(fail_once_range_statuses)
    )

    async def _handler(request: httpx.Request) -> httpx.Response:
        range_header = request.headers.get("Range")
        if request_log is not None:
            request_log.append(range_header or "")
        if if_range_log is not None:
            if_range_log.append(request.headers.get("If-Range") or "")
        base_headers = {
            "content-type": "application/pdf",
            "content-length": str(len(data)),
            "accept-ranges": "bytes",
        }
        if etag is not None:
            base_headers["etag"] = etag
        if last_modified is not None:
            base_headers["last-modified"] = last_modified
        if (
            reject_range_probe_status is not None
            and range_header == webfetch.RANGE_PROBE_HEADER_VALUE
        ):
            return httpx.Response(
                reject_range_probe_status,
                request=request,
                headers=base_headers,
            )
        if ignore_range_probe and range_header == webfetch.RANGE_PROBE_HEADER_VALUE:
            return httpx.Response(
                200,
                request=request,
                headers=base_headers,
                content=data,
            )
        if full_range_returns_200 and range_header == f"bytes=0-{len(data) - 1}":
            return httpx.Response(
                200,
                request=request,
                headers=base_headers,
                content=data,
            )
        if range_header:
            if (
                range_header in remaining_status_failures
                and remaining_status_failures[range_header] > 0
            ):
                status_code = remaining_status_failures.pop(range_header)
                return httpx.Response(
                    status_code,
                    request=request,
                    headers=base_headers,
                )
            start, end = _parse_range_header(range_header, len(data))
            chunk = data[start : end + 1]
            headers = dict(base_headers)
            headers["content-range"] = f"bytes {start}-{end}/{len(data)}"
            headers["content-length"] = str(len(chunk))
            if (
                range_header in remaining_failures
                and remaining_failures[range_header] > 0
            ):
                fail_after = remaining_failures.pop(range_header)
                partial = chunk[:fail_after]
                return httpx.Response(
                    206,
                    request=request,
                    headers=headers,
                    stream=_InterruptingStream(
                        data=partial,
                        error=httpx.ReadError("stream interrupted", request=request),
                    ),
                )
            return httpx.Response(
                206,
                request=request,
                headers=headers,
                content=chunk,
            )
        return httpx.Response(
            200,
            request=request,
            headers=base_headers,
            content=data,
        )

    return httpx.MockTransport(_handler)


def test_validate_web_url_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="http:// or https://"):
        webfetch.validate_web_url("file:///tmp/demo")


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("https://user:pass@example.com", "username or password"),
        ("https://localhost/app", "publicly reachable"),
        ("https://printer/dashboard", "fully qualified domain name"),
        ("https://10.0.0.5/config", "private or local address"),
        ("https://169.254.169.254/latest/meta-data", "private or local address"),
    ],
)
def test_validate_web_url_rejects_local_or_credentialed_targets(
    url: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        webfetch.validate_web_url(url)


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


def test_build_accept_header_prefers_markdown_for_markdown_format() -> None:
    header = webfetch.build_accept_header("markdown")

    assert header.startswith("text/markdown;q=1.0, text/x-markdown;q=0.9")
    assert header.index("text/markdown") < header.index("text/plain")
    assert header.index("text/markdown") < header.index("text/html")


def test_is_textual_content_type_supports_feed_media_types() -> None:
    assert webfetch.is_textual_content_type("application/rss+xml") is True
    assert webfetch.is_textual_content_type("application/atom+xml") is True
    assert webfetch.is_textual_content_type("application/opml+xml") is True
    assert webfetch.is_binary_response("application/rss+xml") is False


def test_is_textual_content_type_supports_markdown_media_types() -> None:
    assert webfetch.is_textual_content_type("text/markdown") is True
    assert webfetch.is_textual_content_type("text/x-markdown") is True
    assert webfetch.is_markdown_content_type("text/markdown") is True
    assert webfetch.is_markdown_content_type("text/x-markdown") is True


def test_preapproved_webfetch_url_honors_path_boundaries() -> None:
    assert is_preapproved_webfetch_url("https://docs.python.org/3/library/pathlib.html")
    assert is_preapproved_webfetch_url("https://github.com/anthropics/claude-code")
    assert not is_preapproved_webfetch_url(
        "https://github.com/anthropics-evil/claude-code"
    )


def test_webfetch_description_mentions_binary_download_support() -> None:
    assert "Downloading network files such as PDFs" in webfetch.DESCRIPTION
    assert "can resume on later `webfetch` calls" in webfetch.DESCRIPTION
    assert "shared `net` HTTP client" in webfetch.DESCRIPTION


def test_build_webfetch_approval_request_marks_preapproved_hosts_safe() -> None:
    request = webfetch.build_webfetch_approval_request("https://docs.python.org/3/")
    decision = ToolApprovalPolicy().evaluate("webfetch", request)

    assert request.risk_level == ComputerActionRisk.SAFE
    assert request.target_summary == "docs.python.org"
    assert request.source == "https://docs.python.org/3/"
    assert decision.required is False


@pytest.mark.asyncio
async def test_fetch_url_sends_markdown_accept_header() -> None:
    accept_headers: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        accept_headers.append(request.headers["Accept"])
        return httpx.Response(
            200,
            request=request,
            text="# Upstream Markdown",
            headers={"content-type": "text/markdown; charset=utf-8"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        response = await webfetch.fetch_url(
            client=client,
            url="https://example.com/docs",
            response_format="markdown",
        )
    finally:
        await client.aclose()

    assert accept_headers == [webfetch.build_accept_header("markdown")]
    await response.aclose()


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
    await response.aclose()


@pytest.mark.asyncio
async def test_fetch_url_uses_reader_fallback_for_anti_bot_challenge() -> None:
    requested_urls: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url) == "https://example.com/protected":
            return httpx.Response(
                403,
                request=request,
                text="<html><body>Enable JavaScript and cookies to continue</body></html>",
                headers={"content-type": "text/html"},
            )
        assert str(request.url) == webfetch.build_reader_fallback_url(
            "https://example.com/protected"
        )
        assert request.headers["X-Respond-With"] == "markdown"
        return httpx.Response(
            200,
            request=request,
            text="# Reader Result",
            headers={"content-type": "text/plain; charset=utf-8"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        response = await webfetch.fetch_url(
            client=client,
            url="https://example.com/protected",
            response_format="markdown",
        )
    finally:
        await client.aclose()

    assert response.text == "# Reader Result"
    assert (
        webfetch.resolve_webfetch_response_url(response)
        == "https://example.com/protected"
    )
    assert requested_urls == [
        "https://example.com/protected",
        webfetch.build_reader_fallback_url("https://example.com/protected"),
    ]
    await response.aclose()


@pytest.mark.asyncio
async def test_fetch_url_uses_reader_fallback_for_streaming_anti_bot_challenge() -> (
    None
):
    async def _handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/protected":
            return httpx.Response(
                403,
                request=request,
                headers={"content-type": "text/html"},
                stream=_StaticStream(
                    b"<html><body>Enable JavaScript and cookies to continue</body></html>"
                ),
            )
        return httpx.Response(
            200,
            request=request,
            text="# Reader Result",
            headers={"content-type": "text/plain; charset=utf-8"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        response = await webfetch.fetch_url(
            client=client,
            url="https://example.com/protected",
            response_format="markdown",
        )
    finally:
        await client.aclose()

    assert response.text == "# Reader Result"
    await response.aclose()


@pytest.mark.asyncio
async def test_fetch_url_follows_same_host_redirect() -> None:
    requested_urls: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url) == "https://example.com/start":
            return httpx.Response(
                302,
                request=request,
                headers={"location": "/finish"},
            )
        return httpx.Response(
            200,
            request=request,
            text="ok",
            headers={"content-type": "text/plain"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        response = await webfetch.fetch_url(
            client=client,
            url="https://example.com/start",
            response_format="text",
        )
    finally:
        await client.aclose()

    assert requested_urls == ["https://example.com/start", "https://example.com/finish"]
    assert str(response.url) == "https://example.com/finish"
    await response.aclose()


@pytest.mark.asyncio
async def test_fetch_url_follows_http_to_https_same_host_redirect() -> None:
    requested_urls: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url) == "http://example.com/start":
            return httpx.Response(
                301,
                request=request,
                headers={"location": "https://example.com/finish"},
            )
        return httpx.Response(
            200,
            request=request,
            text="secure ok",
            headers={"content-type": "text/plain"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        response = await webfetch.fetch_url(
            client=client,
            url="http://example.com/start",
            response_format="text",
        )
    finally:
        await client.aclose()

    assert requested_urls == ["http://example.com/start", "https://example.com/finish"]
    assert str(response.url) == "https://example.com/finish"
    await response.aclose()


@pytest.mark.asyncio
async def test_fetch_url_follows_https_default_port_same_host_redirect() -> None:
    requested_urls: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url) == "https://example.com/start":
            return httpx.Response(
                302,
                request=request,
                headers={"location": "https://example.com:443/finish"},
            )
        return httpx.Response(
            200,
            request=request,
            text="ok",
            headers={"content-type": "text/plain"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        response = await webfetch.fetch_url(
            client=client,
            url="https://example.com/start",
            response_format="text",
        )
    finally:
        await client.aclose()

    assert requested_urls == ["https://example.com/start", "https://example.com/finish"]
    assert str(response.url) == "https://example.com/finish"
    await response.aclose()


@pytest.mark.asyncio
async def test_fetch_url_follows_same_host_html_redirect_without_location_header() -> (
    None
):
    requested_urls: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url) == "https://www.huawei.com/en/annual-report/2020":
            return httpx.Response(
                301,
                request=request,
                content=(
                    "<!DOCTYPE html>"
                    '<html><head><meta http-equiv="refresh" '
                    'content="0; url=https://www.huawei.com/en/annual-report">'
                    "<script>window.location.href = "
                    '"https://www.huawei.com/en/annual-report"</script>'
                    "</head></html>"
                ),
                headers={"content-type": "text/html;charset=utf-8"},
            )
        return httpx.Response(
            200,
            request=request,
            text="annual report",
            headers={"content-type": "text/plain"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        response = await webfetch.fetch_url(
            client=client,
            url="https://www.huawei.com/en/annual-report/2020",
            response_format="text",
        )
    finally:
        await client.aclose()

    assert requested_urls == [
        "https://www.huawei.com/en/annual-report/2020",
        "https://www.huawei.com/en/annual-report",
    ]
    assert str(response.url) == "https://www.huawei.com/en/annual-report"
    await response.aclose()


@pytest.mark.asyncio
async def test_fetch_url_requires_explicit_follow_up_for_cross_host_html_redirect_without_location_header() -> (
    None
):
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            301,
            request=request,
            content=(
                "<!DOCTYPE html>"
                '<html><head><meta http-equiv="refresh" '
                'content="0; url=https://docs.python.org/3/tutorial/">'
                "</head></html>"
            ),
            headers={"content-type": "text/html;charset=utf-8"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(ToolExecutionError) as exc_info:
            await webfetch.fetch_url(
                client=client,
                url="https://example.com/start",
                response_format="text",
            )
    finally:
        await client.aclose()

    assert exc_info.value.error_type == "redirect_required"
    assert exc_info.value.details == {
        "original_url": "https://example.com/start",
        "redirect_url": "https://docs.python.org/3/tutorial/",
        "status_code": 301,
    }


@pytest.mark.asyncio
async def test_fetch_url_wraps_redirect_body_read_errors_as_tool_errors() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            301,
            request=request,
            headers={"content-type": "text/html;charset=utf-8"},
            stream=_InterruptingStream(
                data=b"<!DOCTYPE html>",
                error=httpx.ReadError("stream interrupted", request=request),
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(ToolExecutionError) as exc_info:
            await webfetch.fetch_url(
                client=client,
                url="https://example.com/start",
                response_format="text",
            )
    finally:
        await client.aclose()

    assert exc_info.value.error_type == "network_error"
    assert exc_info.value.retryable is True
    assert exc_info.value.details == {"url_host": "example.com"}


@pytest.mark.asyncio
async def test_fetch_url_parses_redirect_from_buffered_prefix_at_size_cap() -> None:
    requested_urls: list[str] = []
    redirect_prefix = (
        "<!DOCTYPE html>"
        '<html><head><meta http-equiv="refresh" '
        'content="0; url=https://example.com/finish">'
        "</head><body>"
    )
    redirect_body = (
        redirect_prefix
        + ("x" * (webfetch.MAX_REDIRECT_BODY_BYTES - len(redirect_prefix)))
    ).encode("utf-8")

    async def _handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url) == "https://example.com/start":
            return httpx.Response(
                301,
                request=request,
                headers={"content-type": "text/html;charset=utf-8"},
                content=redirect_body,
            )
        return httpx.Response(
            200,
            request=request,
            text="ok",
            headers={"content-type": "text/plain"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        response = await webfetch.fetch_url(
            client=client,
            url="https://example.com/start",
            response_format="text",
        )
    finally:
        await client.aclose()

    assert requested_urls == ["https://example.com/start", "https://example.com/finish"]
    assert str(response.url) == "https://example.com/finish"
    await response.aclose()


@pytest.mark.asyncio
async def test_fetch_url_rejects_http_to_https_non_default_ports() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "http://example.com:8080/start":
            return httpx.Response(
                301,
                request=request,
                headers={"location": "https://example.com:8443/finish"},
            )
        return httpx.Response(
            200,
            request=request,
            text="unexpected",
            headers={"content-type": "text/plain"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(ToolExecutionError) as exc_info:
            await webfetch.fetch_url(
                client=client,
                url="http://example.com:8080/start",
                response_format="text",
            )
    finally:
        await client.aclose()

    assert exc_info.value.error_type == "redirect_required"
    assert exc_info.value.details == {
        "original_url": "http://example.com:8080/start",
        "redirect_url": "https://example.com:8443/finish",
        "status_code": 301,
    }


@pytest.mark.asyncio
async def test_fetch_url_classifies_invalid_redirect_port_as_upstream_error() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            request=request,
            headers={"location": "https://example.com:99999/finish"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(ToolExecutionError) as exc_info:
            await webfetch.fetch_url(
                client=client,
                url="https://example.com/start",
                response_format="text",
            )
    finally:
        await client.aclose()

    assert exc_info.value.error_type == "upstream_error"
    assert exc_info.value.retryable is False
    assert exc_info.value.details == {
        "url_host": "example.com",
        "redirect_url": "https://example.com:99999/finish",
        "invalid_port": True,
    }


@pytest.mark.asyncio
async def test_fetch_url_raises_anti_bot_challenge_when_reader_fallback_fails() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com":
            return httpx.Response(
                403,
                request=request,
                headers={"cf-mitigated": "challenge"},
            )
        return httpx.Response(
            502,
            request=request,
            text="bad gateway",
            headers={"content-type": "text/plain"},
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
        "mitigation": "reader_fallback_failed",
        "fallback_status_code": 502,
    }


@pytest.mark.asyncio
async def test_fetch_url_rechecks_redirects_after_cloudflare_retry() -> None:
    calls: list[tuple[str, str]] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        request_url = str(request.url)
        user_agent = request.headers["User-Agent"]
        calls.append((request_url, user_agent))
        if (
            request_url == "https://example.com/start"
            and user_agent == webfetch.BROWSER_USER_AGENT
        ):
            return httpx.Response(
                403,
                request=request,
                headers={"cf-mitigated": "challenge"},
            )
        if (
            request_url == "https://example.com/start"
            and user_agent == webfetch.FALLBACK_USER_AGENT
        ):
            return httpx.Response(
                302,
                request=request,
                headers={"location": "/finish"},
            )
        return httpx.Response(
            200,
            request=request,
            text="ok",
            headers={"content-type": "text/plain"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        response = await webfetch.fetch_url(
            client=client,
            url="https://example.com/start",
            response_format="text",
        )
    finally:
        await client.aclose()

    assert calls == [
        ("https://example.com/start", webfetch.BROWSER_USER_AGENT),
        ("https://example.com/start", webfetch.FALLBACK_USER_AGENT),
        ("https://example.com/finish", webfetch.FALLBACK_USER_AGENT),
    ]
    assert str(response.url) == "https://example.com/finish"
    await response.aclose()


@pytest.mark.asyncio
async def test_fetch_url_classifies_egress_blocked_proxy_errors() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            request=request,
            headers={"x-proxy-error": "blocked-by-allowlist"},
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

    assert exc_info.value.error_type == "egress_blocked"
    assert exc_info.value.retryable is False
    assert exc_info.value.details == {
        "url_host": "example.com",
        "status_code": 403,
        "proxy_error": "blocked-by-allowlist",
    }


@pytest.mark.asyncio
async def test_fetch_url_classifies_source_access_denied() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            request=request,
            text="Forbidden",
            headers={"content-type": "text/plain"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(ToolExecutionError) as exc_info:
            await webfetch.fetch_url(
                client=client,
                url="https://example.com/forbidden",
                response_format="text",
            )
    finally:
        await client.aclose()

    assert exc_info.value.error_type == "source_access_denied"
    assert exc_info.value.retryable is False
    assert exc_info.value.details == {
        "url_host": "example.com",
        "status_code": 403,
    }


@pytest.mark.asyncio
async def test_fetch_url_classifies_enterprise_proxy_blocks() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            text="HIS Proxy Notification",
            headers={"content-type": "text/html"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(ToolExecutionError) as exc_info:
            await webfetch.fetch_url(
                client=client,
                url=(
                    "http://114.114.114.114:9421/proxycontrolwarn/httpwarning_2907.html"
                ),
                response_format="markdown",
            )
    finally:
        await client.aclose()

    assert exc_info.value.error_type == "proxy_blocked"
    assert exc_info.value.retryable is False
    assert exc_info.value.details == {
        "url_host": "114.114.114.114:9421",
        "blocked_url": (
            "http://114.114.114.114:9421/proxycontrolwarn/httpwarning_2907.html"
        ),
    }


@pytest.mark.asyncio
async def test_fetch_url_classifies_tunnel_failures() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ProxyError("ERR_TUNNEL_CONNECTION_FAILED", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(ToolExecutionError) as exc_info:
            await webfetch.fetch_url(
                client=client,
                url="https://example.com/protected",
                response_format="markdown",
            )
    finally:
        await client.aclose()

    assert exc_info.value.error_type == "tunnel_error"
    assert exc_info.value.retryable is True
    assert exc_info.value.details == {"url_host": "example.com"}


@pytest.mark.asyncio
async def test_fetch_url_classifies_generic_proxy_failures() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ProxyError("proxy refused connection", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(ToolExecutionError) as exc_info:
            await webfetch.fetch_url(
                client=client,
                url="https://example.com/protected",
                response_format="markdown",
            )
    finally:
        await client.aclose()

    assert exc_info.value.error_type == "proxy_error"
    assert exc_info.value.retryable is True
    assert exc_info.value.details == {"url_host": "example.com"}


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


@pytest.mark.asyncio
async def test_fetch_webfetch_projection_returns_redirect_result_for_cross_host_redirect(
    tmp_path: Path,
) -> None:
    request_log: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        request_log.append(str(request.url))
        return httpx.Response(
            302,
            request=request,
            headers={"location": "https://docs.python.org/3/tutorial/"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    shared_store = _build_shared_store(tmp_path)
    try:
        projection = await webfetch.fetch_webfetch_projection(
            client=client,
            requested_url="https://example.com/start",
            response_format="markdown",
            extract=webfetch.WebFetchExtractMode.NONE,
            item_limit=webfetch.DEFAULT_ITEM_LIMIT,
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            tool_call_id="webfetch",
            cancel_check=lambda: None,
        )
    finally:
        await client.aclose()

    assert projection.visible_data is not None
    data = cast(dict[str, object], projection.visible_data)
    assert data["redirect_required"] is True
    assert data["original_url"] == "https://example.com/start"
    assert data["redirect_url"] == "https://docs.python.org/3/tutorial/"
    assert data["status_code"] == 302
    assert request_log == ["https://example.com/start"]


@pytest.mark.asyncio
async def test_read_response_body_raises_when_stream_exceeds_text_limit() -> None:
    payload = b"a" * (webfetch.MAX_TEXT_RESPONSE_SIZE_BYTES + 1)

    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={
                "content-type": "text/plain",
            },
            content=payload,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        response = await webfetch.fetch_url(
            client=client,
            url="https://example.com/large.txt",
            response_format="text",
        )
        try:
            with pytest.raises(ToolExecutionError, match="5MB limit"):
                await webfetch.read_response_body(response)
        finally:
            await response.aclose()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_download_binary_response_streams_parallel_ranges_to_file(
    tmp_path: Path,
) -> None:
    payload = _make_binary_bytes(webfetch.PARALLEL_DOWNLOAD_THRESHOLD_BYTES + 8192)
    request_log: list[str] = []
    transport = _build_binary_transport(data=payload, request_log=request_log)
    client = httpx.AsyncClient(transport=transport)
    shared_store = _build_shared_store(tmp_path)
    try:
        projection = await webfetch.download_binary_response(
            client=client,
            requested_url="https://example.com/report.pdf",
            response_format="markdown",
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            cancel_check=lambda: None,
        )
    finally:
        await client.aclose()

    assert projection.visible_data is not None
    data = cast(dict[str, object], projection.visible_data)
    saved_path = Path(str(data["saved_path"]))
    assert saved_path.exists()
    assert saved_path.read_bytes() == payload
    assert data["download_mode"] == "streaming_ranges"
    assert data["streamed_to_disk"] is True
    assert data["range_supported"] is True
    assert data["resume_supported"] is True
    assert request_log.count(webfetch.RANGE_PROBE_HEADER_VALUE) == 1
    assert (
        len(
            [
                item
                for item in request_log
                if item and item != webfetch.RANGE_PROBE_HEADER_VALUE
            ]
        )
        == 4
    )


@pytest.mark.asyncio
async def test_fetch_webfetch_projection_avoids_preflight_binary_get(
    tmp_path: Path,
) -> None:
    payload = _make_binary_bytes(webfetch.PARALLEL_DOWNLOAD_THRESHOLD_BYTES + 8192)
    request_log: list[str] = []
    client = httpx.AsyncClient(
        transport=_build_binary_transport(data=payload, request_log=request_log)
    )
    shared_store = _build_shared_store(tmp_path)
    try:
        projection = await webfetch.fetch_webfetch_projection(
            client=client,
            requested_url="https://example.com/projection.pdf",
            response_format="markdown",
            extract=webfetch.WebFetchExtractMode.NONE,
            item_limit=webfetch.DEFAULT_ITEM_LIMIT,
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            tool_call_id="webfetch",
            cancel_check=lambda: None,
        )
    finally:
        await client.aclose()

    assert projection.visible_data is not None
    data = cast(dict[str, object], projection.visible_data)
    saved_path = Path(str(data["saved_path"]))
    assert saved_path.read_bytes() == payload
    assert request_log.count(webfetch.RANGE_PROBE_HEADER_VALUE) == 1
    assert "" not in request_log


@pytest.mark.asyncio
async def test_fetch_webfetch_projection_falls_back_when_probe_is_rejected(
    tmp_path: Path,
) -> None:
    payload = _make_binary_bytes(1024 * 1024)
    request_log: list[str] = []
    client = httpx.AsyncClient(
        transport=_build_binary_transport(
            data=payload,
            request_log=request_log,
            reject_range_probe_status=416,
        )
    )
    shared_store = _build_shared_store(tmp_path)
    try:
        projection = await webfetch.fetch_webfetch_projection(
            client=client,
            requested_url="https://example.com/probe-rejected.pdf",
            response_format="markdown",
            extract=webfetch.WebFetchExtractMode.NONE,
            item_limit=webfetch.DEFAULT_ITEM_LIMIT,
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            tool_call_id="webfetch",
            cancel_check=lambda: None,
        )
    finally:
        await client.aclose()

    assert projection.visible_data is not None
    data = cast(dict[str, object], projection.visible_data)
    saved_path = Path(str(data["saved_path"]))
    assert saved_path.read_bytes() == payload
    assert data["download_mode"] == "streaming"
    assert data["streamed_to_disk"] is True
    assert data["range_supported"] is False
    assert data["resume_supported"] is False
    assert request_log == [webfetch.RANGE_PROBE_HEADER_VALUE, ""]


@pytest.mark.asyncio
async def test_download_binary_response_resumes_across_calls(tmp_path: Path) -> None:
    payload = _make_binary_bytes(2 * 1024 * 1024)
    request_log: list[str] = []
    transport = _build_binary_transport(
        data=payload,
        request_log=request_log,
        fail_once_ranges={f"bytes=0-{len(payload) - 1}": 786432},
    )
    client = httpx.AsyncClient(transport=transport)
    shared_store = _build_shared_store(tmp_path)
    try:
        with pytest.raises(ToolExecutionError) as exc_info:
            await webfetch.download_binary_response(
                client=client,
                requested_url="https://example.com/resume.pdf",
                response_format="markdown",
                workspace_dir=tmp_path,
                workspace_id="workspace-1",
                shared_store=shared_store,
                cancel_check=lambda: None,
            )
        assert exc_info.value.error_type == "network_error"

        projection = await webfetch.download_binary_response(
            client=client,
            requested_url="https://example.com/resume.pdf",
            response_format="markdown",
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            cancel_check=lambda: None,
        )
    finally:
        await client.aclose()

    assert projection.visible_data is not None
    data = cast(dict[str, object], projection.visible_data)
    saved_path = Path(str(data["saved_path"]))
    assert saved_path.read_bytes() == payload
    assert data["download_mode"] == "streaming_ranges"
    assert data["streamed_to_disk"] is True
    assert data["range_supported"] is True
    assert data["resume_supported"] is True
    assert webfetch.RANGE_PROBE_HEADER_VALUE in request_log
    assert f"bytes=786432-{len(payload) - 1}" in request_log


@pytest.mark.asyncio
async def test_download_binary_response_does_not_resume_without_strong_validators(
    tmp_path: Path,
) -> None:
    payload = _make_binary_bytes(2 * 1024 * 1024)
    full_range = f"bytes=0-{len(payload) - 1}"
    request_log: list[str] = []
    transport = _build_binary_transport(
        data=payload,
        etag=None,
        last_modified=None,
        request_log=request_log,
        fail_once_ranges={full_range: 786432},
    )
    client = httpx.AsyncClient(transport=transport)
    shared_store = _build_shared_store(tmp_path)
    try:
        with pytest.raises(ToolExecutionError) as exc_info:
            await webfetch.download_binary_response(
                client=client,
                requested_url="https://example.com/no-validator-resume.pdf",
                response_format="markdown",
                workspace_dir=tmp_path,
                workspace_id="workspace-1",
                shared_store=shared_store,
                cancel_check=lambda: None,
            )
        assert exc_info.value.error_type == "network_error"

        projection = await webfetch.download_binary_response(
            client=client,
            requested_url="https://example.com/no-validator-resume.pdf",
            response_format="markdown",
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            cancel_check=lambda: None,
        )
    finally:
        await client.aclose()

    assert projection.visible_data is not None
    data = cast(dict[str, object], projection.visible_data)
    saved_path = Path(str(data["saved_path"]))
    assert saved_path.read_bytes() == payload
    assert request_log.count(full_range) == 2
    assert f"bytes=786432-{len(payload) - 1}" not in request_log


@pytest.mark.asyncio
async def test_download_binary_response_uses_last_modified_for_weak_etag_if_range(
    tmp_path: Path,
) -> None:
    payload = _make_binary_bytes(2 * 1024 * 1024)
    if_range_log: list[str] = []
    client = httpx.AsyncClient(
        transport=_build_binary_transport(
            data=payload,
            etag='W/"weak-etag"',
            last_modified=LAST_MODIFIED,
            if_range_log=if_range_log,
        )
    )
    shared_store = _build_shared_store(tmp_path)
    try:
        projection = await webfetch.download_binary_response(
            client=client,
            requested_url="https://example.com/weak-etag.pdf",
            response_format="markdown",
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            cancel_check=lambda: None,
        )
    finally:
        await client.aclose()

    assert projection.visible_data is not None
    assert LAST_MODIFIED in if_range_log
    assert 'W/"weak-etag"' not in if_range_log


@pytest.mark.asyncio
async def test_download_binary_response_reuses_completed_file_when_validators_match(
    tmp_path: Path,
) -> None:
    payload = _make_binary_bytes(1024 * 1024)
    request_log: list[str] = []
    transport = _build_binary_transport(data=payload, request_log=request_log)
    client = httpx.AsyncClient(transport=transport)
    shared_store = _build_shared_store(tmp_path)
    try:
        first = await webfetch.download_binary_response(
            client=client,
            requested_url="https://example.com/cached.pdf",
            response_format="markdown",
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            cancel_check=lambda: None,
        )
        request_log.clear()
        second = await webfetch.download_binary_response(
            client=client,
            requested_url="https://example.com/cached.pdf",
            response_format="markdown",
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            cancel_check=lambda: None,
        )
    finally:
        await client.aclose()

    first_data = cast(dict[str, object], first.visible_data)
    second_data = cast(dict[str, object], second.visible_data)
    assert first_data["saved_path"] == second_data["saved_path"]
    assert request_log == [webfetch.RANGE_PROBE_HEADER_VALUE]


@pytest.mark.asyncio
async def test_download_binary_response_redownloads_completed_file_without_strong_validators(
    tmp_path: Path,
) -> None:
    original_payload = _make_binary_bytes(1024 * 1024)
    updated_payload = b"updated" + original_payload[7:]
    shared_store = _build_shared_store(tmp_path)

    first_client = httpx.AsyncClient(
        transport=_build_binary_transport(
            data=original_payload,
            etag=None,
            last_modified=None,
        )
    )
    try:
        first = await webfetch.download_binary_response(
            client=first_client,
            requested_url="https://example.com/no-validator-cache.pdf",
            response_format="markdown",
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            cancel_check=lambda: None,
        )
    finally:
        await first_client.aclose()

    request_log: list[str] = []
    second_client = httpx.AsyncClient(
        transport=_build_binary_transport(
            data=updated_payload,
            etag=None,
            last_modified=None,
            request_log=request_log,
        )
    )
    try:
        second = await webfetch.download_binary_response(
            client=second_client,
            requested_url="https://example.com/no-validator-cache.pdf",
            response_format="markdown",
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            cancel_check=lambda: None,
        )
    finally:
        await second_client.aclose()

    first_data = cast(dict[str, object], first.visible_data)
    second_data = cast(dict[str, object], second.visible_data)
    assert first_data["saved_path"] == second_data["saved_path"]
    assert Path(str(second_data["saved_path"])).read_bytes() == updated_payload
    assert request_log == [
        webfetch.RANGE_PROBE_HEADER_VALUE,
        f"bytes=0-{len(updated_payload) - 1}",
    ]


@pytest.mark.asyncio
async def test_download_binary_response_restarts_when_etag_changes(
    tmp_path: Path,
) -> None:
    original_payload = _make_binary_bytes(1024 * 1024)
    updated_payload = b"updated" + original_payload[7:]
    shared_store = _build_shared_store(tmp_path)

    first_client = httpx.AsyncClient(
        transport=_build_binary_transport(data=original_payload, etag='"etag-a"')
    )
    try:
        first = await webfetch.download_binary_response(
            client=first_client,
            requested_url="https://example.com/changing.pdf",
            response_format="markdown",
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            cancel_check=lambda: None,
        )
    finally:
        await first_client.aclose()

    second_client = httpx.AsyncClient(
        transport=_build_binary_transport(data=updated_payload, etag='"etag-b"')
    )
    try:
        second = await webfetch.download_binary_response(
            client=second_client,
            requested_url="https://example.com/changing.pdf",
            response_format="markdown",
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            cancel_check=lambda: None,
        )
    finally:
        await second_client.aclose()

    first_data = cast(dict[str, object], first.visible_data)
    second_data = cast(dict[str, object], second.visible_data)
    assert first_data["saved_path"] == second_data["saved_path"]
    assert Path(str(second_data["saved_path"])).read_bytes() == updated_payload


@pytest.mark.asyncio
async def test_download_binary_response_falls_back_when_range_probe_is_ignored(
    tmp_path: Path,
) -> None:
    payload = _make_binary_bytes(1024 * 1024)
    request_log: list[str] = []
    client = httpx.AsyncClient(
        transport=_build_binary_transport(
            data=payload,
            ignore_range_probe=True,
            request_log=request_log,
        )
    )
    shared_store = _build_shared_store(tmp_path)
    try:
        projection = await webfetch.download_binary_response(
            client=client,
            requested_url="https://example.com/no-range.pdf",
            response_format="markdown",
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            cancel_check=lambda: None,
        )
    finally:
        await client.aclose()

    data = cast(dict[str, object], projection.visible_data)
    saved_path = Path(str(data["saved_path"]))
    assert saved_path.read_bytes() == payload
    assert data["download_mode"] == "streaming"
    assert data["streamed_to_disk"] is True
    assert data["range_supported"] is False
    assert data["resume_supported"] is False
    assert request_log == [webfetch.RANGE_PROBE_HEADER_VALUE]


@pytest.mark.asyncio
async def test_download_binary_response_reuses_completed_non_range_file_metadata(
    tmp_path: Path,
) -> None:
    payload = _make_binary_bytes(1024 * 1024)
    request_log: list[str] = []
    shared_store = _build_shared_store(tmp_path)

    first_client = httpx.AsyncClient(
        transport=_build_binary_transport(
            data=payload,
            ignore_range_probe=True,
            request_log=request_log,
        )
    )
    try:
        first = await webfetch.download_binary_response(
            client=first_client,
            requested_url="https://example.com/reuse-no-range.pdf",
            response_format="markdown",
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            cancel_check=lambda: None,
        )
    finally:
        await first_client.aclose()

    second_client = httpx.AsyncClient(
        transport=_build_binary_transport(
            data=payload,
            ignore_range_probe=True,
            request_log=request_log,
        )
    )
    try:
        second = await webfetch.download_binary_response(
            client=second_client,
            requested_url="https://example.com/reuse-no-range.pdf",
            response_format="markdown",
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            cancel_check=lambda: None,
        )
    finally:
        await second_client.aclose()

    first_data = cast(dict[str, object], first.visible_data)
    second_data = cast(dict[str, object], second.visible_data)
    assert first_data["saved_path"] == second_data["saved_path"]
    assert second_data["download_mode"] == "streaming"
    assert second_data["streamed_to_disk"] is True
    assert second_data["range_supported"] is False
    assert second_data["resume_supported"] is False
    assert request_log == [
        webfetch.RANGE_PROBE_HEADER_VALUE,
        webfetch.RANGE_PROBE_HEADER_VALUE,
    ]


@pytest.mark.asyncio
async def test_download_binary_response_falls_back_when_full_range_request_returns_200(
    tmp_path: Path,
) -> None:
    payload = _make_binary_bytes(1024 * 1024)
    request_log: list[str] = []
    client = httpx.AsyncClient(
        transport=_build_binary_transport(
            data=payload,
            request_log=request_log,
            full_range_returns_200=True,
        )
    )
    shared_store = _build_shared_store(tmp_path)
    try:
        projection = await webfetch.download_binary_response(
            client=client,
            requested_url="https://example.com/full-range-200.pdf",
            response_format="markdown",
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            cancel_check=lambda: None,
        )
    finally:
        await client.aclose()

    data = cast(dict[str, object], projection.visible_data)
    saved_path = Path(str(data["saved_path"]))
    assert saved_path.read_bytes() == payload
    assert data["download_mode"] == "streaming"
    assert data["streamed_to_disk"] is True
    assert data["range_supported"] is False
    assert data["resume_supported"] is False
    assert request_log == [
        webfetch.RANGE_PROBE_HEADER_VALUE,
        f"bytes=0-{len(payload) - 1}",
    ]


@pytest.mark.asyncio
async def test_download_binary_response_retries_when_range_request_returns_416(
    tmp_path: Path,
) -> None:
    payload = _make_binary_bytes(1024 * 1024)
    full_range = f"bytes=0-{len(payload) - 1}"
    request_log: list[str] = []
    client = httpx.AsyncClient(
        transport=_build_binary_transport(
            data=payload,
            request_log=request_log,
            fail_once_range_statuses={full_range: 416},
        )
    )
    shared_store = _build_shared_store(tmp_path)
    try:
        projection = await webfetch.download_binary_response(
            client=client,
            requested_url="https://example.com/range-416.pdf",
            response_format="markdown",
            workspace_dir=tmp_path,
            workspace_id="workspace-1",
            shared_store=shared_store,
            cancel_check=lambda: None,
        )
    finally:
        await client.aclose()

    data = cast(dict[str, object], projection.visible_data)
    saved_path = Path(str(data["saved_path"]))
    assert saved_path.read_bytes() == payload
    assert request_log == [
        webfetch.RANGE_PROBE_HEADER_VALUE,
        full_range,
        full_range,
    ]


@pytest.mark.asyncio
async def test_download_binary_response_rejects_probe_over_binary_limit(
    tmp_path: Path,
) -> None:
    large_total = webfetch.MAX_BINARY_DOWNLOAD_SIZE_BYTES + 1

    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            206,
            request=request,
            headers={
                "content-type": "application/pdf",
                "etag": '"etag-1"',
                "last-modified": LAST_MODIFIED,
                "accept-ranges": "bytes",
                "content-range": f"bytes 0-0/{large_total}",
                "content-length": "1",
            },
            content=b"x",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    shared_store = _build_shared_store(tmp_path)
    try:
        with pytest.raises(ToolExecutionError, match="512MB limit"):
            await webfetch.download_binary_response(
                client=client,
                requested_url="https://example.com/huge.pdf",
                response_format="markdown",
                workspace_dir=tmp_path,
                workspace_id="workspace-1",
                shared_store=shared_store,
                cancel_check=lambda: None,
            )
    finally:
        await client.aclose()


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
    assert data["download_mode"] == "buffered"
    assert data["streamed_to_disk"] is False
    assert data["range_supported"] is False
    assert data["resume_supported"] is False


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


def test_build_webfetch_projection_preserves_upstream_markdown(
    tmp_path: Path,
) -> None:
    projection = webfetch.build_webfetch_projection(
        workspace_dir=tmp_path,
        tool_call_id="call_8",
        requested_url="https://example.com/docs",
        final_url="https://example.com/docs",
        response_format="markdown",
        content_type="text/markdown",
        response_headers={
            "x-markdown-tokens": "2882",
            "x-original-tokens": "55237",
            "content-signal": "ai-train=yes, search=yes, ai-input=yes",
        },
        body=b"# Guide\n\nSee [Install](../install).",
        extract=webfetch.WebFetchExtractMode.NONE,
        item_limit=webfetch.DEFAULT_ITEM_LIMIT,
    )

    assert projection.visible_data is not None
    visible_data = projection.visible_data
    assert isinstance(visible_data, dict)
    assert visible_data["output"] == "# Guide\n\nSee [Install](../install)."
    assert visible_data["content_type"] == "text/markdown"
    assert visible_data["markdown_tokens"] == 2882
    assert visible_data["original_tokens"] == 55237
    assert visible_data["content_signal"] == "ai-train=yes, search=yes, ai-input=yes"
    assert projection.internal_data is not None
    internal_data = projection.internal_data
    assert isinstance(internal_data, dict)
    assert internal_data["markdown_tokens"] == 2882
    assert internal_data["original_tokens"] == 55237
    assert internal_data["requested_url"] == "https://example.com/docs"


def test_build_text_result_metadata_ignores_invalid_markdown_headers() -> None:
    metadata = webfetch.build_text_result_metadata(
        {
            "X-Markdown-Tokens": "not-a-number",
            "X-Original-Tokens": "-1",
            "Content-Signal": "  ",
        }
    )

    assert metadata == {}


@pytest.mark.asyncio
async def test_register_webfetch_uses_default_proxy_env_when_hook_runtime_env_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    webfetch.register(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]], fake_agent.tools["webfetch"]
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

    async def _fake_fetch_webfetch_projection(**kwargs: object) -> dict[str, object]:
        _ = kwargs
        return {"ok": True}

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
        webfetch, "create_async_http_client", _fake_create_async_http_client
    )
    monkeypatch.setattr(
        webfetch, "fetch_webfetch_projection", _fake_fetch_webfetch_projection
    )
    monkeypatch.setattr(webfetch, "execute_tool_call", _fake_execute_tool)

    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            hook_runtime_env={},
            workspace=SimpleNamespace(
                locations=SimpleNamespace(workspace_dir=tmp_path),
            ),
            workspace_id="default",
            shared_store=cast(object, None),
            run_control_manager=SimpleNamespace(
                raise_if_cancelled=lambda **kwargs: None,
            ),
            run_id="run-1",
            instance_id="inst-1",
        ),
        tool_call_id="toolcall-1",
    )

    _ = await tool(ctx, url="https://example.com")

    assert "merged_env" not in captured
