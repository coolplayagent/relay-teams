# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
import asyncio
from html import unescape
import hashlib
import ipaddress
import math
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import ParseResult, urljoin, urlparse
from xml.etree.ElementTree import Element, ParseError

import defusedxml.ElementTree as safe_element_tree
import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai import Agent

from relay_teams.computer import ComputerActionRisk
from relay_teams.net.clients import create_async_http_client
from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolApprovalRequest,
    ToolExecutionError,
    ToolResultProjection,
    execute_tool_call,
)
from relay_teams.tools.web_tools.preapproved import is_preapproved_webfetch_url
from relay_teams.tools.web_tools.common import (
    MAX_TEXT_OUTPUT_CHARS,
    extract_text_from_html,
    convert_html_to_markdown,
    resolve_webfetch_output_dir,
    sanitize_file_extension,
)

MAX_TEXT_RESPONSE_SIZE_BYTES = 5 * 1024 * 1024
MAX_RESPONSE_SIZE_BYTES = MAX_TEXT_RESPONSE_SIZE_BYTES
MAX_BINARY_DOWNLOAD_SIZE_BYTES = 512 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 120
DEFAULT_FORMAT = "markdown"
DEFAULT_ITEM_LIMIT = 20
MAX_ITEM_LIMIT = 50
PARALLEL_DOWNLOAD_THRESHOLD_BYTES = 4 * 1024 * 1024
PARALLEL_DOWNLOAD_SEGMENT_COUNT = 4
MIN_SEGMENT_SIZE_BYTES = 1 * 1024 * 1024
DOWNLOAD_CHUNK_SIZE_BYTES = 256 * 1024
STATE_FLUSH_INTERVAL_BYTES = 1 * 1024 * 1024
RANGE_PROBE_HEADER_VALUE = "bytes=0-0"
WEBFETCH_DOWNLOAD_PREFIX = "webfetch_download"
RANGE_RESET_ERROR_TYPES = {"range_response_invalid", "range_resume_mismatch"}
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
REDIRECT_REQUIRED_ERROR_TYPE = "redirect_required"
WEBFETCH_FINAL_URL_EXTENSION_KEY = "agent_teams_final_url"
MAX_REDIRECTS = 10
MAX_REDIRECT_BODY_BYTES = 64 * 1024
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
FALLBACK_USER_AGENT = "agent-teams"
READER_FALLBACK_BASE_URL = "https://r.jina.ai/"
ANTI_BOT_STATUS_CODES = {403, 429, 503}
TEXTUAL_CHALLENGE_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}
ENTERPRISE_PROXY_BLOCK_URL_MARKERS = ("proxycontrolwarn", "httpwarning_2907")
ENTERPRISE_PROXY_BLOCK_MARKERS = (
    "his proxy notification",
    "proxy notification",
    "proxycontrolwarn",
)
TUNNEL_ERROR_MARKERS = (
    "err_tunnel_connection_failed",
    "tunnel connection failed",
    "tunnel_connection_failed",
)
ANTI_BOT_CHALLENGE_MARKERS = (
    "checking your browser before accessing",
    "enable javascript and cookies to continue",
    "sorry, you have been blocked",
    "verify you are human",
    "captcha",
    "attention required!",
)
DESCRIPTION = load_tool_description(__file__)
META_REFRESH_REDIRECT_PATTERN = re.compile(
    r"""<meta[^>]+http-equiv\s*=\s*["']?refresh["']?[^>]+content\s*=\s*["'][^"']*url\s*=\s*([^"'>\s]+)""",
    re.IGNORECASE,
)
WINDOW_LOCATION_REDIRECT_PATTERN = re.compile(
    r"""window\.location(?:\.href)?\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
LOCATION_REPLACE_REDIRECT_PATTERN = re.compile(
    r"""location\.replace\(\s*["']([^"']+)["']\s*\)""",
    re.IGNORECASE,
)
ANCHOR_REDIRECT_PATTERN = re.compile(
    r"""<a[^>]+href\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)


class WebFetchExtractMode(StrEnum):
    NONE = "none"
    FEED = "feed"
    OPML = "opml"


class BinaryDownloadMode(StrEnum):
    BUFFERED = "buffered"
    STREAMING = "streaming"
    STREAMING_RANGES = "streaming_ranges"


class ParsedDocumentKind(StrEnum):
    FEED = "feed"
    OPML = "opml"


class AutomatedFetchBlockKind(StrEnum):
    CLOUDFLARE_CHALLENGE = "cloudflare_challenge"
    CHALLENGE_PAGE = "challenge_page"
    ENTERPRISE_PROXY_BLOCK = "enterprise_proxy_block"


class FeedEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    link: str | None = None
    published: str | None = None
    updated: str | None = None
    summary: str | None = None


class ParsedFeedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ParsedDocumentKind = ParsedDocumentKind.FEED
    title: str | None = None
    feed_url: str | None = None
    site_url: str | None = None
    updated: str | None = None
    count: int
    total_count: int
    truncated: bool
    entries: list[FeedEntry]


class OpmlFeedEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    title: str | None = None
    feed_type: str | None = None
    xml_url: str
    html_url: str | None = None
    group_path: list[str]


class ParsedOpmlDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ParsedDocumentKind = ParsedDocumentKind.OPML
    title: str | None = None
    count: int
    total_count: int
    truncated: bool
    feeds: list[OpmlFeedEntry]


class BinaryDownloadProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_url: str
    final_url: str
    content_type: str
    total_size: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    range_supported: bool = False


class BinaryDownloadSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    file_name: str = Field(min_length=1)
    downloaded_bytes: int = Field(default=0, ge=0)
    complete: bool = False


class BinaryDownloadManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_url: str
    normalized_requested_url: str
    final_url: str
    mime_type: str
    suffix: str
    total_size: int | None = Field(default=None, ge=0)
    etag: str | None = None
    last_modified: str | None = None
    range_supported: bool = False
    segment_count: int = Field(default=0, ge=0)
    segments: list[BinaryDownloadSegment] = Field(default_factory=list)
    saved_path: str = Field(min_length=1)
    completed: bool = False


class BinaryDownloadFullResponseFallback(Exception):
    def __init__(self, response: httpx.Response) -> None:
        super().__init__("Full-response fallback available for binary download.")
        self.response = response


def normalize_webfetch_host(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if hostname is not None:
        return hostname.rstrip(".").lower()
    if parsed.netloc:
        return parsed.netloc.strip().lower()
    return url.strip()


def build_webfetch_approval_request(url: str) -> ToolApprovalRequest:
    host = normalize_webfetch_host(url)
    return ToolApprovalRequest(
        risk_level=(
            ComputerActionRisk.SAFE if is_preapproved_webfetch_url(url) else None
        ),
        target_summary=host,
        source=url,
    )


def build_webfetch_approval_args_summary(url: str) -> dict[str, JsonValue]:
    return {"host": normalize_webfetch_host(url)}


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def webfetch(
        ctx: ToolContext,
        url: str,
        format: str = DEFAULT_FORMAT,
        timeout: int | None = None,
        extract: WebFetchExtractMode = WebFetchExtractMode.NONE,
        item_limit: int = DEFAULT_ITEM_LIMIT,
    ) -> dict[str, JsonValue]:
        """Fetch web content and return text, a saved file path, or structured feed data."""

        async def _action(
            url: str,
            format: str = DEFAULT_FORMAT,
            timeout: int | None = None,
            extract: WebFetchExtractMode = WebFetchExtractMode.NONE,
            item_limit: int = DEFAULT_ITEM_LIMIT,
        ) -> ToolResultProjection:
            validate_web_url(url)
            resolved_timeout = normalize_timeout_seconds(timeout)
            resolved_item_limit = normalize_item_limit(item_limit)
            async with create_async_http_client(
                timeout_seconds=float(resolved_timeout),
                follow_redirects=False,
            ) as client:
                return await fetch_webfetch_projection(
                    client=client,
                    requested_url=url,
                    response_format=format,
                    extract=normalize_extract_mode(extract),
                    item_limit=resolved_item_limit,
                    workspace_dir=ctx.deps.workspace.locations.workspace_dir,
                    workspace_id=ctx.deps.workspace_id,
                    shared_store=ctx.deps.shared_store,
                    tool_call_id=ctx.tool_call_id or "webfetch",
                    cancel_check=lambda: (
                        ctx.deps.run_control_manager.raise_if_cancelled(
                            run_id=ctx.deps.run_id,
                            instance_id=ctx.deps.instance_id,
                        )
                    ),
                )

        return await execute_tool_call(
            ctx,
            tool_name="webfetch",
            args_summary={
                "url": url,
                "format": format,
                "timeout": timeout,
                "extract": extract,
                "item_limit": item_limit,
            },
            action=_action,
            raw_args=locals(),
            approval_request_factory=lambda tool_input: build_webfetch_approval_request(
                str(tool_input.get("url") or url)
            ),
            approval_args_summary_factory=lambda tool_input: (
                build_webfetch_approval_args_summary(str(tool_input.get("url") or url))
            ),
            keep_approval_ticket_reusable=True,
        )


async def fetch_webfetch_projection(
    *,
    client: httpx.AsyncClient,
    requested_url: str,
    response_format: str,
    extract: WebFetchExtractMode,
    item_limit: int,
    workspace_dir: Path,
    workspace_id: str,
    shared_store: SharedStateRepository,
    tool_call_id: str,
    cancel_check: Callable[[], None],
) -> ToolResultProjection:
    if extract is WebFetchExtractMode.NONE:
        probe_response: httpx.Response | None = None
        try:
            probe, probe_response = await probe_binary_download_with_response(
                client=client,
                url=requested_url,
                response_format=response_format,
            )
        except ToolExecutionError as exc:
            if exc.error_type == REDIRECT_REQUIRED_ERROR_TYPE:
                return build_redirect_required_projection(exc)
            probe = None
        else:
            try:
                if probe is not None and is_binary_response(probe.content_type):
                    if probe.range_supported:
                        return await download_binary_response(
                            client=client,
                            requested_url=requested_url,
                            response_format=response_format,
                            workspace_dir=workspace_dir,
                            workspace_id=workspace_id,
                            shared_store=shared_store,
                            cancel_check=cancel_check,
                            probe=probe,
                        )
                    if probe_response is not None and probe_response.status_code == 200:
                        return await download_binary_response_from_response(
                            response=probe_response,
                            requested_url=requested_url,
                            workspace_dir=workspace_dir,
                            workspace_id=workspace_id,
                            shared_store=shared_store,
                            cancel_check=cancel_check,
                        )
            finally:
                if probe_response is not None:
                    await probe_response.aclose()

    try:
        response = await fetch_url(
            client=client,
            url=requested_url,
            response_format=response_format,
        )
    except ToolExecutionError as exc:
        if exc.error_type == REDIRECT_REQUIRED_ERROR_TYPE:
            return build_redirect_required_projection(exc)
        raise
    try:
        content_type = normalize_content_type(response.headers.get("content-type", ""))
        if extract is WebFetchExtractMode.NONE and is_binary_response(content_type):
            return await download_binary_response_from_response(
                response=response,
                requested_url=requested_url,
                workspace_dir=workspace_dir,
                workspace_id=workspace_id,
                shared_store=shared_store,
                cancel_check=cancel_check,
            )
        enforce_text_content_length_limit(response)
        body = await read_response_body(response)
        return build_webfetch_projection(
            workspace_dir=workspace_dir,
            tool_call_id=tool_call_id,
            requested_url=requested_url,
            final_url=resolve_webfetch_response_url(response),
            response_format=response_format,
            content_type=content_type,
            body=body,
            extract=extract,
            item_limit=item_limit,
        )
    finally:
        await response.aclose()


def validate_web_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL must start with http:// or https://")
    if not parsed.netloc or parsed.hostname is None:
        raise ValueError("URL must include a host")
    if parsed.username or parsed.password:
        raise ValueError("URL must not include a username or password")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".local"):
        raise ValueError("URL host must be publicly reachable")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise ValueError("URL host must be a fully qualified domain name") from None
        return
    if _is_disallowed_webfetch_ip(address):
        raise ValueError("URL host must not be a private or local address")


