# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
import json

import httpx
import pytest
from pydantic import JsonValue

import relay_teams.external_agents.a2a_client as a2a_client
from relay_teams.external_agents.models import (
    ExternalAgentConfig,
    ExternalAgentProtocol,
    StreamableHttpTransportConfig,
)


def _build_a2a_agent(url: str) -> ExternalAgentConfig:
    return ExternalAgentConfig(
        agent_id="a2a_agent",
        name="A2A Agent",
        protocol=ExternalAgentProtocol.A2A,
        transport=StreamableHttpTransportConfig(url=url),
    )


def _request_read_timeout(request: httpx.Request) -> float | None:
    timeout_extension: object = request.extensions.get("timeout")
    if not isinstance(timeout_extension, Mapping):
        return None
    read_timeout: object = timeout_extension.get("read")
    if not isinstance(read_timeout, int | float):
        return None
    return float(read_timeout)


@pytest.mark.asyncio
async def test_probe_a2a_agent_fetches_agent_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert str(request.url) == "http://agent.test/a2a"
            payload = json.loads(request.content.decode("utf-8"))
            assert isinstance(payload, dict)
            assert payload["method"] == "tasks/get"
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "error": {"code": -32001, "message": "Task not found"},
                },
            )
        assert request.method == "GET"
        assert str(request.url) == "http://agent.test/.well-known/agent.json"
        return httpx.Response(
            200,
            json={
                "protocolVersion": "0.2.6",
                "name": "Remote A2A",
                "description": "Remote agent",
                "url": "http://agent.test/a2a",
                "version": "1.0.0",
                "capabilities": {"streaming": False},
                "defaultInputModes": ["text/plain"],
                "defaultOutputModes": ["text/plain"],
                "skills": [],
            },
        )

    monkeypatch.setattr(
        a2a_client,
        "create_async_http_client",
        lambda ssl_verify=None: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )

    result = await a2a_client.probe_a2a_agent(_build_a2a_agent("http://agent.test/a2a"))

    assert result.ok is True
    assert result.protocol == ExternalAgentProtocol.A2A
    assert result.protocol_version_text == "0.2.6"
    assert result.agent_name == "Remote A2A"
    assert result.agent_version == "1.0.0"


@pytest.mark.asyncio
async def test_probe_a2a_agent_falls_back_to_direct_json_rpc_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.method == "GET":
            return httpx.Response(404)
        payload = json.loads(request.content.decode("utf-8"))
        assert isinstance(payload, dict)
        assert payload["method"] == "tasks/get"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "error": {"code": -32001, "message": "Task not found"},
            },
        )

    monkeypatch.setattr(
        a2a_client,
        "create_async_http_client",
        lambda ssl_verify=None: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )

    result = await a2a_client.probe_a2a_agent(
        _build_a2a_agent("http://agent.test/rpc.json")
    )

    assert result.ok is True
    assert result.protocol_version_text == "direct-jsonrpc"
    assert requested_urls[-1] == "http://agent.test/rpc.json"


@pytest.mark.asyncio
async def test_probe_a2a_agent_rejects_non_json_rpc_direct_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404)
        return httpx.Response(200, json={"error": "unauthorized"})

    monkeypatch.setattr(
        a2a_client,
        "create_async_http_client",
        lambda ssl_verify=None: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )

    result = await a2a_client.probe_a2a_agent(
        _build_a2a_agent("http://agent.test/rpc.json")
    )

    assert result.ok is False
    assert "JSON-RPC" in result.message


