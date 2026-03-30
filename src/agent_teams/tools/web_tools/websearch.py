# -*- coding: utf-8 -*-
from __future__ import annotations

from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai import Agent

from agent_teams.env.web_config_models import WebProvider
from agent_teams.net.clients import create_async_http_client
from agent_teams.tools._description_loader import load_tool_description
from agent_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolExecutionError,
    ToolResultProjection,
    execute_tool,
)
from agent_teams.tools.web_tools.common import load_runtime_web_config

EXA_BASE_URL = "https://mcp.exa.ai"
EXA_PATH = "/mcp"
EXA_TOOL_NAME = "web_search_exa"
DEFAULT_NUM_RESULTS = 8
DEFAULT_LIVECRAWL = "fallback"
DEFAULT_SEARCH_TYPE = "auto"
DEFAULT_TIMEOUT_SECONDS = 25.0
DESCRIPTION = load_tool_description(__file__)


class ExaSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    numResults: int
    livecrawl: str
    type: str
    contextMaxCharacters: int | None = None


class ExaSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jsonrpc: str = "2.0"
    id: int = 1
    method: str = "tools/call"
    params: dict[str, JsonValue]


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def websearch(
        ctx: ToolContext,
        query: str,
        num_results: int | None = None,
        livecrawl: str | None = None,
        search_type: str | None = None,
        context_max_characters: int | None = None,
    ) -> dict[str, JsonValue]:
        """Search the web and return a text summary."""

        async def _action() -> ToolResultProjection:
            config = load_runtime_web_config()
            if config.provider != WebProvider.EXA:
                raise ValueError(f"Unsupported web provider: {config.provider.value}")

            payload = build_exa_search_request(
                query=query,
                num_results=num_results,
                livecrawl=livecrawl,
                search_type=search_type,
                context_max_characters=context_max_characters,
            )
            endpoint = build_exa_search_url(api_key=config.api_key)
            async with create_async_http_client(
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
                follow_redirects=True,
            ) as client:
                response_text = await fetch_exa_search_response(
                    client=client,
                    endpoint=endpoint,
                    payload=payload,
                )

            output = extract_search_output(response_text)
            visible_data: dict[str, JsonValue] = {
                "output": output
                or "No search results found. Please try a different query.",
                "backend": config.provider.value,
                "query": query,
            }
            return ToolResultProjection(
                visible_data=visible_data,
                internal_data={
                    **visible_data,
                    "endpoint": endpoint,
                },
            )

        return await execute_tool(
            ctx,
            tool_name="websearch",
            args_summary={
                "query": query,
                "num_results": num_results,
                "livecrawl": livecrawl,
                "search_type": search_type,
                "context_max_characters": context_max_characters,
            },
            action=_action,
        )


def build_exa_search_request(
    *,
    query: str,
    num_results: int | None,
    livecrawl: str | None,
    search_type: str | None,
    context_max_characters: int | None,
) -> dict[str, JsonValue]:
    arguments = ExaSearchArguments(
        query=query,
        numResults=num_results or DEFAULT_NUM_RESULTS,
        livecrawl=livecrawl or DEFAULT_LIVECRAWL,
        type=search_type or DEFAULT_SEARCH_TYPE,
        contextMaxCharacters=context_max_characters,
    )
    request = ExaSearchRequest(
        params={
            "name": EXA_TOOL_NAME,
            "arguments": arguments.model_dump(mode="json", exclude_none=True),
        }
    )
    return request.model_dump(mode="json")


def build_exa_search_url(*, api_key: str | None) -> str:
    base_url = f"{EXA_BASE_URL}{EXA_PATH}"
    if not api_key:
        return base_url
    return f"{base_url}?{urlencode({'exaApiKey': api_key})}"


async def fetch_exa_search_response(
    *,
    client: httpx.AsyncClient,
    endpoint: str,
    payload: dict[str, JsonValue],
) -> str:
    endpoint_host = httpx.URL(endpoint).host or ""
    try:
        response = await client.post(
            endpoint,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    except httpx.TimeoutException as exc:
        raise ToolExecutionError(
            error_type="network_timeout",
            message="Exa web search timed out",
            retryable=True,
            details={
                "provider": WebProvider.EXA.value,
                "endpoint_host": endpoint_host,
            },
        ) from exc
    except httpx.RequestError as exc:
        raise ToolExecutionError(
            error_type="network_error",
            message=f"Exa web search request failed: {exc}",
            retryable=True,
            details={
                "provider": WebProvider.EXA.value,
                "endpoint_host": endpoint_host,
            },
        ) from exc

    if response.status_code >= 400:
        raise ToolExecutionError(
            error_type=_search_status_error_type(response.status_code),
            message=_search_status_error_message(response),
            retryable=response.status_code in {429} or response.status_code >= 500,
            details={
                "provider": WebProvider.EXA.value,
                "endpoint_host": endpoint_host,
                "status_code": response.status_code,
            },
        )
    return response.text


def _search_status_error_type(status_code: int) -> str:
    if status_code == 401:
        return "auth_error"
    if status_code == 403:
        return "source_access_denied"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "upstream_unavailable"
    return "upstream_error"


def _search_status_error_message(response: httpx.Response) -> str:
    detail = response.text.strip()
    base = f"Exa web search returned HTTP {response.status_code}"
    if detail:
        return f"{base}: {detail}"
    return base


def extract_search_output(response_text: str) -> str | None:
    for raw_line in response_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        event = _parse_search_event(payload)
        result = event.get("result")
        if not isinstance(result, dict):
            continue
        content = result.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


def _parse_search_event(payload: str) -> dict[str, JsonValue]:
    import json

    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError("Invalid SSE payload from web search backend")
    normalized: dict[str, JsonValue] = {}
    for key, value in data.items():
        if isinstance(key, str):
            normalized[key] = _normalize_json_value(value)
    return normalized


def _normalize_json_value(value: object) -> JsonValue:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            normalized[str(key)] = _normalize_json_value(item)
        return normalized
    return str(value)
