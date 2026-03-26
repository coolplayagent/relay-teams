from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.external_agents.config_service import ExternalAgentConfigService
from agent_teams.external_agents.models import (
    ExternalAgentConfig,
    StdioTransportConfig,
)
from agent_teams.external_agents import provider as provider_module
from agent_teams.external_agents.provider import (
    _ActivePromptState,
    _ConversationHandle,
    _conversation_key,
    _extract_tool_result,
    ExternalAcpSessionManager,
)
from agent_teams.external_agents.session_repository import (
    ExternalAgentSessionRepository,
)
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.providers.provider_contracts import LLMRequest
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.workspace import WorkspaceManager


class _FakeTransport:
    async def start(self) -> None:
        return None

    async def send_request(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _ = (method, params)
        return {}

    async def send_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        _ = (method, params)

    async def close(self) -> None:
        return None


class _HangingPromptTransport(_FakeTransport):
    def __init__(self) -> None:
        self.notifications: list[tuple[str, dict[str, JsonValue]]] = []
        self.prompt_started = asyncio.Event()

    async def send_request(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if method != "session/prompt":
            return {}
        _ = params
        self.prompt_started.set()
        future: asyncio.Future[dict[str, JsonValue]] = asyncio.Future()
        return await future

    async def send_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        self.notifications.append((method, params))


class _SequencedPromptTransport(_FakeTransport):
    def __init__(self) -> None:
        self.notifications: list[tuple[str, dict[str, JsonValue]]] = []
        self.prompt_started: asyncio.Queue[int] = asyncio.Queue()
        self.gates: list[asyncio.Event] = []
        self.requests: list[dict[str, JsonValue]] = []

    async def send_request(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if method != "session/prompt":
            return {}
        self.requests.append(params)
        gate = asyncio.Event()
        self.gates.append(gate)
        await self.prompt_started.put(len(self.gates))
        await gate.wait()
        return {}

    async def send_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        self.notifications.append((method, params))


class _FakeConfigService:
    def resolve_runtime_agent(self, agent_id: str) -> ExternalAgentConfig:
        return ExternalAgentConfig(
            agent_id=agent_id,
            name="Test Agent",
            transport=StdioTransportConfig(command="echo"),
        )


def _build_session_manager(tmp_path: Path) -> ExternalAcpSessionManager:
    return ExternalAcpSessionManager(
        config_service=cast(ExternalAgentConfigService, _FakeConfigService()),
        session_repo=cast(ExternalAgentSessionRepository, object()),
        message_repo=MessageRepository(tmp_path / "messages.db"),
        run_event_hub=RunEventHub(),
        workspace_manager=cast(WorkspaceManager, object()),
        mcp_registry=cast(McpRegistry, object()),
    )


def _build_request() -> LLMRequest:
    return LLMRequest(
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="default",
        conversation_id="conversation-1",
        instance_id="inst-1",
        role_id="MainAgent",
        system_prompt="sys",
        user_prompt="return the image",
    )


_PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+cFfoAAAAASUVORK5CYII="


@pytest.mark.asyncio
async def test_agent_message_chunk_converts_png_content_to_data_url(
    tmp_path: Path,
) -> None:
    manager = _build_session_manager(tmp_path)
    request = _build_request()
    key = _conversation_key(
        session_id=request.session_id,
        role_id=request.role_id,
        agent_id="opencode",
    )
    handle = _ConversationHandle(
        transport=_FakeTransport(),
        external_session_id="external-session-1",
    )
    handle.active_prompt = _ActivePromptState(request=request)
    manager._conversations[key] = handle

    queue = manager._run_event_hub.subscribe(request.run_id)
    await manager._handle_transport_message(
        key=key,
        message={
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {
                        "type": "image",
                        "mimeType": "image/png",
                        "data": "iVBORw0KGgoAAAANSUhEUgAAAAUA",
                    },
                }
            },
        },
    )

    expected = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA"
    assert handle.active_prompt.text_chunks == [expected]

    event = queue.get_nowait()
    assert event.event_type == RunEventType.TEXT_DELTA
    payload = json.loads(event.payload_json)
    assert payload["text"] == expected


def test_extract_tool_result_keeps_text_json_behavior() -> None:
    result = _extract_tool_result(
        {
            "content": [
                {
                    "type": "content",
                    "content": {
                        "type": "text",
                        "text": '{"ok": true}',
                    },
                }
            ]
        }
    )

    assert result == {"ok": True}


def test_extract_tool_result_converts_image_content_to_text_data_url() -> None:
    result = _extract_tool_result(
        {
            "content": [
                {
                    "type": "content",
                    "content": {
                        "type": "image",
                        "mimeType": "image/png",
                        "data": "aGVsbG8=",
                    },
                }
            ]
        }
    )

    assert result == {"text": "data:image/png;base64,aGVsbG8="}


@pytest.mark.asyncio
async def test_prompt_uses_image_tool_result_as_timeout_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _build_session_manager(tmp_path)
    transport = _HangingPromptTransport()
    request = _build_request()
    key = _conversation_key(
        session_id=request.session_id,
        role_id=request.role_id,
        agent_id="opencode",
    )
    handle = _ConversationHandle(
        transport=transport,
        external_session_id="external-session-1",
    )
    manager._conversations[key] = handle
    queue = manager._run_event_hub.subscribe(request.run_id)

    async def _ensure_conversation(**_: object) -> _ConversationHandle:
        return handle

    monkeypatch.setattr(manager, "_ensure_conversation", _ensure_conversation)
    monkeypatch.setattr(
        provider_module,
        "_EXTERNAL_ACP_PROMPT_INACTIVITY_TIMEOUT_SECONDS",
        0.01,
    )

    prompt_task = asyncio.create_task(
        manager.prompt(
            agent_id="opencode",
            role=cast(RoleDefinition, object()),
            request=request,
        )
    )
    await transport.prompt_started.wait()
    await manager._handle_transport_message(
        key=key,
        message={
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "tool-1",
                    "title": "bash",
                    "content": [
                        {
                            "type": "content",
                            "content": {
                                "type": "text",
                                "text": _PNG_BASE64,
                            },
                        }
                    ],
                }
            },
        },
    )

    result = await prompt_task

    assert result == f"data:image/png;base64,{_PNG_BASE64}"
    assert transport.notifications == [
        ("session/cancel", {"sessionId": "external-session-1"})
    ]

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    text_events = [
        event for event in events if event.event_type == RunEventType.TEXT_DELTA
    ]
    assert text_events[-1].payload_json == json.dumps(
        {
            "text": result,
            "role_id": request.role_id,
            "instance_id": request.instance_id,
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_prompt_times_out_when_external_agent_stops_sending_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _build_session_manager(tmp_path)
    transport = _HangingPromptTransport()
    request = _build_request()
    key = _conversation_key(
        session_id=request.session_id,
        role_id=request.role_id,
        agent_id="opencode",
    )
    handle = _ConversationHandle(
        transport=transport,
        external_session_id="external-session-1",
    )
    manager._conversations[key] = handle

    async def _ensure_conversation(**_: object) -> _ConversationHandle:
        return handle

    monkeypatch.setattr(manager, "_ensure_conversation", _ensure_conversation)
    monkeypatch.setattr(
        provider_module,
        "_EXTERNAL_ACP_PROMPT_INACTIVITY_TIMEOUT_SECONDS",
        0.01,
    )

    with pytest.raises(
        RuntimeError,
        match="External ACP prompt timed out after 0.01 seconds without updates",
    ):
        await manager.prompt(
            agent_id="opencode",
            role=cast(RoleDefinition, object()),
            request=request,
        )

    assert transport.notifications == [
        ("session/cancel", {"sessionId": "external-session-1"})
    ]


@pytest.mark.asyncio
async def test_prompt_retries_once_when_external_agent_returns_empty_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _build_session_manager(tmp_path)
    transport = _SequencedPromptTransport()
    request = _build_request()
    key = _conversation_key(
        session_id=request.session_id,
        role_id=request.role_id,
        agent_id="opencode",
    )
    handle = _ConversationHandle(
        transport=transport,
        external_session_id="external-session-1",
    )
    manager._conversations[key] = handle

    async def _ensure_conversation(**_: object) -> _ConversationHandle:
        return handle

    monkeypatch.setattr(manager, "_ensure_conversation", _ensure_conversation)
    prompt_task = asyncio.create_task(
        manager.prompt(
            agent_id="opencode",
            role=cast(RoleDefinition, object()),
            request=request,
        )
    )

    first_attempt = await transport.prompt_started.get()
    assert first_attempt == 1
    transport.gates[0].set()

    second_attempt = await transport.prompt_started.get()
    assert second_attempt == 2
    await manager._handle_transport_message(
        key=key,
        message={
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {
                        "type": "text",
                        "text": "data:image/png;base64,aGVsbG8=",
                    },
                }
            },
        },
    )
    transport.gates[1].set()

    result = await prompt_task

    assert result == "data:image/png;base64,aGVsbG8="
    assert len(transport.requests) == 2
    second_prompt = cast(
        list[dict[str, JsonValue]],
        transport.requests[1]["prompt"],
    )
    second_prompt_text = cast(str, second_prompt[0]["text"])
    assert "Your previous reply was empty." in second_prompt_text