def _is_disallowed_webfetch_ip(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return (
        str(address) == "169.254.169.254"
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def normalize_timeout_seconds(timeout: int | None) -> int:
    if timeout is None:
        return DEFAULT_TIMEOUT_SECONDS
    if timeout <= 0:
        raise ValueError("timeout must be greater than 0")
    return min(timeout, MAX_TIMEOUT_SECONDS)


def normalize_extract_mode(mode: WebFetchExtractMode | str) -> WebFetchExtractMode:
    if isinstance(mode, WebFetchExtractMode):
        return mode
    try:
        return WebFetchExtractMode(mode)
    except ValueError as exc:
        raise ValueError("extract must be one of: none, feed, opml") from exc


def normalize_item_limit(item_limit: int) -> int:
    if item_limit <= 0:
        raise ValueError("item_limit must be greater than 0")
    return min(item_limit, MAX_ITEM_LIMIT)


def build_accept_header(response_format: str) -> str:
    if response_format == "markdown":
        return (
            "text/markdown;q=1.0, text/x-markdown;q=0.9, "
            "text/plain;q=0.8, text/html;q=0.7, */*;q=0.1"
        )
    if response_format == "text":
        return "text/plain;q=1.0, text/markdown;q=0.9, text/html;q=0.8, */*;q=0.1"
    if response_format == "html":
        return (
            "text/html;q=1.0, application/xhtml+xml;q=0.9, "
            "text/plain;q=0.8, text/markdown;q=0.7, */*;q=0.1"
        )
    raise ValueError(f"Unsupported format: {response_format}")


async def fetch_url(
    *,
    client: httpx.AsyncClient,
    url: str,
    response_format: str,
    extra_headers: dict[str, str] | None = None,
    allowed_error_status_codes: set[int] | None = None,
) -> httpx.Response:
    url_host = urlparse(url).netloc
    headers = build_direct_fetch_headers(
        response_format=response_format,
        user_agent=BROWSER_USER_AGENT,
        extra_headers=extra_headers,
    )
    current_url = url
    redirect_count = 0
    used_fallback_user_agent = False
    while True:
        response = await _perform_request(
            client=client, url=current_url, headers=headers
        )
        while response.status_code in REDIRECT_STATUS_CODES:
            location = response.headers.get("location")
            if not location:
                try:
                    location = await _extract_redirect_location_from_response_body(
                        response
                    )
                except ToolExecutionError:
                    await response.aclose()
                    raise
            if not location:
                await response.aclose()
                raise ToolExecutionError(
                    error_type="upstream_error",
                    message=f"Web fetch failed for {url_host or url} with HTTP {response.status_code}",
                    retryable=False,
                    details={
                        "url_host": url_host,
                        "status_code": response.status_code,
                        "missing_location": True,
                    },
                )
            redirect_url = urljoin(current_url, location)
            status_code = response.status_code
            await response.aclose()
            if not is_permitted_redirect(current_url, redirect_url):
                raise ToolExecutionError(
                    error_type=REDIRECT_REQUIRED_ERROR_TYPE,
                    message="Web fetch requires an explicit follow-up for a cross-host redirect.",
                    retryable=False,
                    details={
                        "original_url": current_url,
                        "redirect_url": redirect_url,
                        "status_code": status_code,
                    },
                )
            redirect_count += 1
            if redirect_count > MAX_REDIRECTS:
                raise ToolExecutionError(
                    error_type="redirect_loop",
                    message=f"Web fetch exceeded the redirect limit for {url_host or url}",
                    retryable=True,
                    details={
                        "url_host": url_host,
                        "redirect_count": redirect_count,
                    },
                )
            current_url = redirect_url
            response = await _perform_request(
                client=client, url=current_url, headers=headers
            )
        block_kind = await detect_automated_fetch_block(response)
        if (
            block_kind == AutomatedFetchBlockKind.CLOUDFLARE_CHALLENGE
            and not used_fallback_user_agent
        ):
            await response.aclose()
            headers = build_direct_fetch_headers(
                response_format=response_format,
                user_agent=FALLBACK_USER_AGENT,
                extra_headers=extra_headers,
            )
            used_fallback_user_agent = True
            continue
        break
    if response.status_code == 403 and (
        response.headers.get("x-proxy-error") == "blocked-by-allowlist"
    ):
        await response.aclose()
        raise ToolExecutionError(
            error_type="egress_blocked",
            message=f"Web fetch was blocked by the network egress policy for {url_host or url}",
            retryable=False,
            details={
                "url_host": url_host,
                "status_code": response.status_code,
                "proxy_error": "blocked-by-allowlist",
            },
        )
    if block_kind == AutomatedFetchBlockKind.ENTERPRISE_PROXY_BLOCK:
        await response.aclose()
        raise ToolExecutionError(
            error_type="proxy_blocked",
            message=f"Web fetch was blocked by the enterprise proxy for {url_host or url}",
            retryable=False,
            details={
                "url_host": url_host,
                "blocked_url": current_url,
            },
        )
    if block_kind is not None:
        await response.aclose()
        return await fetch_with_reader_fallback(
            client=client,
            url=current_url,
            response_format=response_format,
            url_host=url_host,
        )
    if response.status_code >= 400 and (
        allowed_error_status_codes is None
        or response.status_code not in allowed_error_status_codes
    ):
        await response.aclose()
        raise ToolExecutionError(
            error_type=_webfetch_status_error_type(response.status_code),
            message=_webfetch_status_error_message(
                url_host=url_host, response=response
            ),
            retryable=response.status_code in {429} or response.status_code >= 500,
            details={
                "url_host": url_host,
                "status_code": response.status_code,
            },
        )
    return response


def build_direct_fetch_headers(
    *,
    response_format: str,
    user_agent: str,
    extra_headers: dict[str, str] | None,
) -> dict[str, str]:
    headers = {
        "User-Agent": user_agent,
        "Accept": build_accept_header(response_format),
        "Accept-Language": "en-US,en;q=0.9",
    }
    if extra_headers is not None:
        headers.update(extra_headers)
    return headers


async def detect_automated_fetch_block(
    response: httpx.Response,
) -> AutomatedFetchBlockKind | None:
    if is_cloudflare_challenge_response(response):
        return AutomatedFetchBlockKind.CLOUDFLARE_CHALLENGE
    if is_enterprise_proxy_block_response(response):
        return AutomatedFetchBlockKind.ENTERPRISE_PROXY_BLOCK
    if await is_textual_challenge_response(response):
        return AutomatedFetchBlockKind.CHALLENGE_PAGE
    return None


def is_cloudflare_challenge_response(response: httpx.Response) -> bool:
    return (
        response.status_code == 403
        and response.headers.get("cf-mitigated") == "challenge"
    )


def is_enterprise_proxy_block_response(response: httpx.Response) -> bool:
    final_url = str(response.url).lower()
    return any(marker in final_url for marker in ENTERPRISE_PROXY_BLOCK_URL_MARKERS)


async def is_textual_challenge_response(response: httpx.Response) -> bool:
    content_type = normalize_content_type(response.headers.get("content-type", ""))
    if content_type not in TEXTUAL_CHALLENGE_CONTENT_TYPES:
        return False
    if (
        response.status_code >= 400
        and response.status_code not in ANTI_BOT_STATUS_CODES
    ):
        return False
    body_text = (await response.aread()).decode("utf-8", errors="replace").lower()
    if any(marker in body_text for marker in ENTERPRISE_PROXY_BLOCK_MARKERS):
        return False
    return any(marker in body_text for marker in ANTI_BOT_CHALLENGE_MARKERS)


async def fetch_with_reader_fallback(
    *,
    client: httpx.AsyncClient,
    url: str,
    response_format: str,
    url_host: str,
) -> httpx.Response:
    try:
        fallback_response = await _perform_request(
            client=client,
            url=build_reader_fallback_url(url),
            headers=build_reader_fallback_headers(response_format),
        )
    except ToolExecutionError as exc:
        raise _build_anti_bot_challenge_error(
            url=url,
            url_host=url_host,
            details={"fallback_error_type": exc.error_type},
        ) from exc
    if fallback_response.status_code >= 400:
        await fallback_response.aclose()
        raise _build_anti_bot_challenge_error(
            url=url,
            url_host=url_host,
            details={"fallback_status_code": fallback_response.status_code},
        )
    fallback_response.extensions[WEBFETCH_FINAL_URL_EXTENSION_KEY] = url
    return fallback_response


def build_reader_fallback_url(url: str) -> str:
    return f"{READER_FALLBACK_BASE_URL}{url}"


def build_reader_fallback_headers(response_format: str) -> dict[str, str]:
    return {
        "User-Agent": FALLBACK_USER_AGENT,
        "Accept": build_accept_header(response_format),
        "X-Respond-With": response_format,
        "X-No-Cache": "true",
    }


def _build_anti_bot_challenge_error(
    *,
    url: str,
    url_host: str,
    details: dict[str, JsonValue],
) -> ToolExecutionError:
    return ToolExecutionError(
        error_type="anti_bot_challenge",
        message=f"Website blocked automated fetch with an anti-bot challenge: {url_host or url}",
        retryable=False,
        details={
            "url_host": url_host,
            "mitigation": "reader_fallback_failed",
            **details,
        },
    )


async def _extract_redirect_location_from_response_body(
    response: httpx.Response,
) -> str | None:
    url_host = urlparse(str(response.request.url)).netloc
    content_type = normalize_content_type(response.headers.get("content-type", ""))
    if content_type and content_type not in {"text/html", "application/xhtml+xml"}:
        return None
    body = bytearray()
    try:
        async for chunk in response.aiter_bytes():
            if not chunk:
                continue
            remaining = MAX_REDIRECT_BODY_BYTES - len(body)
            if remaining <= 0:
                break
            body.extend(chunk[:remaining])
            if len(body) >= MAX_REDIRECT_BODY_BYTES:
                break
    except httpx.TimeoutException as exc:
        raise ToolExecutionError(
            error_type="network_timeout",
            message=f"Web fetch timed out for {url_host or response.request.url}",
            retryable=True,
            details={"url_host": url_host},
        ) from exc
    except httpx.RequestError as exc:
        raise ToolExecutionError(
            error_type="network_error",
            message=f"Web fetch request failed for {url_host or response.request.url}: {exc}",
            retryable=True,
            details={"url_host": url_host},
        ) from exc
    if not body:
        return None
    text = body.decode("utf-8", errors="replace")
    return _extract_redirect_location_from_html(text)


def _extract_redirect_location_from_html(body: str) -> str | None:
    patterns = (
        META_REFRESH_REDIRECT_PATTERN,
        WINDOW_LOCATION_REDIRECT_PATTERN,
        LOCATION_REPLACE_REDIRECT_PATTERN,
        ANCHOR_REDIRECT_PATTERN,
    )
    for pattern in patterns:
        match = pattern.search(body)
        if match is None:
            continue
        location = _normalize_redirect_location_candidate(match.group(1))
        if location:
            return location
    return None


def _normalize_redirect_location_candidate(value: str) -> str | None:
    candidate = unescape(value).strip()
    if not candidate:
        return None
    if candidate.lower().startswith("url="):
        candidate = candidate[4:].strip()
    candidate = candidate.strip("\"' \t\r\n")
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return None
    if parsed.username or parsed.password:
        return None
    if parsed.scheme and not parsed.netloc:
        return None
    return candidate


async def _perform_request(
    *,
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
) -> httpx.Response:
    url_host = urlparse(url).netloc
    try:
        request = client.build_request("GET", url, headers=headers)
        return await client.send(request, stream=True)
    except httpx.TimeoutException as exc:
        raise ToolExecutionError(
            error_type="network_timeout",
            message=f"Web fetch timed out for {url_host or url}",
            retryable=True,
            details={"url_host": url_host},
        ) from exc
    except httpx.ProxyError as exc:
        error_type = (
            "tunnel_error" if is_tunnel_error_message(str(exc)) else "proxy_error"
        )
        raise ToolExecutionError(
            error_type=error_type,
            message=f"Web fetch request failed for {url_host or url}: {exc}",
            retryable=True,
            details={"url_host": url_host},
        ) from exc
    except httpx.RequestError as exc:
        error_type = (
            "tunnel_error" if is_tunnel_error_message(str(exc)) else "network_error"
        )
        raise ToolExecutionError(
            error_type=error_type,
            message=f"Web fetch request failed for {url_host or url}: {exc}",
            retryable=True,
            details={"url_host": url_host},
        ) from exc


def is_permitted_redirect(original_url: str, redirect_url: str) -> bool:
    original = urlparse(original_url)
    redirect = urlparse(redirect_url)
    if redirect.username or redirect.password:
        return False
    if not _is_permitted_redirect_scheme_transition(original.scheme, redirect.scheme):
        return False
    original_host = (original.hostname or "").removeprefix("www.")
    redirect_host = (redirect.hostname or "").removeprefix("www.")
    if not original_host or original_host != redirect_host:
        return False
    original_port = _normalize_default_port(original, redirect_url=original_url)
    redirect_port = _normalize_default_port(redirect, redirect_url=redirect_url)

    if original.scheme == redirect.scheme:
        return original_port == redirect_port
    return _is_default_port(original_port, original.scheme) and _is_default_port(
        redirect_port, redirect.scheme
    )


def _is_permitted_redirect_scheme_transition(
    original_scheme: str, redirect_scheme: str
) -> bool:
    return (
        original_scheme == redirect_scheme and original_scheme in {"http", "https"}
    ) or (original_scheme == "http" and redirect_scheme == "https")


def _is_default_port(port: int | None, scheme: str) -> bool:
    default_port = _default_port_for_scheme(scheme)
    return default_port is not None and port == default_port


def _normalize_default_port(
    parsed: ParseResult,
    *,
    redirect_url: str,
) -> int | None:
    try:
        port = parsed.port
    except ValueError as exc:
        raise ToolExecutionError(
            error_type="upstream_error",
            message="Web fetch received an invalid redirect URL.",
            retryable=False,
            details={
                "url_host": parsed.hostname or "",
                "redirect_url": redirect_url,
                "invalid_port": True,
            },
        ) from exc
    default_port = _default_port_for_scheme(parsed.scheme)
    if default_port is None:
        return port
    return default_port if port is None else port


def _default_port_for_scheme(scheme: str) -> int | None:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None


def build_redirect_required_projection(
    error: ToolExecutionError,
) -> ToolResultProjection:
    original_url = str(error.details.get("original_url") or "")
    redirect_url = str(error.details.get("redirect_url") or "")
    status_code_raw = error.details.get("status_code")
    status_code = status_code_raw if isinstance(status_code_raw, int) else 0
    output = (
        "Redirect required: the requested URL redirected to a different host.\n\n"
        f"Original URL: {original_url}\n"
        f"Redirect URL: {redirect_url}\n"
        f"Status: {status_code}\n\n"
        "Run webfetch again with the redirect URL if you want to continue."
    )
    visible_data: dict[str, JsonValue] = {
        "output": output,
        "redirect_required": True,
        "original_url": original_url,
        "redirect_url": redirect_url,
        "status_code": status_code,
    }
    return ToolResultProjection(
        visible_data=visible_data,
        internal_data=visible_data,
    )


def _parse_content_length(response: httpx.Response) -> int | None:
    content_length = response.headers.get("content-length")
    if content_length is None:
        return None
    try:
        return int(content_length)
    except ValueError:
        return None


def enforce_text_content_length_limit(response: httpx.Response) -> None:
    parsed_length = _parse_content_length(response)
    if parsed_length is not None and parsed_length > MAX_TEXT_RESPONSE_SIZE_BYTES:
        raise ToolExecutionError(
            error_type="response_too_large",
            message="Response too large (exceeds 5MB limit)",
            retryable=False,
        )


def _raise_binary_size_limit_error(limit_bytes: int) -> None:
    limit_mb = limit_bytes // (1024 * 1024)
    raise ToolExecutionError(
        error_type="response_too_large",
        message=f"Binary download too large (exceeds {limit_mb}MB limit)",
        retryable=False,
    )


async def read_response_body(response: httpx.Response) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        body.extend(chunk)
        if len(body) > MAX_TEXT_RESPONSE_SIZE_BYTES:
            raise ToolExecutionError(
                error_type="response_too_large",
                message="Response too large (exceeds 5MB limit)",
                retryable=False,
            )
    return bytes(body)
    return body


def is_tunnel_error_message(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in TUNNEL_ERROR_MARKERS)


def _webfetch_status_error_type(status_code: int) -> str:
    if status_code == 401:
        return "auth_error"
    if status_code == 403:
        return "source_access_denied"
    if status_code == 404:
        return "not_found"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "upstream_unavailable"
    return "upstream_error"


def _webfetch_status_error_message(
    *,
    url_host: str,
    response: httpx.Response,
) -> str:
    target = url_host or str(response.request.url)
    return f"Web fetch failed for {target} with HTTP {response.status_code}"


def normalize_content_type(content_type_header: str) -> str:
    return content_type_header.split(";", 1)[0].strip().lower()


def resolve_webfetch_response_url(response: httpx.Response) -> str:
    stored_url = response.extensions.get(WEBFETCH_FINAL_URL_EXTENSION_KEY)
    if isinstance(stored_url, str) and stored_url:
        return stored_url
    return str(response.url)


def build_webfetch_projection(
    *,
    workspace_dir: Path,
    tool_call_id: str,
    requested_url: str,
    final_url: str,
    response_format: str,
    content_type: str,
    body: bytes,
    extract: WebFetchExtractMode,
    item_limit: int,
) -> ToolResultProjection:
    if extract is not WebFetchExtractMode.NONE:
        if is_binary_response(content_type):
            raise ValueError(
                f"Cannot parse extract={extract.value!r} from binary content type: {content_type or 'unknown'}"
            )
        decoded_body = body.decode("utf-8", errors="replace")
        parsed_projection = build_structured_projection(
            final_url=final_url,
            content_type=content_type,
            body=decoded_body,
            extract=extract,
            item_limit=item_limit,
        )
        return ToolResultProjection(
            visible_data=parsed_projection,
            internal_data={
                **parsed_projection,
                "requested_url": requested_url,
            },
        )

    if is_binary_response(content_type):
        return save_binary_result(
            workspace_dir=workspace_dir,
            tool_call_id=tool_call_id,
            final_url=final_url,
            content_type=content_type,
            body=body,
        )

    decoded_body = body.decode("utf-8", errors="replace")
    rendered_output = render_text_output(
        response_format=response_format,
        content_type=content_type,
        final_url=final_url,
        body=decoded_body,
    )
    return finalize_text_result(
        workspace_dir=workspace_dir,
        tool_call_id=tool_call_id,
        requested_url=requested_url,
        final_url=final_url,
        content_type=content_type,
        output=rendered_output,
    )


def is_binary_response(content_type: str) -> bool:
    if not content_type:
        return False
    return not is_textual_content_type(content_type)


def is_textual_content_type(content_type: str) -> bool:
    if not content_type:
        return True
    if content_type.startswith("text/"):
        return True
    if content_type in {
        "application/json",
        "application/xml",
        "application/xhtml+xml",
        "image/svg+xml",
    }:
        return True
    return content_type.endswith("+xml") or content_type.endswith("+json")


def render_text_output(
    *,
    response_format: str,
    content_type: str,
    final_url: str,
    body: str,
) -> str:
    if response_format == "html":
        return body
    if content_type == "text/html" or content_type == "application/xhtml+xml":
        if response_format == "text":
            return extract_text_from_html(body)
        return convert_html_to_markdown(body, base_url=final_url)
    return body


def finalize_text_result(
    *,
    workspace_dir: Path,
    tool_call_id: str,
    requested_url: str,
    final_url: str,
    content_type: str,
    output: str,
) -> ToolResultProjection:
    visible_data: dict[str, JsonValue] = {
        "output": output,
        "final_url": final_url,
        "content_type": content_type,
        "truncated": False,
    }
    if len(output) <= MAX_TEXT_OUTPUT_CHARS:
        return ToolResultProjection(
            visible_data=visible_data,
            internal_data={
                **visible_data,
                "requested_url": requested_url,
            },
        )

    output_dir = resolve_webfetch_output_dir(workspace_dir)
    output_path = output_dir / f"{tool_call_id}.txt"
    output_path.write_text(output, encoding="utf-8")
    truncated_output = output[:MAX_TEXT_OUTPUT_CHARS]
    visible_data["output"] = truncated_output
    visible_data["truncated"] = True
    visible_data["saved_path"] = str(output_path)
    return ToolResultProjection(
        visible_data=visible_data,
        internal_data={
            **visible_data,
            "requested_url": requested_url,
            "full_output_path": str(output_path),
        },
    )


def save_binary_result(
    *,
    workspace_dir: Path,
    tool_call_id: str,
    final_url: str,
    content_type: str,
    body: bytes,
) -> ToolResultProjection:
    output_dir = resolve_webfetch_output_dir(workspace_dir)
    suffix = sanitize_file_extension(final_url, content_type)
    output_path = output_dir / f"{tool_call_id}{suffix}"
    output_path.write_bytes(body)
    return build_binary_download_projection(
        saved_path=output_path,
        content_type=content_type,
        size_bytes=len(body),
        final_url=final_url,
        download_mode=BinaryDownloadMode.BUFFERED,
        range_supported=False,
        resume_supported=False,
    )


async def download_binary_response(
    *,
    client: httpx.AsyncClient,
    requested_url: str,
    response_format: str,
    workspace_dir: Path,
    workspace_id: str,
    shared_store: SharedStateRepository,
    cancel_check: Callable[[], None],
    probe: BinaryDownloadProbe | None = None,
) -> ToolResultProjection:
    normalized_url = normalize_requested_download_url(requested_url)
    download_key = build_binary_download_key(normalized_url)
    download_dir = resolve_binary_download_dir(workspace_dir, download_key)
    manifest_path = resolve_binary_manifest_path(download_dir)
    manifest = load_binary_download_manifest(manifest_path)
    if manifest is not None:
        manifest = reconcile_binary_download_manifest(manifest, download_dir)
        if manifest is None:
            _cleanup_binary_download_dir(download_dir)

    active_probe = probe
    probe_response: httpx.Response | None = None
    if active_probe is None:
        active_probe, probe_response = await probe_binary_download_with_response(
            client=client,
            url=requested_url,
            response_format=response_format,
        )
    try:
        if (
            active_probe.total_size is not None
            and active_probe.total_size > MAX_BINARY_DOWNLOAD_SIZE_BYTES
        ):
            _cleanup_binary_download_dir(download_dir)
            _raise_binary_size_limit_error(MAX_BINARY_DOWNLOAD_SIZE_BYTES)

        if manifest is not None and not binary_download_manifest_matches_probe(
            manifest=manifest,
            probe=active_probe,
        ):
            _cleanup_binary_download_dir(download_dir)
            manifest = None

        if (
            manifest is not None
            and manifest.completed
            and binary_download_manifest_is_usable(
                manifest=manifest,
                probe=active_probe,
            )
        ):
            save_binary_download_manifest(
                manifest=manifest,
                manifest_path=manifest_path,
                shared_store=shared_store,
                workspace_id=workspace_id,
                download_key=download_key,
            )
            return build_binary_download_projection(
                saved_path=Path(manifest.saved_path),
                content_type=manifest.mime_type,
                size_bytes=manifest.total_size
                or Path(manifest.saved_path).stat().st_size,
                final_url=manifest.final_url,
                download_mode=(
                    BinaryDownloadMode.STREAMING_RANGES
                    if manifest.range_supported
                    else BinaryDownloadMode.STREAMING
                ),
                range_supported=manifest.range_supported,
                resume_supported=manifest.range_supported
                and binary_download_resume_supported(probe=active_probe),
            )

        if manifest is None:
            manifest = initialize_binary_download_manifest(
                normalized_requested_url=normalized_url,
                probe=active_probe,
                download_dir=download_dir,
            )
            save_binary_download_manifest(
                manifest=manifest,
                manifest_path=manifest_path,
                shared_store=shared_store,
                workspace_id=workspace_id,
                download_key=download_key,
            )

        if not active_probe.range_supported or active_probe.total_size is None:
            _cleanup_binary_download_dir(download_dir)
            manifest = initialize_binary_download_manifest(
                normalized_requested_url=normalized_url,
                probe=active_probe,
                download_dir=download_dir,
            )
            if probe_response is not None and probe_response.status_code == 200:
                manifest = await save_non_resumable_binary_response(
                    response=probe_response,
                    requested_url=requested_url,
                    manifest=manifest,
                    download_dir=download_dir,
                    manifest_path=manifest_path,
                    shared_store=shared_store,
                    workspace_id=workspace_id,
                    download_key=download_key,
                    cancel_check=cancel_check,
                )
            else:
                manifest = await download_binary_without_resume(
                    client=client,
                    requested_url=requested_url,
                    response_format=response_format,
                    manifest=manifest,
                    download_dir=download_dir,
                    manifest_path=manifest_path,
                    shared_store=shared_store,
                    workspace_id=workspace_id,
                    download_key=download_key,
                    cancel_check=cancel_check,
                )
            return build_binary_download_projection(
                saved_path=Path(manifest.saved_path),
                content_type=manifest.mime_type,
                size_bytes=manifest.total_size
                or Path(manifest.saved_path).stat().st_size,
                final_url=manifest.final_url,
                download_mode=BinaryDownloadMode.STREAMING,
                range_supported=False,
                resume_supported=False,
            )

        last_error: ToolExecutionError | None = None
        for attempt in range(2):
            active_manifest = manifest
            if attempt == 1:
                _cleanup_binary_download_dir(download_dir)
                active_manifest = initialize_binary_download_manifest(
                    normalized_requested_url=normalized_url,
                    probe=active_probe,
                    download_dir=download_dir,
                )
                save_binary_download_manifest(
                    manifest=active_manifest,
                    manifest_path=manifest_path,
                    shared_store=shared_store,
                    workspace_id=workspace_id,
                    download_key=download_key,
                )
            try:
                manifest = await download_binary_with_ranges(
                    client=client,
                    requested_url=requested_url,
                    response_format=response_format,
                    manifest=active_manifest,
                    probe=active_probe,
                    download_dir=download_dir,
                    manifest_path=manifest_path,
                    shared_store=shared_store,
                    workspace_id=workspace_id,
                    download_key=download_key,
                    cancel_check=cancel_check,
                )
                return build_binary_download_projection(
                    saved_path=Path(manifest.saved_path),
                    content_type=manifest.mime_type,
                    size_bytes=manifest.total_size
                    or Path(manifest.saved_path).stat().st_size,
                    final_url=manifest.final_url,
                    download_mode=BinaryDownloadMode.STREAMING_RANGES,
                    range_supported=True,
                    resume_supported=binary_download_resume_supported(
                        probe=active_probe
                    ),
                )
            except ToolExecutionError as exc:
                last_error = exc
                if exc.error_type not in RANGE_RESET_ERROR_TYPES or attempt == 1:
                    raise

        if last_error is not None:
            raise last_error
        raise RuntimeError("Binary download did not return a result")
    finally:
        if probe_response is not None:
            await probe_response.aclose()


async def probe_binary_download(
    *,
    client: httpx.AsyncClient,
    url: str,
    response_format: str,
) -> BinaryDownloadProbe:
    probe, response = await probe_binary_download_with_response(
        client=client,
        url=url,
        response_format=response_format,
    )
    await response.aclose()
    return probe


async def probe_binary_download_with_response(
    *,
    client: httpx.AsyncClient,
    url: str,
    response_format: str,
) -> tuple[BinaryDownloadProbe, httpx.Response]:
    response = await fetch_url(
        client=client,
        url=url,
        response_format=response_format,
        extra_headers={"Range": RANGE_PROBE_HEADER_VALUE},
    )
    return (
        build_binary_download_probe_from_response(
            response=response,
            requested_url=url,
        ),
        response,
    )


def build_binary_download_probe_from_response(
    *,
    response: httpx.Response,
    requested_url: str,
) -> BinaryDownloadProbe:
    content_range = parse_content_range(response.headers.get("content-range"))
    total_size = _parse_content_length(response)
    range_supported = False
    if response.status_code == 206 and content_range is not None:
        start, end, total = content_range
        if start == 0 and end == 0:
            total_size = total
            range_supported = True
    return BinaryDownloadProbe(
        requested_url=requested_url,
        final_url=str(response.url),
        content_type=normalize_content_type(response.headers.get("content-type", "")),
        total_size=total_size,
        etag=_normalized_optional_header(response.headers.get("etag")),
        last_modified=_normalized_optional_header(
            response.headers.get("last-modified")
        ),
        range_supported=range_supported,
    )


async def download_binary_without_resume(
    *,
    client: httpx.AsyncClient,
    requested_url: str,
    response_format: str,
    manifest: BinaryDownloadManifest,
    download_dir: Path,
    manifest_path: Path,
    shared_store: SharedStateRepository,
    workspace_id: str,
    download_key: str,
    cancel_check: Callable[[], None],
) -> BinaryDownloadManifest:
    temp_path = resolve_binary_temp_path(download_dir)
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    response = await fetch_url(
        client=client,
        url=requested_url,
        response_format=response_format,
    )
    try:
        return await save_non_resumable_binary_response(
            response=response,
            requested_url=requested_url,
            manifest=manifest,
            download_dir=download_dir,
            manifest_path=manifest_path,
            shared_store=shared_store,
            workspace_id=workspace_id,
            download_key=download_key,
            cancel_check=cancel_check,
        )
    finally:
        await response.aclose()


async def download_binary_response_from_response(
    *,
    response: httpx.Response,
    requested_url: str,
    workspace_dir: Path,
    workspace_id: str,
    shared_store: SharedStateRepository,
    cancel_check: Callable[[], None],
) -> ToolResultProjection:
    normalized_url = normalize_requested_download_url(requested_url)
    download_key = build_binary_download_key(normalized_url)
    download_dir = resolve_binary_download_dir(workspace_dir, download_key)
    manifest_path = resolve_binary_manifest_path(download_dir)
    _cleanup_binary_download_dir(download_dir)
    manifest = initialize_binary_download_manifest(
        normalized_requested_url=normalized_url,
        probe=build_binary_download_probe_from_response(
            response=response,
            requested_url=requested_url,
        ),
        download_dir=download_dir,
    )
    manifest = await save_non_resumable_binary_response(
        response=response,
        requested_url=requested_url,
        manifest=manifest,
        download_dir=download_dir,
        manifest_path=manifest_path,
        shared_store=shared_store,
        workspace_id=workspace_id,
        download_key=download_key,
        cancel_check=cancel_check,
    )
    return build_binary_download_projection(
        saved_path=Path(manifest.saved_path),
        content_type=manifest.mime_type,
        size_bytes=manifest.total_size or Path(manifest.saved_path).stat().st_size,
        final_url=manifest.final_url,
        download_mode=BinaryDownloadMode.STREAMING,
        range_supported=False,
        resume_supported=False,
    )


async def save_non_resumable_binary_response(
    *,
    response: httpx.Response,
    requested_url: str,
    manifest: BinaryDownloadManifest,
    download_dir: Path,
    manifest_path: Path,
    shared_store: SharedStateRepository,
    workspace_id: str,
    download_key: str,
    cancel_check: Callable[[], None],
) -> BinaryDownloadManifest:
    temp_path = resolve_binary_temp_path(download_dir)
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    try:
        parsed_length = _parse_content_length(response)
        if parsed_length is not None and parsed_length > MAX_BINARY_DOWNLOAD_SIZE_BYTES:
            _raise_binary_size_limit_error(MAX_BINARY_DOWNLOAD_SIZE_BYTES)
        with temp_path.open("wb") as handle:
            async for chunk in response.aiter_bytes():
                cancel_check()
                if not chunk:
                    continue
                handle.write(chunk)
                bytes_written += len(chunk)
                if bytes_written > MAX_BINARY_DOWNLOAD_SIZE_BYTES:
                    _raise_binary_size_limit_error(MAX_BINARY_DOWNLOAD_SIZE_BYTES)
        finalized_manifest = finalize_non_resumable_manifest(
            manifest=manifest,
            download_dir=download_dir,
            final_url=resolve_webfetch_response_url(response),
            mime_type=normalize_content_type(response.headers.get("content-type", "")),
            size_bytes=bytes_written,
        )
        finalize_binary_temp_file(temp_path, Path(finalized_manifest.saved_path))
        save_binary_download_manifest(
            manifest=finalized_manifest,
            manifest_path=manifest_path,
            shared_store=shared_store,
            workspace_id=workspace_id,
            download_key=download_key,
        )
        return finalized_manifest
    except httpx.TimeoutException as exc:
        raise ToolExecutionError(
            error_type="network_timeout",
            message=f"Web fetch timed out for {urlparse(requested_url).netloc or requested_url}",
            retryable=True,
            details={"url_host": urlparse(requested_url).netloc},
        ) from exc
    except httpx.RequestError as exc:
        raise ToolExecutionError(
            error_type="network_error",
            message=f"Web fetch request failed for {urlparse(requested_url).netloc or requested_url}: {exc}",
            retryable=True,
            details={"url_host": urlparse(requested_url).netloc},
        ) from exc
    except Exception:
        _cleanup_binary_download_dir(download_dir)
        raise


async def download_binary_with_ranges(
    *,
    client: httpx.AsyncClient,
    requested_url: str,
    response_format: str,
    manifest: BinaryDownloadManifest,
    probe: BinaryDownloadProbe,
    download_dir: Path,
    manifest_path: Path,
    shared_store: SharedStateRepository,
    workspace_id: str,
    download_key: str,
    cancel_check: Callable[[], None],
) -> BinaryDownloadManifest:
    state_lock = asyncio.Lock()

    async def persist_progress(
        *,
        segment_index: int,
        downloaded_bytes: int,
        complete: bool,
    ) -> None:
        async with state_lock:
            segment = manifest.segments[segment_index]
            segment.downloaded_bytes = downloaded_bytes
            segment.complete = complete
            manifest.completed = all(current.complete for current in manifest.segments)
            save_binary_download_manifest(
                manifest=manifest,
                manifest_path=manifest_path,
                shared_store=shared_store,
                workspace_id=workspace_id,
                download_key=download_key,
            )

    pending_segments = [
        segment for segment in manifest.segments if not segment.complete
    ]
    if len(pending_segments) == 1:
        try:
            await download_binary_range_segment(
                client=client,
                requested_url=requested_url,
                response_format=response_format,
                probe=probe,
                segment=pending_segments[0],
                download_dir=download_dir,
                cancel_check=cancel_check,
                persist_progress=persist_progress,
            )
        except BinaryDownloadFullResponseFallback as exc:
            try:
                _cleanup_binary_download_dir(download_dir)
                fallback_manifest = initialize_binary_download_manifest(
                    normalized_requested_url=manifest.normalized_requested_url,
                    probe=build_binary_download_probe_from_response(
                        response=exc.response,
                        requested_url=requested_url,
                    ),
                    download_dir=download_dir,
                )
                return await save_non_resumable_binary_response(
                    response=exc.response,
                    requested_url=requested_url,
                    manifest=fallback_manifest,
                    download_dir=download_dir,
                    manifest_path=manifest_path,
                    shared_store=shared_store,
                    workspace_id=workspace_id,
                    download_key=download_key,
                    cancel_check=cancel_check,
                )
            finally:
                await exc.response.aclose()
    elif pending_segments:
        await asyncio.gather(
            *[
                download_binary_range_segment(
                    client=client,
                    requested_url=requested_url,
                    response_format=response_format,
                    probe=probe,
                    segment=segment,
                    download_dir=download_dir,
                    cancel_check=cancel_check,
                    persist_progress=persist_progress,
                )
                for segment in pending_segments
            ]
        )
    finalized_manifest = finalize_resumable_manifest(
        manifest=manifest,
        probe=probe,
        download_dir=download_dir,
    )
    save_binary_download_manifest(
        manifest=finalized_manifest,
        manifest_path=manifest_path,
        shared_store=shared_store,
        workspace_id=workspace_id,
        download_key=download_key,
    )
    return finalized_manifest


async def download_binary_range_segment(
    *,
    client: httpx.AsyncClient,
    requested_url: str,
    response_format: str,
    probe: BinaryDownloadProbe,
    segment: BinaryDownloadSegment,
    download_dir: Path,
    cancel_check: Callable[[], None],
    persist_progress: Callable[..., asyncio.Future | None] | Callable[..., object],
) -> None:
    segment_length = binary_segment_length(segment)
    if segment.downloaded_bytes >= segment_length:
        segment.downloaded_bytes = segment_length
        segment.complete = True
        await _maybe_await(
            persist_progress(
                segment_index=segment.index,
                downloaded_bytes=segment_length,
                complete=True,
            )
        )
        return

    segment_path = resolve_binary_segment_path(download_dir, segment)
    segment_path.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = min(segment.downloaded_bytes, segment_length)
    range_start = segment.start + bytes_written
    range_end = segment.end
    extra_headers = {"Range": f"bytes={range_start}-{range_end}"}
    if_range = build_if_range_header(probe)
    if if_range is not None:
        extra_headers["If-Range"] = if_range

    response = await fetch_url(
        client=client,
        url=requested_url,
        response_format=response_format,
        extra_headers=extra_headers,
        allowed_error_status_codes={416},
    )
    bytes_since_flush = 0
    keep_response_open = False
    try:
        expected_total = probe.total_size
        if response.status_code == 416:
            raise ToolExecutionError(
                error_type="range_resume_mismatch",
                message="Range download could not be resumed because the upstream response rejected the requested byte range.",
                retryable=True,
            )
        if (
            response.status_code == 200
            and expected_total is not None
            and segment_length == expected_total
        ):
            keep_response_open = True
            raise BinaryDownloadFullResponseFallback(response)
        content_range = parse_content_range(response.headers.get("content-range"))
        if response.status_code != 206 or content_range is None:
            raise ToolExecutionError(
                error_type="range_resume_mismatch",
                message="Range download could not be resumed because the upstream response changed.",
                retryable=True,
            )
        if (
            expected_total is None
            or content_range[0] != range_start
            or content_range[1] != range_end
            or content_range[2] != expected_total
        ):
            raise ToolExecutionError(
                error_type="range_response_invalid",
                message="Range download returned an invalid Content-Range header.",
                retryable=True,
            )

        with segment_path.open("ab") as handle:
            async for chunk in response.aiter_bytes():
                cancel_check()
                if not chunk:
                    continue
                handle.write(chunk)
                bytes_written += len(chunk)
                bytes_since_flush += len(chunk)
                if bytes_written > segment_length:
                    raise ToolExecutionError(
                        error_type="range_response_invalid",
                        message="Range download returned more bytes than requested.",
                        retryable=True,
                    )
                if bytes_since_flush >= STATE_FLUSH_INTERVAL_BYTES:
                    await _maybe_await(
                        persist_progress(
                            segment_index=segment.index,
                            downloaded_bytes=bytes_written,
                            complete=False,
                        )
                    )
                    bytes_since_flush = 0
        complete = bytes_written == segment_length
        await _maybe_await(
            persist_progress(
                segment_index=segment.index,
                downloaded_bytes=bytes_written,
                complete=complete,
            )
        )
        if not complete:
            raise ToolExecutionError(
                error_type="network_error",
                message=f"Range download interrupted for {urlparse(requested_url).netloc or requested_url}",
                retryable=True,
                details={"url_host": urlparse(requested_url).netloc},
            )
    except httpx.TimeoutException as exc:
        await _maybe_await(
            persist_progress(
                segment_index=segment.index,
                downloaded_bytes=bytes_written,
                complete=False,
            )
        )
        raise ToolExecutionError(
            error_type="network_timeout",
            message=f"Web fetch timed out for {urlparse(requested_url).netloc or requested_url}",
            retryable=True,
            details={"url_host": urlparse(requested_url).netloc},
        ) from exc
    except httpx.RequestError as exc:
        await _maybe_await(
            persist_progress(
                segment_index=segment.index,
                downloaded_bytes=bytes_written,
                complete=False,
            )
        )
        raise ToolExecutionError(
            error_type="network_error",
            message=f"Web fetch request failed for {urlparse(requested_url).netloc or requested_url}: {exc}",
            retryable=True,
            details={"url_host": urlparse(requested_url).netloc},
        ) from exc
    finally:
        if not keep_response_open:
            await response.aclose()


def initialize_binary_download_manifest(
    *,
    normalized_requested_url: str,
    probe: BinaryDownloadProbe,
    download_dir: Path,
) -> BinaryDownloadManifest:
    suffix = sanitize_file_extension(probe.final_url, probe.content_type)
    final_path = resolve_binary_final_path(download_dir, suffix)
    segments = (
        plan_binary_download_segments(probe.total_size)
        if probe.range_supported and probe.total_size is not None
        else []
    )
    return BinaryDownloadManifest(
        requested_url=probe.requested_url,
        normalized_requested_url=normalized_requested_url,
        final_url=probe.final_url,
        mime_type=probe.content_type,
        suffix=suffix,
        total_size=probe.total_size,
        etag=probe.etag,
        last_modified=probe.last_modified,
        range_supported=probe.range_supported,
        segment_count=len(segments),
        segments=segments,
        saved_path=str(final_path),
        completed=False,
    )


def plan_binary_download_segments(
    total_size: int | None,
) -> list[BinaryDownloadSegment]:
    if total_size is None or total_size <= 0:
        return []
    segment_count = (
        PARALLEL_DOWNLOAD_SEGMENT_COUNT
        if total_size >= PARALLEL_DOWNLOAD_THRESHOLD_BYTES
        else 1
    )
    segment_size = max(
        MIN_SEGMENT_SIZE_BYTES,
        math.ceil(total_size / segment_count),
    )
    segments: list[BinaryDownloadSegment] = []
    start = 0
    index = 0
    while start < total_size:
        end = min(total_size - 1, start + segment_size - 1)
        segments.append(
            BinaryDownloadSegment(
                index=index,
                start=start,
                end=end,
                file_name=f"{index:04d}.part",
            )
        )
        index += 1
        start = end + 1
    return segments


def reconcile_binary_download_manifest(
    manifest: BinaryDownloadManifest,
    download_dir: Path,
) -> BinaryDownloadManifest | None:
    if manifest.completed:
        final_path = Path(manifest.saved_path)
        if not final_path.exists():
            return None
        if (
            manifest.total_size is not None
            and final_path.stat().st_size != manifest.total_size
        ):
            return None
        return manifest

    updated_segments: list[BinaryDownloadSegment] = []
    for segment in manifest.segments:
        segment_path = resolve_binary_segment_path(download_dir, segment)
        expected_length = binary_segment_length(segment)
        file_size = segment_path.stat().st_size if segment_path.exists() else 0
        if file_size > expected_length:
            return None
        updated_segments.append(
            segment.model_copy(
                update={
                    "downloaded_bytes": file_size,
                    "complete": file_size == expected_length,
                }
            )
        )
    return manifest.model_copy(
        update={
            "segments": updated_segments,
            "completed": all(segment.complete for segment in updated_segments),
        }
    )


def binary_download_manifest_matches_probe(
    *,
    manifest: BinaryDownloadManifest,
    probe: BinaryDownloadProbe,
) -> bool:
    if manifest.normalized_requested_url != normalize_requested_download_url(
        probe.requested_url
    ):
        return False
    if manifest.final_url != probe.final_url:
        return False
    if manifest.mime_type != probe.content_type:
        return False
    if not binary_download_has_strong_validator(
        etag=probe.etag,
        last_modified=probe.last_modified,
    ):
        return False
    manifest_strong_etag = normalize_strong_etag(manifest.etag)
    probe_strong_etag = normalize_strong_etag(probe.etag)
    if manifest_strong_etag is not None or probe_strong_etag is not None:
        return (
            manifest_strong_etag is not None
            and manifest_strong_etag == probe_strong_etag
        )
    if manifest.last_modified is not None or probe.last_modified is not None:
        return (
            manifest.last_modified is not None
            and manifest.last_modified == probe.last_modified
        )
    return False


def binary_download_manifest_is_usable(
    *,
    manifest: BinaryDownloadManifest,
    probe: BinaryDownloadProbe,
) -> bool:
    if not binary_download_manifest_matches_probe(manifest=manifest, probe=probe):
        return False
    final_path = Path(manifest.saved_path)
    if not final_path.exists():
        return False
    if (
        manifest.total_size is not None
        and final_path.stat().st_size != manifest.total_size
    ):
        return False
    return True


def finalize_non_resumable_manifest(
    *,
    manifest: BinaryDownloadManifest,
    download_dir: Path,
    final_url: str,
    mime_type: str,
    size_bytes: int,
) -> BinaryDownloadManifest:
    suffix = sanitize_file_extension(final_url, mime_type)
    final_path = resolve_binary_final_path(download_dir, suffix)
    return manifest.model_copy(
        update={
            "final_url": final_url,
            "mime_type": mime_type,
            "suffix": suffix,
            "total_size": size_bytes,
            "saved_path": str(final_path),
            "range_supported": False,
            "segment_count": 0,
            "segments": [],
            "completed": True,
        }
    )


def finalize_resumable_manifest(
    *,
    manifest: BinaryDownloadManifest,
    probe: BinaryDownloadProbe,
    download_dir: Path,
) -> BinaryDownloadManifest:
    if not all(segment.complete for segment in manifest.segments):
        raise ToolExecutionError(
            error_type="network_error",
            message=f"Range download interrupted for {urlparse(probe.requested_url).netloc or probe.requested_url}",
            retryable=True,
            details={"url_host": urlparse(probe.requested_url).netloc},
        )
    final_path = resolve_binary_final_path(download_dir, manifest.suffix)
    temp_final_path = resolve_binary_merge_path(download_dir, manifest.suffix)
    temp_final_path.parent.mkdir(parents=True, exist_ok=True)
    with temp_final_path.open("wb") as handle:
        for segment in manifest.segments:
            with resolve_binary_segment_path(download_dir, segment).open(
                "rb"
            ) as source:
                shutil.copyfileobj(source, handle, length=DOWNLOAD_CHUNK_SIZE_BYTES)
    if (
        probe.total_size is not None
        and temp_final_path.stat().st_size != probe.total_size
    ):
        temp_final_path.unlink(missing_ok=True)
        raise ToolExecutionError(
            error_type="range_response_invalid",
            message="Range download assembled to an unexpected size.",
            retryable=True,
        )
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_final_path.replace(final_path)
    shutil.rmtree(resolve_binary_segments_dir(download_dir), ignore_errors=True)
    return manifest.model_copy(
        update={
            "saved_path": str(final_path),
            "completed": True,
            "total_size": probe.total_size,
            "final_url": probe.final_url,
            "mime_type": probe.content_type,
            "etag": probe.etag,
            "last_modified": probe.last_modified,
        }
    )


def build_binary_download_projection(
    *,
    saved_path: Path,
    content_type: str,
    size_bytes: int,
    final_url: str,
    download_mode: BinaryDownloadMode,
    range_supported: bool,
    resume_supported: bool,
) -> ToolResultProjection:
    visible_data: dict[str, JsonValue] = {
        "output": "Binary content saved to file",
        "saved_path": str(saved_path),
        "mime_type": content_type,
        "size_bytes": size_bytes,
        "final_url": final_url,
        "download_mode": download_mode.value,
        "streamed_to_disk": download_mode is not BinaryDownloadMode.BUFFERED,
        "range_supported": range_supported,
        "resume_supported": resume_supported,
    }
    return ToolResultProjection(
        visible_data=visible_data,
        internal_data=visible_data,
    )


def normalize_requested_download_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


def build_binary_download_key(normalized_url: str) -> str:
    return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()


def resolve_binary_download_dir(workspace_dir: Path, download_key: str) -> Path:
    return resolve_webfetch_output_dir(workspace_dir) / "downloads" / download_key


def resolve_binary_manifest_path(download_dir: Path) -> Path:
    return download_dir / "manifest.json"


def resolve_binary_segments_dir(download_dir: Path) -> Path:
    return download_dir / "segments"


def resolve_binary_segment_path(
    download_dir: Path,
    segment: BinaryDownloadSegment,
) -> Path:
    return resolve_binary_segments_dir(download_dir) / segment.file_name


def resolve_binary_final_path(download_dir: Path, suffix: str) -> Path:
    return download_dir / f"payload{suffix}"


def resolve_binary_temp_path(download_dir: Path) -> Path:
    return download_dir / "payload.part"


def resolve_binary_merge_path(download_dir: Path, suffix: str) -> Path:
    return download_dir / f"payload{suffix}.tmp"


def load_binary_download_manifest(manifest_path: Path) -> BinaryDownloadManifest | None:
    if not manifest_path.exists():
        return None
    try:
        return BinaryDownloadManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except Exception:
        return None


def save_binary_download_manifest(
    *,
    manifest: BinaryDownloadManifest,
    manifest_path: Path,
    shared_store: SharedStateRepository,
    workspace_id: str,
    download_key: str,
) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(manifest_path, manifest.model_dump_json(indent=2))
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.WORKSPACE, scope_id=workspace_id),
            key=f"{WEBFETCH_DOWNLOAD_PREFIX}:{download_key}",
            value_json=manifest.model_dump_json(),
        )
    )


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    try:
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def finalize_binary_temp_file(temp_path: Path, final_path: Path) -> None:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.replace(final_path)


