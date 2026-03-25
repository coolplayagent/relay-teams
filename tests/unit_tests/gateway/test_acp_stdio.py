# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from io import BytesIO
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

import agent_teams.gateway.acp_stdio as acp_stdio_module
from agent_teams.gateway.acp_stdio import AcpGatewayServer, AcpStdioRuntime
from agent_teams.gateway.gateway_session_repository import GatewaySessionRepository
from agent_teams.gateway.gateway_session_model_profile_store import (
    GatewaySessionModelProfileStore,
)
from agent_teams.gateway.gateway_session_service import GatewaySessionService
from agent_teams.providers.token_usage_repo import RunTokenUsage
from agent_teams.sessions import SessionService
from agent_teams.sessions.session_models import SessionRecord
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.run_manager import RunManager
from agent_teams.sessions.runs.run_models import IntentInput, RunEvent


class FakeSessionService:
    def __init__(self) -> None:
        self._counter = 0
        self.messages_by_session: dict[str, list[dict[str, object]]] = {}
        self.usage_by_run: dict[str, RunTokenUsage] = {}

    def create_session(
        self,
        *,
        session_id: str | None = None,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
    ) -> SessionRecord:
        _ = metadata
        self._counter += 1
        resolved_session_id = session_id or f"session-{self._counter}"
        record = SessionRecord(
            session_id=resolved_session_id,
            workspace_id=workspace_id,
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
        )
        self.messages_by_session.setdefault(record.session_id, [])
        return record

    def get_session_messages(self, session_id: str) -> list[dict[str, object]]:
        return list(self.messages_by_session.get(session_id, []))

    def get_token_usage_by_run(self, run_id: str) -> RunTokenUsage:
        return self.usage_by_run[run_id]


class FakeRunManager:
    def __init__(self) -> None:
        self._counter = 0
        self.events_by_run: dict[str, tuple[RunEvent, ...]] = {}
        self.create_calls: list[IntentInput] = []
        self.ensure_started_calls: list[str] = []
        self.stop_calls: list[str] = []

    def create_run(self, intent: IntentInput) -> tuple[str, str]:
        self._counter += 1
        run_id = f"run-{self._counter}"
        self.create_calls.append(intent.model_copy(deep=True))
        return run_id, run_id

    async def stream_run_events(self, run_id: str) -> AsyncIterator[RunEvent]:
        for event in self.events_by_run.get(run_id, ()):
            yield event

    def ensure_run_started(self, run_id: str) -> None:
        self.ensure_started_calls.append(run_id)

    def stop_run(self, run_id: str) -> None:
        self.stop_calls.append(run_id)


@pytest.mark.asyncio
async def test_initialize_returns_gateway_capabilities(tmp_path: Path) -> None:
    server, _, _, _ = _build_server(tmp_path)

    response = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 2},
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": 2,
            "agentCapabilities": {
                "loadSession": True,
                "promptCapabilities": {
                    "audio": False,
                    "embeddedContext": False,
                    "image": False,
                },
                "mcpCapabilities": {
                    "acp": True,
                    "http": False,
                    "sse": False,
                },
            },
            "agentInfo": {
                "name": "agent-teams",
                "version": "0.1.0",
            },
        },
    }


@pytest.mark.asyncio
async def test_session_prompt_streams_updates_and_usage(
    tmp_path: Path,
) -> None:
    server, session_service, run_manager, notifications = _build_server(tmp_path)
    run_manager.events_by_run["run-1"] = (
        _event(
            "session-1",
            "run-1",
            RunEventType.THINKING_DELTA,
            {"text": "thinking"},
        ),
        _event(
            "session-1",
            "run-1",
            RunEventType.TOOL_CALL,
            {
                "tool_call_id": "tool-1",
                "tool_name": "filesystem.read",
                "args": {"path": "README.md"},
            },
        ),
        _event(
            "session-1",
            "run-1",
            RunEventType.TOOL_RESULT,
            {
                "tool_call_id": "tool-1",
                "result": {"ok": True},
            },
        ),
        _event("session-1", "run-1", RunEventType.TEXT_DELTA, {"text": "done"}),
        _event("session-1", "run-1", RunEventType.RUN_COMPLETED, {}),
    )
    session_service.usage_by_run["run-1"] = RunTokenUsage(
        run_id="run-1",
        total_input_tokens=11,
        total_cached_input_tokens=2,
        total_output_tokens=7,
        total_reasoning_output_tokens=3,
        total_tokens=18,
        total_requests=1,
        total_tool_calls=1,
        by_agent=[],
    )

    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {
                "cwd": str(tmp_path),
                "capabilities": {"filesystem": True},
                "mcpServers": [
                    {
                        "id": "filesystem",
                        "name": "filesystem",
                        "transport": "acp",
                    }
                ],
            },
        }
    )
    created_result = _require_result_object(created)
    session_id = _require_str(created_result, "sessionId")

    response = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "messageId": "user-msg-1",
                "prompt": [{"type": "text", "text": "Summarize README"}],
            },
        }
    )
    response_result = _require_result_object(response)

    assert response_result == {"stopReason": "end_turn"}
    assert len(run_manager.create_calls) == 1
    assert run_manager.create_calls[0] == IntentInput(
        session_id="session-1",
        intent="Summarize README",
        yolo=True,
    )
    assert run_manager.ensure_started_calls == ["run-1"]

    session_updates = [_session_update_name(item) for item in notifications]
    assert session_updates == [
        "user_message_chunk",
        "agent_thought_chunk",
        "tool_call",
        "tool_call_update",
        "agent_message_chunk",
    ]


@pytest.mark.asyncio
async def test_session_prompt_streams_progress_updates_for_zed(
    tmp_path: Path,
) -> None:
    server, _, run_manager, notifications = _build_server(tmp_path)
    server.set_zed_compat_mode(True)
    run_manager.events_by_run["run-1"] = (
        _event(
            "session-1",
            "run-1",
            RunEventType.THINKING_DELTA,
            {"text": "thinking"},
        ),
        _event(
            "session-1",
            "run-1",
            RunEventType.TOOL_CALL,
            {
                "tool_call_id": "tool-1",
                "tool_name": "filesystem.read",
                "args": {"path": "README.md"},
            },
        ),
        _event(
            "session-1",
            "run-1",
            RunEventType.TOOL_RESULT,
            {
                "tool_call_id": "tool-1",
                "result": {"ok": True},
            },
        ),
        _event("session-1", "run-1", RunEventType.TEXT_DELTA, {"text": "done"}),
        _event("session-1", "run-1", RunEventType.RUN_COMPLETED, {}),
    )

    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )
    created_result = _require_result_object(created)
    session_id = _require_str(created_result, "sessionId")

    response = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "messageId": "user-msg-1",
                "prompt": [{"type": "text", "text": "Summarize README"}],
            },
        }
    )

    assert _require_result_object(response) == {"stopReason": "end_turn"}
    session_updates = [_session_update_name(item) for item in notifications]
    assert session_updates == [
        "agent_thought_chunk",
        "tool_call",
        "tool_call_update",
        "agent_message_chunk",
    ]


@pytest.mark.asyncio
async def test_session_prompt_includes_string_tool_raw_input_for_zed(
    tmp_path: Path,
) -> None:
    server, _, run_manager, notifications = _build_server(tmp_path)
    server.set_zed_compat_mode(True)
    run_manager.events_by_run["run-1"] = (
        _event(
            "session-1",
            "run-1",
            RunEventType.TOOL_CALL,
            {
                "tool_call_id": "tool-1",
                "tool_name": "shell",
                "args": "echo hello world",
            },
        ),
        _event("session-1", "run-1", RunEventType.RUN_COMPLETED, {}),
    )

    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )
    created_result = _require_result_object(created)
    session_id = _require_str(created_result, "sessionId")

    response = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "messageId": "user-msg-1",
                "prompt": [{"type": "text", "text": "run shell"}],
            },
        }
    )

    assert _require_result_object(response) == {"stopReason": "end_turn"}
    params = notifications[0]["params"]
    assert isinstance(params, dict)
    update = params["update"]
    assert isinstance(update, dict)
    assert update["sessionUpdate"] == "tool_call"
    assert update["rawInput"] == "echo hello world"


@pytest.mark.asyncio
async def test_session_load_persists_host_provided_mcp_servers(
    tmp_path: Path,
) -> None:
    server, _, _, _ = _build_server(tmp_path)

    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )
    created_result = _require_result_object(created)
    session_id = _require_str(created_result, "sessionId")

    loaded = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/load",
            "params": {
                "sessionId": session_id,
                "mcpServers": [
                    {
                        "name": "mcp-server-context7",
                        "command": "npx",
                        "args": ["-y", "@upstash/context7-mcp"],
                    }
                ],
            },
        }
    )

    assert _require_result_object(loaded) == {"sessionId": session_id}
    repository = GatewaySessionRepository(tmp_path / "gateway.db")
    record = repository.get(session_id)
    assert len(record.session_mcp_servers) == 1
    server_spec = record.session_mcp_servers[0]
    assert server_spec.server_id == "mcp-server-context7"
    assert server_spec.transport == "stdio"
    assert server_spec.config["command"] == "npx"
    assert server_spec.config["args"] == ["-y", "@upstash/context7-mcp"]


@pytest.mark.asyncio
async def test_session_load_replays_thinking_and_response_chunks_separately(
    tmp_path: Path,
) -> None:
    server, session_service, _, notifications = _build_server(tmp_path)

    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )
    created_result = _require_result_object(created)
    session_id = _require_str(created_result, "sessionId")
    session_service.messages_by_session["session-1"] = [
        {
            "role": "user",
            "message": {
                "parts": [
                    {
                        "part_kind": "user-prompt",
                        "content": "hello",
                    }
                ]
            },
        },
        {
            "role": "assistant",
            "message": {
                "parts": [
                    {
                        "part_kind": "thinking",
                        "content": "draft reasoning",
                    },
                    {
                        "part_kind": "text",
                        "content": "final answer",
                    },
                ]
            },
        },
    ]

    loaded = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/load",
            "params": {
                "sessionId": session_id,
                "cwd": str(tmp_path),
                "mcpServers": [],
            },
        }
    )

    assert _require_result_object(loaded) == {"sessionId": session_id}
    session_updates = [_session_update_name(item) for item in notifications]
    assert session_updates == [
        "user_message_chunk",
        "agent_thought_chunk",
        "agent_message_chunk",
    ]
    thought_update = _session_update_payload(notifications[1])
    assert thought_update["content"] == {
        "type": "text",
        "text": "draft reasoning",
    }
    message_update = _session_update_payload(notifications[2])
    assert message_update["content"] == {
        "type": "text",
        "text": "final answer",
    }


@pytest.mark.asyncio
async def test_session_prompt_preserves_whitespace_for_zed_chunks(
    tmp_path: Path,
) -> None:
    server, _, run_manager, notifications = _build_server(tmp_path)
    server.set_zed_compat_mode(True)
    formatted_text = "Line 1\n\n  - item 1\n  - item 2\n"
    run_manager.events_by_run["run-1"] = (
        _event("session-1", "run-1", RunEventType.TEXT_DELTA, {"text": formatted_text}),
        _event("session-1", "run-1", RunEventType.RUN_COMPLETED, {}),
    )

    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )
    created_result = _require_result_object(created)
    session_id = _require_str(created_result, "sessionId")

    response = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "messageId": "user-msg-1",
                "prompt": [{"type": "text", "text": "format this"}],
            },
        }
    )

    assert _require_result_object(response) == {"stopReason": "end_turn"}
    params = notifications[0]["params"]
    assert isinstance(params, dict)
    update = params["update"]
    assert isinstance(update, dict)
    content = update["content"]
    assert isinstance(content, dict)
    assert content["text"] == formatted_text


@pytest.mark.asyncio
async def test_mcp_connection_lifecycle_updates_gateway_state(tmp_path: Path) -> None:
    server, _, _, _ = _build_server(tmp_path)

    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {
                "mcpServers": [
                    {
                        "id": "filesystem",
                        "name": "filesystem",
                        "transport": "acp",
                    }
                ]
            },
        }
    )
    created_result = _require_result_object(created)
    session_id = _require_str(created_result, "sessionId")

    connected = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "mcp/connect",
            "params": {
                "sessionId": session_id,
                "acpId": "filesystem",
            },
        }
    )
    connected_result = _require_result_object(connected)
    connection_id = _require_str(connected_result, "connectionId")
    assert connected_result["serverId"] == "filesystem"
    assert connected_result["status"] == "open"

    disconnected = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "mcp/disconnect",
            "params": {
                "sessionId": session_id,
                "connectionId": connection_id,
            },
        }
    )
    assert disconnected == {
        "jsonrpc": "2.0",
        "id": 3,
        "result": {
            "status": "closed",
            "connectionId": connection_id,
        },
    }
    repository = GatewaySessionRepository(tmp_path / "gateway.db")
    record = repository.get(session_id)
    assert len(record.mcp_connections) == 1
    assert record.mcp_connections[0].connection_id == connection_id


@pytest.mark.asyncio
async def test_stdio_runtime_uses_content_length_framing_by_default(
    tmp_path: Path,
) -> None:
    server, _, _, _ = _build_server(tmp_path)
    output = BytesIO()
    runtime = AcpStdioRuntime(
        server=server,
        input_stream=BytesIO(),
        output_stream=output,
    )

    await runtime.send_message({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})

    written = output.getvalue()
    assert written.startswith(b"Content-Length: ")
    header, _, payload = written.partition(b"\r\n\r\n")
    assert header
    assert json.loads(payload.decode("utf-8")) == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"ok": True},
    }


@pytest.mark.asyncio
async def test_stdio_runtime_emits_json_lines_for_zed(
    tmp_path: Path,
) -> None:
    server, _, _, _ = _build_server(tmp_path)
    output = BytesIO()
    runtime = AcpStdioRuntime(
        server=server,
        input_stream=BytesIO(),
        output_stream=output,
    )
    runtime.set_transport_mode(framed_input=False)

    await runtime.send_message({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})

    payload = output.getvalue().decode("utf-8")
    assert payload.endswith("\n")
    assert json.loads(payload) == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"ok": True},
    }


@pytest.mark.asyncio
async def test_session_new_stores_model_profile_override_without_persisting_api_key(
    tmp_path: Path,
) -> None:
    session_service = FakeSessionService()
    repository = GatewaySessionRepository(tmp_path / "gateway.db")
    session_model_profile_store = GatewaySessionModelProfileStore()
    gateway_session_service = GatewaySessionService(
        repository=repository,
        session_service=cast(SessionService, session_service),
        session_model_profile_store=session_model_profile_store,
    )
    notifications: list[dict[str, JsonValue]] = []

    async def notify(message: dict[str, JsonValue]) -> None:
        notifications.append(message)

    server = AcpGatewayServer(
        gateway_session_service=gateway_session_service,
        session_service=cast(SessionService, session_service),
        run_service=cast(RunManager, FakeRunManager()),
        notify=notify,
    )

    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {
                "modelProfileOverride": {
                    "name": "default",
                    "provider": "openai_compatible",
                    "model": "gpt-4.1",
                    "baseUrl": "https://api.openai.com/v1",
                    "apiKey": "sk-secret",
                    "temperature": 0.2,
                }
            },
        }
    )
    created_result = _require_result_object(created)
    session_id = _require_str(created_result, "sessionId")

    record = repository.get(session_id)
    public_override = record.channel_state["acp_model_profile_override"]
    assert isinstance(public_override, dict)
    assert public_override == {
        "name": "default",
        "provider": "openai_compatible",
        "model": "gpt-4.1",
        "baseUrl": "https://api.openai.com/v1",
        "sslVerify": None,
        "temperature": 0.2,
        "topP": None,
        "maxTokens": None,
        "contextWindow": None,
        "connectTimeoutSeconds": None,
    }
    assert "apiKey" not in public_override

    runtime_override = session_model_profile_store.get(record.internal_session_id)
    assert runtime_override is not None
    assert runtime_override.model == "gpt-4.1"
    assert runtime_override.base_url == "https://api.openai.com/v1"
    assert runtime_override.api_key == "sk-secret"
    assert notifications == []


def test_acp_trace_messages_require_explicit_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ACP_TRACE_STDIO", raising=False)
    recorded_events: list[str] = []

    def fake_log_event(
        _logger: object,
        level: int,
        *,
        event: str,
        message: str,
        payload: dict[str, JsonValue] | None = None,
        duration_ms: int | None = None,
        exc_info: object = None,
    ) -> None:
        _ = (level, message, payload, duration_ms, exc_info)
        recorded_events.append(event)

    monkeypatch.setattr(acp_stdio_module, "log_event", fake_log_event)

    acp_stdio_module._trace_acp_message(
        "outbound",
        {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}},
    )
    assert recorded_events == []

    monkeypatch.setenv("ACP_TRACE_STDIO", "1")
    acp_stdio_module._trace_acp_message(
        "outbound",
        {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}},
    )
    assert recorded_events == ["gateway.acp.outbound"]


def _build_server(
    tmp_path: Path,
) -> tuple[
    AcpGatewayServer,
    FakeSessionService,
    FakeRunManager,
    list[dict[str, JsonValue]],
]:
    session_service = FakeSessionService()
    repository = GatewaySessionRepository(tmp_path / "gateway.db")
    gateway_session_service = GatewaySessionService(
        repository=repository,
        session_service=cast(SessionService, session_service),
    )
    run_manager = FakeRunManager()
    notifications: list[dict[str, JsonValue]] = []

    async def notify(message: dict[str, JsonValue]) -> None:
        notifications.append(message)

    async def send_request(
        _method: str,
        _params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {},
        }

    server = AcpGatewayServer(
        gateway_session_service=gateway_session_service,
        session_service=cast(SessionService, session_service),
        run_service=cast(RunManager, run_manager),
        notify=notify,
    )
    server.set_mcp_relay_outbound(send_request=send_request, send_notification=notify)
    return server, session_service, run_manager, notifications


def _event(
    session_id: str,
    run_id: str,
    event_type: RunEventType,
    payload: dict[str, JsonValue],
) -> RunEvent:
    return RunEvent(
        session_id=session_id,
        run_id=run_id,
        trace_id=run_id,
        event_type=event_type,
        payload_json=json.dumps(payload, ensure_ascii=False),
    )


def _require_result_object(
    response: dict[str, JsonValue] | None,
) -> dict[str, JsonValue]:
    assert response is not None
    result = response.get("result")
    assert isinstance(result, dict)
    return result


def _require_str(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    assert isinstance(value, str)
    return value


def _session_update_name(message: dict[str, JsonValue]) -> str:
    params = message.get("params")
    assert isinstance(params, dict)
    update = params.get("update")
    assert isinstance(update, dict)
    session_update = update.get("sessionUpdate")
    assert isinstance(session_update, str)
    return session_update


def _session_update_payload(message: dict[str, JsonValue]) -> dict[str, JsonValue]:
    params = message.get("params")
    assert isinstance(params, dict)
    update = params.get("update")
    assert isinstance(update, dict)
    return update
