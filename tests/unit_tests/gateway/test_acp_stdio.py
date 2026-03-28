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
from agent_teams.gateway.acp_stdio import (
    RECOVERABLE_PAUSED_RUN_MESSAGE,
    AcpGatewayServer,
    AcpStdioRuntime,
)
from agent_teams.gateway.gateway_session_repository import GatewaySessionRepository
from agent_teams.gateway.gateway_session_model_profile_store import (
    GatewaySessionModelProfileStore,
)
from agent_teams.gateway.gateway_session_service import GatewaySessionService
from agent_teams.media import MediaAssetService, content_parts_from_text
from agent_teams.providers.token_usage_repo import RunTokenUsage
from agent_teams.sessions import SessionService
from agent_teams.sessions.session_models import SessionRecord
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.run_manager import RunManager
from agent_teams.sessions.runs.run_models import IntentInput, RunEvent
from agent_teams.workspace import WorkspaceService
from agent_teams.workspace.workspace_models import WorkspaceRecord


class FakeSessionService:
    def __init__(self) -> None:
        self._counter = 0
        self._sessions: dict[str, SessionRecord] = {}
        self.messages_by_session: dict[str, list[dict[str, object]]] = {}
        self.global_events_by_session: dict[str, list[dict[str, object]]] = {}
        self.recovery_snapshot_by_session: dict[str, dict[str, object]] = {}
        self.usage_by_run: dict[str, RunTokenUsage] = {}
        self.create_session_calls: list[dict[str, object]] = []
        self.rebind_session_calls: list[dict[str, object]] = []
        self.active_run_session_ids: set[str] = set()

    def create_session(
        self,
        *,
        session_id: str | None = None,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
        session_mode: object | None = None,
        normal_root_role_id: str | None = None,
        orchestration_preset_id: str | None = None,
    ) -> SessionRecord:
        _ = metadata
        self.create_session_calls.append(
            {
                "session_id": session_id,
                "workspace_id": workspace_id,
                "session_mode": session_mode,
                "normal_root_role_id": normal_root_role_id,
                "orchestration_preset_id": orchestration_preset_id,
            }
        )
        self._counter += 1
        resolved_session_id = session_id or f"session-{self._counter}"
        record = SessionRecord(
            session_id=resolved_session_id,
            workspace_id=workspace_id,
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
        )
        self._sessions[record.session_id] = record
        self.messages_by_session.setdefault(record.session_id, [])
        return record

    def get_session(self, session_id: str) -> SessionRecord:
        return self._sessions[session_id]

    def rebind_session_workspace(
        self,
        session_id: str,
        *,
        workspace_id: str,
    ) -> SessionRecord:
        if session_id in self.active_run_session_ids:
            raise RuntimeError(
                "Cannot rebind workspace while session has active or recoverable run"
            )
        current = self._sessions[session_id]
        updated = current.model_copy(
            update={
                "workspace_id": workspace_id,
                "project_id": workspace_id,
                "updated_at": datetime.now(tz=timezone.utc),
            }
        )
        self._sessions[session_id] = updated
        self.rebind_session_calls.append(
            {
                "session_id": session_id,
                "workspace_id": workspace_id,
            }
        )
        return updated

    def get_session_messages(self, session_id: str) -> list[dict[str, object]]:
        return list(self.messages_by_session.get(session_id, []))

    def get_global_events(self, session_id: str) -> list[dict[str, object]]:
        return list(self.global_events_by_session.get(session_id, []))

    def get_recovery_snapshot(self, session_id: str) -> dict[str, object]:
        return self.recovery_snapshot_by_session.get(
            session_id,
            {
                "active_run": None,
                "pending_tool_approvals": [],
                "paused_subagent": None,
                "round_snapshot": None,
            },
        )

    def get_token_usage_by_run(self, run_id: str) -> RunTokenUsage:
        return self.usage_by_run[run_id]


class FakeRunManager:
    def __init__(self) -> None:
        self._counter = 0
        self.events_by_run: dict[str, tuple[RunEvent, ...]] = {}
        self.create_calls: list[IntentInput] = []
        self.ensure_started_calls: list[str] = []
        self.resume_calls: list[str] = []
        self.stop_calls: list[str] = []
        self.stream_calls: list[tuple[str, int]] = []

    def create_run(self, intent: IntentInput) -> tuple[str, str]:
        self._counter += 1
        run_id = f"run-{self._counter}"
        self.create_calls.append(intent.model_copy(deep=True))
        return run_id, run_id

    async def stream_run_events(
        self,
        run_id: str,
        after_event_id: int = 0,
    ) -> AsyncIterator[RunEvent]:
        self.stream_calls.append((run_id, after_event_id))
        for event in self.events_by_run.get(run_id, ()):
            if (
                after_event_id > 0
                and event.event_id is not None
                and event.event_id <= after_event_id
            ):
                continue
            yield event

    def ensure_run_started(self, run_id: str) -> None:
        self.ensure_started_calls.append(run_id)

    def resume_run(self, run_id: str) -> str:
        self.resume_calls.append(run_id)
        return "session-1"

    def stop_run(self, run_id: str) -> None:
        self.stop_calls.append(run_id)


class FakeWorkspaceService:
    def __init__(self) -> None:
        self.workspaces_by_root: dict[Path, WorkspaceRecord] = {}

    def create_workspace_for_root(self, *, root_path: Path) -> WorkspaceRecord:
        resolved_root = root_path.resolve()
        if not resolved_root.exists():
            raise ValueError(f"Workspace root does not exist: {resolved_root}")
        if not resolved_root.is_dir():
            raise ValueError(f"Workspace root is not a directory: {resolved_root}")
        existing = self.workspaces_by_root.get(resolved_root)
        if existing is not None:
            return existing
        workspace_id = f"workspace-{len(self.workspaces_by_root) + 1}"
        record = WorkspaceRecord(
            workspace_id=workspace_id,
            root_path=resolved_root,
        )
        self.workspaces_by_root[resolved_root] = record
        return record


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
                    "audio": True,
                    "embeddedContext": False,
                    "image": True,
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

    assert response_result == {
        "stopReason": "end_turn",
        "runId": "run-1",
        "runStatus": "completed",
        "recoverable": False,
    }
    assert len(run_manager.create_calls) == 1
    assert run_manager.create_calls[0] == IntentInput(
        session_id="session-1",
        input=content_parts_from_text("Summarize README"),
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
async def test_session_new_uses_cwd_backed_workspace_for_internal_session(
    tmp_path: Path,
) -> None:
    workspace_service = FakeWorkspaceService()
    server, session_service, _, _ = _build_server(
        tmp_path,
        workspace_service=workspace_service,
    )

    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )

    session_id = _require_str(_require_result_object(created), "sessionId")
    record = GatewaySessionRepository(tmp_path / "gateway.db").get(session_id)
    internal_session = session_service.get_session(record.internal_session_id)
    assert internal_session.workspace_id == "workspace-1"
    assert record.cwd == str(tmp_path.resolve())
    workspace_record = workspace_service.workspaces_by_root[tmp_path.resolve()]
    assert workspace_record.root_path == tmp_path.resolve()


@pytest.mark.asyncio
async def test_session_new_rejects_invalid_cwd(tmp_path: Path) -> None:
    workspace_service = FakeWorkspaceService()
    server, _, _, _ = _build_server(
        tmp_path,
        workspace_service=workspace_service,
    )
    missing_path = tmp_path / "missing-project"

    response = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(missing_path), "mcpServers": []},
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {
            "code": -32602,
            "message": f"Workspace root does not exist: {missing_path.resolve()}",
        },
    }


@pytest.mark.asyncio
async def test_session_new_uses_gateway_default_normal_root_role(
    tmp_path: Path,
) -> None:
    server, session_service, _, _ = _build_server(
        tmp_path,
        default_normal_root_role_id="Crafter",
    )

    _ = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )

    assert session_service.create_session_calls == [
        {
            "session_id": None,
            "workspace_id": "default",
            "session_mode": None,
            "normal_root_role_id": "Crafter",
            "orchestration_preset_id": None,
        }
    ]


@pytest.mark.asyncio
async def test_session_prompt_returns_paused_run_without_clearing_binding(
    tmp_path: Path,
) -> None:
    server, _, run_manager, notifications = _build_server(tmp_path)
    run_manager.events_by_run["run-1"] = (
        _event(
            "session-1",
            "run-1",
            RunEventType.RUN_PAUSED,
            {"error_message": "stream interrupted"},
        ),
    )

    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )
    session_id = _require_str(_require_result_object(created), "sessionId")

    response = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "messageId": "user-msg-1",
                "prompt": [{"type": "text", "text": "continue"}],
            },
        }
    )

    assert _require_result_object(response) == {
        "stopReason": "end_turn",
        "runId": "run-1",
        "runStatus": "paused",
        "recoverable": True,
    }
    params = notifications[-1]["params"]
    assert isinstance(params, dict)
    update = params["update"]
    assert isinstance(update, dict)
    assert update["sessionUpdate"] == "agent_message_chunk"
    content = update["content"]
    assert isinstance(content, dict)
    assert (
        content["text"]
        == "Run paused: stream interrupted\nSend session/resume to continue."
    )
    repository = GatewaySessionRepository(tmp_path / "gateway.db")
    record = repository.get(session_id)
    assert record.active_run_id == "run-1"


@pytest.mark.asyncio
async def test_session_prompt_rejects_recoverable_paused_run(
    tmp_path: Path,
) -> None:
    server, session_service, run_manager, notifications = _build_server(tmp_path)
    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )
    session_id = _require_str(_require_result_object(created), "sessionId")
    session_service.recovery_snapshot_by_session["session-1"] = {
        "active_run": {
            "run_id": "run-paused",
            "status": "paused",
            "phase": "awaiting_recovery",
            "is_recoverable": True,
            "should_show_recover": True,
        },
        "pending_tool_approvals": [],
        "paused_subagent": None,
        "round_snapshot": None,
    }

    response = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": "keep going"}],
            },
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {
            "code": -32000,
            "message": RECOVERABLE_PAUSED_RUN_MESSAGE,
        },
    }
    assert run_manager.create_calls == []
    assert notifications == []


@pytest.mark.asyncio
async def test_session_resume_restarts_active_run_and_returns_result(
    tmp_path: Path,
) -> None:
    server, _, run_manager, _notifications = _build_server(tmp_path)
    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )
    session_id = _require_str(_require_result_object(created), "sessionId")
    repository = GatewaySessionRepository(tmp_path / "gateway.db")
    record = repository.get(session_id)
    repository.update(record.model_copy(update={"active_run_id": "run-9"}))
    run_manager.events_by_run["run-9"] = (
        _event("session-1", "run-9", RunEventType.RUN_COMPLETED, {}),
    )

    response = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/resume",
            "params": {"sessionId": session_id},
        }
    )

    assert _require_result_object(response) == {
        "stopReason": "end_turn",
        "runId": "run-9",
        "runStatus": "completed",
        "recoverable": False,
    }
    assert run_manager.resume_calls == ["run-9"]
    assert run_manager.ensure_started_calls == ["run-9"]
    assert repository.get(session_id).active_run_id is None


@pytest.mark.asyncio
async def test_session_resume_streams_only_new_events_after_last_seen_event(
    tmp_path: Path,
) -> None:
    server, session_service, run_manager, notifications = _build_server(tmp_path)
    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )
    session_id = _require_str(_require_result_object(created), "sessionId")
    repository = GatewaySessionRepository(tmp_path / "gateway.db")
    record = repository.get(session_id)
    repository.update(record.model_copy(update={"active_run_id": "run-9"}))
    session_service.recovery_snapshot_by_session["session-1"] = {
        "active_run": {
            "run_id": "run-9",
            "last_event_id": 3,
        },
        "pending_tool_approvals": [],
        "paused_subagent": None,
        "round_snapshot": None,
    }
    run_manager.events_by_run["run-9"] = (
        _event(
            "session-1",
            "run-9",
            RunEventType.TEXT_DELTA,
            {"text": "old output"},
            event_id=1,
        ),
        _event(
            "session-1",
            "run-9",
            RunEventType.RUN_STOPPED,
            {},
            event_id=3,
        ),
        _event(
            "session-1",
            "run-9",
            RunEventType.TEXT_DELTA,
            {"text": "new output"},
            event_id=4,
        ),
        _event(
            "session-1",
            "run-9",
            RunEventType.RUN_COMPLETED,
            {},
            event_id=5,
        ),
    )

    response = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/resume",
            "params": {"sessionId": session_id},
        }
    )

    assert _require_result_object(response) == {
        "stopReason": "end_turn",
        "runId": "run-9",
        "runStatus": "completed",
        "recoverable": False,
    }
    assert run_manager.stream_calls == [("run-9", 3)]
    session_updates = [_session_update_name(item) for item in notifications]
    assert session_updates == ["agent_message_chunk"]
    update = _session_update_payload(notifications[0])
    content = update["content"]
    assert isinstance(content, dict)
    assert content["text"] == "new output"


@pytest.mark.asyncio
async def test_session_resume_suppresses_replayed_text_prefix_from_resumed_stream(
    tmp_path: Path,
) -> None:
    server, session_service, run_manager, notifications = _build_server(tmp_path)
    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )
    session_id = _require_str(_require_result_object(created), "sessionId")
    repository = GatewaySessionRepository(tmp_path / "gateway.db")
    record = repository.get(session_id)
    repository.update(record.model_copy(update={"active_run_id": "run-9"}))
    session_service.recovery_snapshot_by_session["session-1"] = {
        "active_run": {
            "run_id": "run-9",
            "last_event_id": 3,
        },
        "pending_tool_approvals": [],
        "paused_subagent": None,
        "round_snapshot": None,
    }
    session_service.global_events_by_session["session-1"] = [
        {
            "trace_id": "run-9",
            "event_type": RunEventType.TEXT_DELTA.value,
            "payload_json": json.dumps({"text": "LINE0001LINE0002"}),
        }
    ]
    run_manager.events_by_run["run-9"] = (
        _event(
            "session-1",
            "run-9",
            RunEventType.RUN_RESUMED,
            {"reason": "resume"},
            event_id=4,
        ),
        _event(
            "session-1",
            "run-9",
            RunEventType.TEXT_DELTA,
            {"text": "LINE0001LINE0002"},
            event_id=5,
        ),
        _event(
            "session-1",
            "run-9",
            RunEventType.TEXT_DELTA,
            {"text": "LINE0003"},
            event_id=6,
        ),
        _event(
            "session-1",
            "run-9",
            RunEventType.RUN_COMPLETED,
            {},
            event_id=7,
        ),
    )

    response = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/resume",
            "params": {"sessionId": session_id},
        }
    )

    assert _require_result_object(response) == {
        "stopReason": "end_turn",
        "runId": "run-9",
        "runStatus": "completed",
        "recoverable": False,
    }
    session_updates = [_session_update_name(item) for item in notifications]
    assert session_updates == ["agent_message_chunk"]
    update = _session_update_payload(notifications[0])
    content = update["content"]
    assert isinstance(content, dict)
    assert content["text"] == "LINE0003"


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

    assert _require_result_object(response) == {
        "stopReason": "end_turn",
        "runId": "run-1",
        "runStatus": "completed",
        "recoverable": False,
    }
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

    assert _require_result_object(response) == {
        "stopReason": "end_turn",
        "runId": "run-1",
        "runStatus": "completed",
        "recoverable": False,
    }
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
async def test_session_load_rebinds_internal_workspace_for_new_cwd(
    tmp_path: Path,
) -> None:
    workspace_service = FakeWorkspaceService()
    target_root = tmp_path / "project-b"
    target_root.mkdir()
    server, session_service, _, _ = _build_server(
        tmp_path,
        workspace_service=workspace_service,
    )

    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )
    session_id = _require_str(_require_result_object(created), "sessionId")
    repository = GatewaySessionRepository(tmp_path / "gateway.db")
    before = repository.get(session_id)
    assert session_service.get_session(before.internal_session_id).workspace_id == (
        "workspace-1"
    )

    loaded = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/load",
            "params": {
                "sessionId": session_id,
                "cwd": str(target_root),
                "mcpServers": [],
            },
        }
    )

    assert _require_result_object(loaded) == {"sessionId": session_id}
    after = repository.get(session_id)
    assert after.cwd == str(target_root.resolve())
    assert session_service.get_session(after.internal_session_id).workspace_id == (
        "workspace-2"
    )
    assert session_service.rebind_session_calls == [
        {
            "session_id": after.internal_session_id,
            "workspace_id": "workspace-2",
        }
    ]