@pytest.mark.asyncio
async def test_send_a2a_prompt_uses_message_send_and_polls_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, JsonValue]] = []
    observed_card_timeouts: list[float] = []
    observed_post_timeouts: list[tuple[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            read_timeout = _request_read_timeout(request)
            if read_timeout is not None:
                observed_card_timeouts.append(read_timeout)
            return httpx.Response(
                200,
                json={
                    "protocolVersion": "0.2.6",
                    "name": "Remote A2A",
                    "description": "Remote agent",
                    "url": "http://agent.test/a2a",
                    "version": "1.0.0",
                    "capabilities": {"streaming": False},
                    "defaultInputModes": ["text/plain"],
                    "defaultOutputModes": ["text/plain"],
                    "skills": [],
                },
            )
        read_timeout = _request_read_timeout(request)
        payload = json.loads(request.content.decode("utf-8"))
        assert isinstance(payload, dict)
        normalized = {str(key): value for key, value in payload.items()}
        requests.append(normalized)
        if read_timeout is not None:
            observed_post_timeouts.append((str(normalized["method"]), read_timeout))
        if normalized["method"] == "message/send":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": normalized["id"],
                    "result": {
                        "kind": "task",
                        "id": "task-remote",
                        "contextId": "ctx-1",
                        "status": {"state": "working"},
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": normalized["id"],
                "result": {
                    "kind": "task",
                    "id": "task-remote",
                    "contextId": "ctx-1",
                    "status": {"state": "completed"},
                    "artifacts": [
                        {
                            "artifactId": "artifact-1",
                            "parts": [{"kind": "text", "text": "done"}],
                        }
                    ],
                },
            },
        )

    monkeypatch.setattr(
        a2a_client,
        "create_async_http_client",
        lambda ssl_verify=None: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )

    result = await a2a_client.send_a2a_prompt(
        config=_build_a2a_agent("http://agent.test/.well-known/agent.json"),
        prompt="Please work.",
        metadata={"relay_teams": {"run_id": "run-1"}},
        timeout_seconds=3,
    )

    assert result.text == "done"
    assert [request["method"] for request in requests] == [
        "message/send",
        "tasks/get",
    ]
    message_params = requests[0]["params"]
    assert isinstance(message_params, dict)
    message = message_params["message"]
    assert isinstance(message, dict)
    assert message["role"] == "user"
    assert message["parts"] == [{"kind": "text", "text": "Please work."}]
    assert len(observed_card_timeouts) == 1
    assert 0 < observed_card_timeouts[0] <= 3.0
    assert [method for method, _timeout in observed_post_timeouts] == [
        "message/send",
        "tasks/get",
    ]
    message_send_timeout = observed_post_timeouts[0][1]
    task_poll_timeout = observed_post_timeouts[1][1]
    assert 0 < message_send_timeout <= 3.0
    assert 0 < task_poll_timeout < message_send_timeout


@pytest.mark.asyncio
async def test_send_a2a_prompt_raises_for_failed_task_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "protocolVersion": "0.2.6",
                    "name": "Remote A2A",
                    "description": "Remote agent",
                    "url": "http://agent.test/a2a",
                    "version": "1.0.0",
                    "capabilities": {"streaming": False},
                    "defaultInputModes": ["text/plain"],
                    "defaultOutputModes": ["text/plain"],
                    "skills": [],
                },
            )
        payload = json.loads(request.content.decode("utf-8"))
        assert isinstance(payload, dict)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "kind": "task",
                    "id": "task-failed",
                    "contextId": "ctx-1",
                    "status": {
                        "state": "failed",
                        "message": {
                            "kind": "message",
                            "parts": [{"kind": "text", "text": "runtime failed"}],
                        },
                    },
                },
            },
        )

    monkeypatch.setattr(
        a2a_client,
        "create_async_http_client",
        lambda ssl_verify=None: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )

    with pytest.raises(a2a_client.A2aClientError, match="runtime failed"):
        await a2a_client.send_a2a_prompt(
            config=_build_a2a_agent("http://agent.test/.well-known/agent.json"),
            prompt="Please work.",
            metadata={},
            timeout_seconds=3,
        )


@pytest.mark.asyncio
async def test_send_a2a_prompt_treats_rpc_json_as_direct_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.method == "GET":
            return httpx.Response(404)
        payload = json.loads(request.content.decode("utf-8"))
        assert isinstance(payload, dict)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "kind": "message",
                    "contextId": "ctx-1",
                    "parts": [{"kind": "text", "text": "direct endpoint"}],
                },
            },
        )

    monkeypatch.setattr(
        a2a_client,
        "create_async_http_client",
        lambda ssl_verify=None: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )

    result = await a2a_client.send_a2a_prompt(
        config=_build_a2a_agent("http://agent.test/rpc.json"),
        prompt="Please work.",
        metadata={},
        timeout_seconds=3,
    )

    assert result.text == "direct endpoint"
    assert "http://agent.test/rpc.json" == requested_urls[-1]