def _cleanup_binary_download_dir(download_dir: Path) -> None:
    shutil.rmtree(download_dir, ignore_errors=True)


def build_if_range_header(probe: BinaryDownloadProbe) -> str | None:
    strong_etag = normalize_strong_etag(probe.etag)
    if strong_etag is not None:
        return strong_etag
    if probe.last_modified:
        return probe.last_modified
    return None


def binary_download_resume_supported(*, probe: BinaryDownloadProbe) -> bool:
    return (
        probe.range_supported
        and probe.total_size is not None
        and build_if_range_header(probe) is not None
    )


def binary_download_has_strong_validator(
    *,
    etag: str | None,
    last_modified: str | None,
) -> bool:
    return normalize_strong_etag(etag) is not None or last_modified is not None


def normalize_strong_etag(etag: str | None) -> str | None:
    if etag is None:
        return None
    stripped = etag.strip()
    if not stripped:
        return None
    if stripped[:2].lower() == "w/":
        return None
    return stripped


def parse_content_range(content_range: str | None) -> tuple[int, int, int] | None:
    if not content_range:
        return None
    match = re.match(r"^bytes (\d+)-(\d+)/(\d+)$", content_range.strip())
    if match is None:
        return None
    start = int(match.group(1))
    end = int(match.group(2))
    total = int(match.group(3))
    if start > end or end >= total:
        return None
    return start, end, total


def binary_segment_length(segment: BinaryDownloadSegment) -> int:
    return segment.end - segment.start + 1


def _normalized_optional_header(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


async def _maybe_await(value: object) -> None:
    if asyncio.isfuture(value) or asyncio.iscoroutine(value):
        await value


def build_structured_projection(
    *,
    final_url: str,
    content_type: str,
    body: str,
    extract: WebFetchExtractMode,
    item_limit: int,
) -> dict[str, JsonValue]:
    root = parse_xml_root(body=body, extract=extract)
    if extract is WebFetchExtractMode.FEED:
        document = parse_feed_document(
            root=root, final_url=final_url, item_limit=item_limit
        )
        return build_structured_result_payload(
            final_url=final_url,
            content_type=content_type,
            summary=build_feed_summary(document),
            payload=document.model_dump(mode="json"),
        )
    if extract is WebFetchExtractMode.OPML:
        document = parse_opml_document(
            root=root, final_url=final_url, item_limit=item_limit
        )
        return build_structured_result_payload(
            final_url=final_url,
            content_type=content_type,
            summary=build_opml_summary(document),
            payload=document.model_dump(mode="json"),
        )
    raise ValueError(f"Unsupported extract mode: {extract}")


def build_structured_result_payload(
    *,
    final_url: str,
    content_type: str,
    summary: str,
    payload: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    return {
        "output": summary,
        "final_url": final_url,
        "content_type": content_type,
        **payload,
    }


def parse_xml_root(*, body: str, extract: WebFetchExtractMode) -> Element:
    try:
        return safe_element_tree.fromstring(body)
    except ParseError as exc:
        raise ValueError(
            f"Response is not valid XML for extract={extract.value!r}"
        ) from exc


def parse_feed_document(
    *,
    root: Element,
    final_url: str,
    item_limit: int,
) -> ParsedFeedDocument:
    root_name = local_name(root.tag)
    if root_name == "feed":
        return parse_atom_feed(root=root, final_url=final_url, item_limit=item_limit)
    if root_name in {"rss", "rdf"}:
        return parse_rss_feed(root=root, final_url=final_url, item_limit=item_limit)
    raise ValueError("Response is XML, but not an RSS or Atom feed")


def parse_atom_feed(
    *,
    root: Element,
    final_url: str,
    item_limit: int,
) -> ParsedFeedDocument:
    entries = [
        parse_atom_entry(entry=entry, final_url=final_url)
        for entry in iter_children(root, "entry")
    ]
    total_count = len(entries)
    sliced_entries = entries[:item_limit]
    return ParsedFeedDocument(
        title=find_child_text_content(root, "title"),
        feed_url=pick_atom_link(root=root, final_url=final_url, preferred_rel="self"),
        site_url=pick_atom_link(
            root=root, final_url=final_url, preferred_rel="alternate"
        ),
        updated=find_child_text_content(root, "updated"),
        count=len(sliced_entries),
        total_count=total_count,
        truncated=total_count > item_limit,
        entries=sliced_entries,
    )


def parse_atom_entry(*, entry: Element, final_url: str) -> FeedEntry:
    return FeedEntry(
        title=find_child_text_content(entry, "title"),
        link=pick_atom_link(root=entry, final_url=final_url, preferred_rel="alternate"),
        published=find_child_text_content(entry, "published"),
        updated=find_child_text_content(entry, "updated"),
        summary=extract_summary_text(
            first_non_empty(
                find_child_text_content(entry, "summary"),
                find_child_text_content(entry, "content"),
            )
        ),
    )


def parse_rss_feed(
    *,
    root: Element,
    final_url: str,
    item_limit: int,
) -> ParsedFeedDocument:
    channel = find_first_child(root, "channel")
    if channel is None:
        raise ValueError("Response is XML, but does not contain an RSS channel")

    if local_name(root.tag) == "rdf":
        items_parent = root
    else:
        items_parent = channel

    entries = [
        parse_rss_item(item=item, final_url=final_url)
        for item in iter_children(items_parent, "item")
    ]
    total_count = len(entries)
    sliced_entries = entries[:item_limit]
    return ParsedFeedDocument(
        title=find_child_text_content(channel, "title"),
        feed_url=first_non_empty(
            resolve_link(find_child_href(channel, "link"), base_url=final_url),
            final_url,
        ),
        site_url=resolve_link(
            find_child_text_content(channel, "link"), base_url=final_url
        ),
        updated=first_non_empty(
            find_child_text_content(channel, "lastBuildDate"),
            find_child_text_content(channel, "pubDate"),
        ),
        count=len(sliced_entries),
        total_count=total_count,
        truncated=total_count > item_limit,
        entries=sliced_entries,
    )


def parse_rss_item(*, item: Element, final_url: str) -> FeedEntry:
    return FeedEntry(
        title=find_child_text_content(item, "title"),
        link=resolve_link(find_child_text_content(item, "link"), base_url=final_url),
        published=find_child_text_content(item, "pubDate"),
        updated=first_non_empty(
            find_child_text_content(item, "updated"),
            find_child_text_content(item, "pubDate"),
        ),
        summary=extract_summary_text(
            first_non_empty(
                find_child_text_content(item, "description"),
                find_child_text_content(item, "encoded"),
            )
        ),
    )


def parse_opml_document(
    *,
    root: Element,
    final_url: str,
    item_limit: int,
) -> ParsedOpmlDocument:
    if local_name(root.tag) != "opml":
        raise ValueError("Response is XML, but not an OPML document")
    body = find_first_child(root, "body")
    if body is None:
        raise ValueError("OPML document is missing a body element")

    feeds: list[OpmlFeedEntry] = []
    for outline in iter_children(body, "outline"):
        collect_opml_feeds(
            outline=outline,
            final_url=final_url,
            group_path=[],
            feeds=feeds,
        )

    total_count = len(feeds)
    sliced_feeds = feeds[:item_limit]
    return ParsedOpmlDocument(
        title=find_nested_text(root, ["head", "title"]),
        count=len(sliced_feeds),
        total_count=total_count,
        truncated=total_count > item_limit,
        feeds=sliced_feeds,
    )


def collect_opml_feeds(
    *,
    outline: Element,
    final_url: str,
    group_path: list[str],
    feeds: list[OpmlFeedEntry],
) -> None:
    label = first_non_empty(
        normalize_text(outline.attrib.get("text")),
        normalize_text(outline.attrib.get("title")),
    )
    xml_url = resolve_link(outline.attrib.get("xmlUrl"), base_url=final_url)
    if xml_url is not None:
        feeds.append(
            OpmlFeedEntry(
                text=normalize_text(outline.attrib.get("text")),
                title=normalize_text(outline.attrib.get("title")),
                feed_type=normalize_text(outline.attrib.get("type")),
                xml_url=xml_url,
                html_url=resolve_link(
                    outline.attrib.get("htmlUrl"), base_url=final_url
                ),
                group_path=list(group_path),
            )
        )

    child_group_path = group_path
    if xml_url is None and label is not None:
        child_group_path = [*group_path, label]
    for child in iter_children(outline, "outline"):
        collect_opml_feeds(
            outline=child,
            final_url=final_url,
            group_path=child_group_path,
            feeds=feeds,
        )


def build_feed_summary(document: ParsedFeedDocument) -> str:
    if document.truncated:
        return (
            f"Parsed feed {format_document_label(document.title)} with "
            f"{document.count} of {document.total_count} entries."
        )
    return (
        f"Parsed feed {format_document_label(document.title)} with "
        f"{document.count} entries."
    )


def build_opml_summary(document: ParsedOpmlDocument) -> str:
    if document.truncated:
        return (
            f"Parsed OPML {format_document_label(document.title)} with "
            f"{document.count} of {document.total_count} feeds."
        )
    return (
        f"Parsed OPML {format_document_label(document.title)} with "
        f"{document.count} feeds."
    )


def format_document_label(value: str | None) -> str:
    normalized = normalize_text(value)
    if normalized is None:
        return "document"
    return f'"{normalized}"'


def pick_atom_link(
    *,
    root: Element,
    final_url: str,
    preferred_rel: str,
) -> str | None:
    fallback: str | None = None
    for child in iter_children(root, "link"):
        href = normalize_text(child.attrib.get("href"))
        if href is None:
            continue
        resolved = resolve_link(href, base_url=final_url)
        if resolved is None:
            continue
        rel = normalize_text(child.attrib.get("rel"))
        if rel == preferred_rel:
            return resolved
        if fallback is None:
            fallback = resolved
    return fallback


def extract_summary_text(value: str | None) -> str | None:
    normalized = normalize_text(value)
    if normalized is None:
        return None
    extracted = extract_text_from_html(normalized)
    collapsed = " ".join(extracted.split())
    compact = re.sub(r"\s+([,.;:!?])", r"\1", collapsed)
    return compact or None


def find_nested_text(root: Element, path: list[str]) -> str | None:
    current: Element | None = root
    for name in path:
        if current is None:
            return None
        current = find_first_child(current, name)
    if current is None or current.text is None:
        return None
    return normalize_text(current.text)


def find_child_text_content(root: Element, name: str) -> str | None:
    child = find_first_child(root, name)
    if child is None or child.text is None:
        return None
    return normalize_text(child.text)


def find_child_href(root: Element, name: str) -> str | None:
    for child in iter_children(root, name):
        href = normalize_text(child.attrib.get("href"))
        if href is not None:
            return href
    return None


def find_first_child(root: Element, name: str) -> Element | None:
    expected = normalize_lookup_name(name)
    for child in root:
        if local_name(child.tag) == expected:
            return child
    return None


def iter_children(root: Element, name: str) -> list[Element]:
    expected = normalize_lookup_name(name)
    return [child for child in root if local_name(child.tag) == expected]


def normalize_lookup_name(name: str) -> str:
    return name.split(":", 1)[-1].strip().lower()


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1].strip().lower()
    return tag.strip().lower()


def resolve_link(value: str | None, *, base_url: str) -> str | None:
    normalized = normalize_text(value)
    if normalized is None:
        return None
    return urljoin(base_url, normalized)


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    collapsed = " ".join(value.split())
    return collapsed or None


def first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value is not None and value.strip():
            return value
    return None