@pytest.mark.asyncio
async def test_session_load_allows_same_workspace_when_active_run_exists(
    tmp_path: Path,
) -> None:
    workspace_service = FakeWorkspaceService()
    server, session_service, _, _ = _build_server(
        tmp_path,
        workspace_service=workspace_service,
    )

    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )
    session_id = _require_str(_require_result_object(created), "sessionId")
    repository = GatewaySessionRepository(tmp_path / "gateway.db")
    record = repository.get(session_id)
    session_service.active_run_session_ids.add(record.internal_session_id)

    loaded = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/load",
            "params": {
                "sessionId": session_id,
                "cwd": str(tmp_path.resolve()),
                "mcpServers": [],
            },
        }
    )

    assert _require_result_object(loaded) == {"sessionId": session_id}
    assert session_service.rebind_session_calls == []
    assert repository.get(session_id).cwd == str(tmp_path.resolve())


@pytest.mark.asyncio
async def test_session_load_rejects_workspace_rebind_while_run_is_active(
    tmp_path: Path,
) -> None:
    workspace_service = FakeWorkspaceService()
    target_root = tmp_path / "project-b"
    target_root.mkdir()
    server, session_service, _, _ = _build_server(
        tmp_path,
        workspace_service=workspace_service,
    )

    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )
    session_id = _require_str(_require_result_object(created), "sessionId")
    repository = GatewaySessionRepository(tmp_path / "gateway.db")
    before = repository.get(session_id)
    session_service.active_run_session_ids.add(before.internal_session_id)

    response = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/load",
            "params": {
                "sessionId": session_id,
                "cwd": str(target_root),
                "mcpServers": [],
            },
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {
            "code": -32000,
            "message": "Cannot rebind workspace while session has active or recoverable run",
        },
    }
    after = repository.get(session_id)
    assert after.cwd == str(tmp_path.resolve())
    assert session_service.get_session(after.internal_session_id).workspace_id == (
        "workspace-1"
    )
    assert session_service.rebind_session_calls == []


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

    assert _require_result_object(response) == {
        "stopReason": "end_turn",
        "runId": "run-1",
        "runStatus": "completed",
        "recoverable": False,
    }
    params = notifications[0]["params"]
    assert isinstance(params, dict)
    update = params["update"]
    assert isinstance(update, dict)
    content = update["content"]
    assert isinstance(content, dict)
    assert content["text"] == formatted_text


@pytest.mark.asyncio
async def test_session_cancel_falls_back_to_persisted_active_run_binding(
    tmp_path: Path,
) -> None:
    server, _, run_manager, _notifications = _build_server(tmp_path)
    created = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )
    session_id = _require_str(_require_result_object(created), "sessionId")
    repository = GatewaySessionRepository(tmp_path / "gateway.db")
    record = repository.get(session_id)
    repository.update(record.model_copy(update={"active_run_id": "run-cancel"}))

    response = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/cancel",
            "params": {"sessionId": session_id},
        }
    )

    assert _require_result_object(response) == {"status": "ok"}
    assert run_manager.stop_calls == ["run-cancel"]


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
        media_asset_service=cast(MediaAssetService, object()),
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
    *,
    workspace_service: FakeWorkspaceService | None = None,
    default_normal_root_role_id: str | None = None,
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
        workspace_service=cast(WorkspaceService | None, workspace_service),
        default_normal_root_role_id=default_normal_root_role_id,
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
        media_asset_service=cast(MediaAssetService, object()),
        notify=notify,
    )
    server.set_mcp_relay_outbound(send_request=send_request, send_notification=notify)
    return server, session_service, run_manager, notifications


def _event(
    session_id: str,
    run_id: str,
    event_type: RunEventType,
    payload: dict[str, JsonValue],
    *,
    event_id: int | None = None,
) -> RunEvent:
    return RunEvent(
        session_id=session_id,
        run_id=run_id,
        trace_id=run_id,
        event_type=event_type,
        payload_json=json.dumps(payload, ensure_ascii=False),
        event_id=event_id,
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
