# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from typing import cast
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from relay_teams.external_agents.models import (
    ExternalAgentConfig,
    ExternalAgentProtocol,
    ExternalAgentTestResult,
    StreamableHttpTransportConfig,
)
from relay_teams.net.clients import create_async_http_client

_A2A_AGENT_CARD_WELL_KNOWN_PATH = "/.well-known/agent.json"
_A2A_TERMINAL_TASK_STATES = {"completed", "canceled", "failed", "rejected"}
_A2A_POLL_INTERVAL_SECONDS = 1.0
_A2A_MAX_POLL_ATTEMPTS = 60


class A2aClientError(RuntimeError):
    pass


class A2aAgentCapabilities(BaseModel):
    model_config = ConfigDict(extra="ignore")

    streaming: bool = False
    push_notifications: bool = Field(default=False, alias="pushNotifications")
    state_transition_history: bool = Field(
        default=False,
        alias="stateTransitionHistory",
    )


class A2aAgentCard(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    protocol_version: str = Field(default="", alias="protocolVersion")
    name: str
    description: str = ""
    url: str
    version: str = ""
    capabilities: A2aAgentCapabilities = Field(default_factory=A2aAgentCapabilities)
    default_input_modes: tuple[str, ...] = Field(
        default=(),
        alias="defaultInputModes",
    )
    default_output_modes: tuple[str, ...] = Field(
        default=(),
        alias="defaultOutputModes",
    )


class A2aPromptResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    task_id: str | None = None
    context_id: str | None = None
    state: str | None = None


async def probe_a2a_agent(config: ExternalAgentConfig) -> ExternalAgentTestResult:
    try:
        async with _build_a2a_client(config) as client:
            try:
                card = await client.fetch_agent_card()
            except A2aClientError:
                if _looks_like_agent_card_url(client.configured_url):
                    raise
                await client.probe_direct_endpoint()
                return ExternalAgentTestResult(
                    ok=True,
                    message="External A2A JSON-RPC endpoint is reachable.",
                    protocol=ExternalAgentProtocol.A2A,
                    protocol_version_text="direct-jsonrpc",
                )
        return ExternalAgentTestResult(
            ok=True,
            message="External A2A agent card is reachable.",
            protocol=ExternalAgentProtocol.A2A,
            protocol_version_text=card.protocol_version,
            agent_name=card.name,
            agent_version=card.version,
        )
    except Exception as exc:
        return ExternalAgentTestResult(
            ok=False,
            message=str(exc) or exc.__class__.__name__,
            protocol=ExternalAgentProtocol.A2A,
        )


async def send_a2a_prompt(
    *,
    config: ExternalAgentConfig,
    prompt: str,
    metadata: dict[str, JsonValue],
    timeout_seconds: float,
) -> A2aPromptResult:
    async with _build_a2a_client(config) as client:
        return await client.send_message(
            prompt=prompt,
            metadata=metadata,
            timeout_seconds=timeout_seconds,
        )


class A2aHttpClient:
    def __init__(
        self,
        *,
        transport: StreamableHttpTransportConfig,
    ) -> None:
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._agent_card: A2aAgentCard | None = None
        self._request_id = 0

    async def __aenter__(self) -> A2aHttpClient:
        await self.start()
        return self

    async def __aexit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        await self.close()

    async def start(self) -> None:
        if self._client is not None and not self._client.is_closed:
            return
        self._client = create_async_http_client(
            ssl_verify=self._transport.ssl_verify,
        )

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None

    async def fetch_agent_card(self) -> A2aAgentCard:
        if self._agent_card is not None:
            return self._agent_card
        client = self._require_client()
        errors: list[str] = []
        for card_url in _agent_card_url_candidates(self._transport.url):
            try:
                response = await client.get(card_url, headers=self._headers())
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise A2aClientError("A2A agent card must be a JSON object")
                card = A2aAgentCard.model_validate(payload)
                self._agent_card = card
                return card
            except Exception as exc:
                errors.append(f"{card_url}: {str(exc) or exc.__class__.__name__}")
        raise A2aClientError(
            "Unable to fetch A2A agent card. Tried " + "; ".join(errors)
        )

    @property
    def configured_url(self) -> str:
        return self._transport.url

    async def probe_direct_endpoint(self) -> None:
        payload: dict[str, JsonValue] = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "tasks/get",
            "params": {"id": "relay-teams-probe"},
        }
        response = await self._require_client().post(
            self._transport.url,
            json=payload,
            headers={
                **self._headers(),
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise A2aClientError("A2A JSON-RPC probe response must be a JSON object")
        response_payload = {str(key): item for key, item in parsed.items()}
        if "result" not in response_payload and "error" not in response_payload:
            raise A2aClientError("A2A JSON-RPC probe response missing result or error")

    async def send_message(
        self,
        *,
        prompt: str,
        metadata: dict[str, JsonValue],
        timeout_seconds: float,
    ) -> A2aPromptResult:
        endpoint = await self._resolve_endpoint_url()
        request_id = self._next_request_id()
        message_id = str(uuid4())
        payload: dict[str, JsonValue] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": message_id,
                    "parts": [{"kind": "text", "text": prompt}],
                },
                "configuration": {"blocking": True},
                "metadata": metadata,
            },
        }
        response = await self._post_json_rpc(endpoint=endpoint, payload=payload)
        result = _json_object(response.get("result"))
        parsed = _extract_prompt_result(result)
        if parsed.text or parsed.state in _A2A_TERMINAL_TASK_STATES:
            return parsed
        if parsed.task_id is None:
            return parsed
        return await self._poll_task(
            endpoint=endpoint,
            task_id=parsed.task_id,
            timeout_seconds=timeout_seconds,
        )

    async def _poll_task(
        self,
        *,
        endpoint: str,
        task_id: str,
        timeout_seconds: float,
    ) -> A2aPromptResult:
        attempts = min(
            _A2A_MAX_POLL_ATTEMPTS,
            max(1, int(timeout_seconds // _A2A_POLL_INTERVAL_SECONDS)),
        )
        for _index in range(attempts):
            await asyncio.sleep(_A2A_POLL_INTERVAL_SECONDS)
            response = await self._post_json_rpc(
                endpoint=endpoint,
                payload={
                    "jsonrpc": "2.0",
                    "id": self._next_request_id(),
                    "method": "tasks/get",
                    "params": {"id": task_id},
                },
            )
            latest = _extract_prompt_result(_json_object(response.get("result")))
            if latest.text or latest.state in _A2A_TERMINAL_TASK_STATES:
                return latest
        raise A2aClientError(f"A2A task {task_id} did not complete before timeout")

    async def _resolve_endpoint_url(self) -> str:
        if _looks_like_agent_card_url(self._transport.url):
            return (await self.fetch_agent_card()).url
        try:
            return (await self.fetch_agent_card()).url
        except A2aClientError:
            return self._transport.url

    async def _post_json_rpc(
        self,
        *,
        endpoint: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        response = await self._require_client().post(
            endpoint,
            json=payload,
            headers={
                **self._headers(),
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise A2aClientError("A2A JSON-RPC response must be a JSON object")
        response_payload = {str(key): item for key, item in parsed.items()}
        error = _json_object(response_payload.get("error"))
        if error:
            message = _optional_str(error.get("message")) or "A2A request failed"
            raise A2aClientError(message)
        return response_payload

    def _headers(self) -> dict[str, str]:
        return {
            binding.name: binding.value
            for binding in self._transport.headers
            if binding.value is not None
        }

    def _next_request_id(self) -> str:
        self._request_id += 1
        return f"a2a-{self._request_id}"

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("A2A HTTP client is not started")
        return self._client


def _build_a2a_client(config: ExternalAgentConfig) -> A2aHttpClient:
    if not isinstance(config.transport, StreamableHttpTransportConfig):
        raise A2aClientError("A2A agent runtimes require streamable_http transport")
    return A2aHttpClient(transport=config.transport)


def _agent_card_url_candidates(url: str) -> tuple[str, ...]:
    normalized_url = url.strip()
    if _looks_like_agent_card_url(normalized_url):
        return (normalized_url,)
    parsed = urlsplit(normalized_url)
    root_candidate = urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            _A2A_AGENT_CARD_WELL_KNOWN_PATH,
            "",
            "",
        )
    )
    path_candidate = normalized_url.rstrip("/") + _A2A_AGENT_CARD_WELL_KNOWN_PATH
    if root_candidate == path_candidate:
        return (root_candidate,)
    return root_candidate, path_candidate


def _looks_like_agent_card_url(url: str) -> bool:
    path = urlsplit(url.strip()).path
    return path.rstrip("/").endswith(_A2A_AGENT_CARD_WELL_KNOWN_PATH)


def _extract_prompt_result(payload: dict[str, JsonValue]) -> A2aPromptResult:
    kind = _optional_str(payload.get("kind"))
    if kind == "message":
        return A2aPromptResult(
            text=_extract_message_text(payload),
            context_id=_optional_str(payload.get("contextId")),
        )
    status = _json_object(payload.get("status"))
    status_message = _json_object(status.get("message"))
    artifacts = _json_array(payload.get("artifacts"))
    text_parts = [
        _extract_message_text(status_message),
        *(_extract_artifact_text(item) for item in artifacts),
    ]
    state = _optional_str(status.get("state"))
    return A2aPromptResult(
        text="\n\n".join(part for part in text_parts if part).strip(),
        task_id=_optional_str(payload.get("id")),
        context_id=_optional_str(payload.get("contextId")),
        state=state,
    )


def _extract_message_text(message: dict[str, JsonValue]) -> str:
    parts = _json_array(message.get("parts"))
    return "\n".join(_extract_part_text(part) for part in parts).strip()


def _extract_artifact_text(artifact: JsonValue) -> str:
    artifact_object = _json_object(artifact)
    return "\n".join(
        _extract_part_text(part) for part in _json_array(artifact_object.get("parts"))
    ).strip()


def _extract_part_text(part: JsonValue) -> str:
    part_object = _json_object(part)
    kind = _optional_str(part_object.get("kind"))
    if kind == "text":
        return _optional_str(part_object.get("text")) or ""
    if kind == "data":
        data = part_object.get("data")
        return json.dumps(data, ensure_ascii=False, default=str)
    if kind == "file":
        file_payload = _json_object(part_object.get("file"))
        return (
            _optional_str(file_payload.get("uri"))
            or _optional_str(file_payload.get("mimeType"))
            or ""
        )
    return ""


def _json_object(value: JsonValue | None) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _json_array(value: JsonValue | None) -> tuple[JsonValue, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(cast(JsonValue, item) for item in value)


def _optional_str(value: JsonValue | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized
