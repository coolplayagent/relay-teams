# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from agent_teams.gateway.acp_stdio import AcpGatewayServer
from agent_teams.gateway.gateway_models import GatewayMcpConnectionStatus
from agent_teams.gateway.gateway_session_repository import GatewaySessionRepository
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
        self.stop_calls: list[str] = []

    def create_run(self, intent: IntentInput) -> tuple[str, str]:
        self._counter += 1
        run_id = f"run-{self._counter}"
        self.create_calls.append(intent.model_copy(deep=True))
        return run_id, run_id

    async def stream_run_events(self, run_id: str) -> AsyncIterator[RunEvent]:
        for event in self.events_by_run.get(run_id, ()):
            yield event

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
                    "acp": False,
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
async def test_session_prompt_streams_updates_and_usage(tmp_path: Path) -> None:
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

    assert response_result["stopReason"] == "end_turn"
    assert response_result["userMessageId"] == "user-msg-1"
    assert response_result["usage"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "thought_tokens": 3,
        "cached_read_tokens": 2,
        "cached_write_tokens": 0,
        "total_tokens": 18,
    }
    assert response_result["_meta"] == {
        "requestId": "2",
        "runId": "run-1",
    }
    assert len(run_manager.create_calls) == 1
    assert run_manager.create_calls[0] == IntentInput(
        session_id="session-1",
        intent="Summarize README",
        yolo=True,
    )

    session_updates = [_session_update_name(item) for item in notifications]
    assert session_updates == [
        "user_message_chunk",
        "agent_thought_chunk",
        "tool_call",
        "tool_call_update",
        "agent_message_chunk",
    ]


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
                "serverId": "filesystem",
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
    assert record.mcp_connections[0].status == GatewayMcpConnectionStatus.CLOSED


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

    server = AcpGatewayServer(
        gateway_session_service=gateway_session_service,
        session_service=cast(SessionService, session_service),
        run_service=cast(RunManager, run_manager),
        notify=notify,
    )
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
