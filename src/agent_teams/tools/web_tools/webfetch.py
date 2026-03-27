# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import StrEnum
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse
from xml.etree.ElementTree import Element, ParseError

import defusedxml.ElementTree as safe_element_tree
import httpx
from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai import Agent

from agent_teams.net.clients import create_async_http_client
from agent_teams.tools._description_loader import load_tool_description
from agent_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolResultProjection,
    execute_tool,
)
from agent_teams.tools.web_tools.common import (
    MAX_TEXT_OUTPUT_CHARS,
    extract_text_from_html,
    convert_html_to_markdown,
    resolve_webfetch_output_dir,
    sanitize_file_extension,
)

MAX_RESPONSE_SIZE_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 120
DEFAULT_FORMAT = "markdown"
DEFAULT_ITEM_LIMIT = 20
MAX_ITEM_LIMIT = 50
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
FALLBACK_USER_AGENT = "agent-teams"
DESCRIPTION = load_tool_description(__file__)


class WebFetchExtractMode(StrEnum):
    NONE = "none"
    FEED = "feed"
    OPML = "opml"


class ParsedDocumentKind(StrEnum):
    FEED = "feed"
    OPML = "opml"


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

        async def _action() -> ToolResultProjection:
            validate_web_url(url)
            resolved_timeout = normalize_timeout_seconds(timeout)
            resolved_extract = normalize_extract_mode(extract)
            resolved_item_limit = normalize_item_limit(item_limit)
            async with create_async_http_client(
                timeout_seconds=float(resolved_timeout),
                follow_redirects=True,
            ) as client:
                response = await fetch_url(
                    client=client,
                    url=url,
                    response_format=format,
                )
            content_type = normalize_content_type(
                response.headers.get("content-type", "")
            )
            body = await read_response_body(response)
            return build_webfetch_projection(
                workspace_dir=ctx.deps.workspace.locations.workspace_dir,
                tool_call_id=ctx.tool_call_id or "webfetch",
                requested_url=url,
                final_url=str(response.url),
                response_format=format,
                content_type=content_type,
                body=body,
                extract=resolved_extract,
                item_limit=resolved_item_limit,
            )

        return await execute_tool(
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
        )


def validate_web_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("URL must include a host")


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
) -> httpx.Response:
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": build_accept_header(response_format),
        "Accept-Language": "en-US,en;q=0.9",
    }
    response = await _perform_request(client=client, url=url, headers=headers)
    if (
        response.status_code == 403
        and response.headers.get("cf-mitigated") == "challenge"
    ):
        retry_headers = dict(headers)
        retry_headers["User-Agent"] = FALLBACK_USER_AGENT
        response = await _perform_request(client=client, url=url, headers=retry_headers)
    if response.status_code >= 400:
        raise RuntimeError(f"Request failed with status code: {response.status_code}")
    enforce_content_length_limit(response)
    return response


async def _perform_request(
    *,
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
) -> httpx.Response:
    try:
        return await client.get(url, headers=headers)
    except httpx.TimeoutException as exc:
        raise RuntimeError("Request timed out") from exc
    except httpx.RequestError as exc:
        raise RuntimeError(f"Request failed: {exc}") from exc


def enforce_content_length_limit(response: httpx.Response) -> None:
    content_length = response.headers.get("content-length")
    if content_length is None:
        return
    try:
        parsed_length = int(content_length)
    except ValueError:
        return
    if parsed_length > MAX_RESPONSE_SIZE_BYTES:
        raise RuntimeError("Response too large (exceeds 5MB limit)")


async def read_response_body(response: httpx.Response) -> bytes:
    body = await response.aread()
    if len(body) > MAX_RESPONSE_SIZE_BYTES:
        raise RuntimeError("Response too large (exceeds 5MB limit)")
    return body


def normalize_content_type(content_type_header: str) -> str:
    return content_type_header.split(";", 1)[0].strip().lower()


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
    visible_data: dict[str, JsonValue] = {
        "output": "Binary content saved to file",
        "saved_path": str(output_path),
        "mime_type": content_type,
        "size_bytes": len(body),
        "final_url": final_url,
    }
    return ToolResultProjection(
        visible_data=visible_data,
        internal_data=visible_data,
    )


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
