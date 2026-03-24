# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import httpx
from pydantic import JsonValue
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
    convert_html_to_markdown,
    extract_text_from_html,
    resolve_webfetch_output_dir,
    sanitize_file_extension,
)

MAX_RESPONSE_SIZE_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 120
DEFAULT_FORMAT = "markdown"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
FALLBACK_USER_AGENT = "agent-teams"
DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def webfetch(
        ctx: ToolContext,
        url: str,
        format: str = DEFAULT_FORMAT,
        timeout: int | None = None,
    ) -> dict[str, JsonValue]:
        """Fetch web content and return text or a saved file path."""

        async def _action() -> ToolResultProjection:
            validate_web_url(url)
            resolved_timeout = normalize_timeout_seconds(timeout)
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
            )

        return await execute_tool(
            ctx,
            tool_name="webfetch",
            args_summary={"url": url, "format": format, "timeout": timeout},
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
) -> ToolResultProjection:
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
    if content_type.startswith("text/"):
        return False
    if content_type in {
        "application/json",
        "application/xml",
        "application/xhtml+xml",
        "image/svg+xml",
    }:
        return False
    return True


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
