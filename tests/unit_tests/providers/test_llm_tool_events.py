# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import httpx
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from agent_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from agent_teams.media import MediaAssetService
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.providers.model_config import ModelEndpointConfig
from agent_teams.providers.provider_contracts import LLMRequest
from agent_teams.providers.openai_compatible import OpenAICompatibleProvider
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.tools.runtime import ToolApprovalManager
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.tools.runtime import ToolApprovalPolicy
from agent_teams.tools.registry import ToolRegistry
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.roles import RoleMemoryService
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.agents.orchestration.task_execution_service import TaskExecutionService
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.workspace import WorkspaceManager


class _FakeRunEventHub:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


class _FakeRunControlManager:
    def is_run_stop_requested(self, run_id: str) -> bool:
        return False

    def is_subagent_stop_requested(self, *, run_id: str, instance_id: str) -> bool:
        return False


class _FakeTaskRepository:
    pass


class _FakeSharedStateRepository:
    pass


class _FakeEventLog:
    pass


class _FakeMessageRepository:
    def __init__(self) -> None:
        self._messages_by_conversation: dict[
            str, list[ModelRequest | ModelResponse]
        ] = {}

    def append(
        self,
        *,
        session_id: str,
        workspace_id: str,
        conversation_id: str,
        agent_role_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> None:
        _ = (
            session_id,
            workspace_id,
            agent_role_id,
            instance_id,
            task_id,
            trace_id,
        )
        stored = self._messages_by_conversation.setdefault(conversation_id, [])
        stored.extend(messages)

    def get_history_for_conversation(
        self, conversation_id: str
    ) -> list[ModelRequest | ModelResponse]:
        return list(self._messages_by_conversation.get(conversation_id, []))


def _provider_with_hub(hub: _FakeRunEventHub) -> OpenAICompatibleProvider:
    config = ModelEndpointConfig(
        model="gpt-test",
        base_url="http://localhost",
        api_key="test-key",
    )
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="coordinator",
            description="Coordinates delegated work.",
            version="1",
            tools=(),
            system_prompt="Coordinate work.",
        )
    )
    db_path = Path(tempfile.mkstemp(suffix=".db")[1])
    shared_store = SharedStateRepository(db_path)
    session_history_marker_repo = SessionHistoryMarkerRepository(db_path)
    return OpenAICompatibleProvider(
        config,
        task_repo=cast(TaskRepository, cast(object, _FakeTaskRepository())),
        shared_store=shared_store,
        event_bus=cast(EventLog, cast(object, _FakeEventLog())),
        injection_manager=cast(RunInjectionManager, object()),
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        agent_repo=cast(AgentInstanceRepository, object()),
        approval_ticket_repo=cast(ApprovalTicketRepository, object()),
        run_runtime_repo=cast(RunRuntimeRepository, object()),
        run_intent_repo=cast(RunIntentRepository, object()),
        background_task_service=None,
        workspace_manager=WorkspaceManager(
            project_root=Path("."),
            shared_store=shared_store,
        ),
        media_asset_service=cast(MediaAssetService, object()),
        role_memory_service=cast(RoleMemoryService | None, None),
        subagent_reflection_service=None,
        tool_registry=cast(ToolRegistry, object()),
        mcp_registry=cast(McpRegistry, object()),
        skill_registry=cast(SkillRegistry, object()),
        allowed_tools=(),
        allowed_mcp_servers=(),
        allowed_skills=(),
        message_repo=cast(MessageRepository, object()),
        session_history_marker_repo=session_history_marker_repo,
        role_registry=role_registry,
        task_execution_service=cast(TaskExecutionService, object()),
        task_service=cast(TaskOrchestrationService, object()),
        run_control_manager=cast(
            RunControlManager, cast(object, _FakeRunControlManager())
        ),
        tool_approval_manager=cast(ToolApprovalManager, object()),
        tool_approval_policy=ToolApprovalPolicy(),
    )


def _request() -> LLMRequest:
    return LLMRequest(
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="default",
        instance_id="inst-1",
        role_id="Coordinator",
        system_prompt="sys",
        user_prompt="user",
    )


def test_publish_tool_events_emits_call_validation_failure_and_result() -> None:
    hub = _FakeRunEventHub()
    provider = _provider_with_hub(hub)

    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="create_tasks",
                    args={"objective": "x"},
                    tool_call_id="call-1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                RetryPromptPart(
                    content="Invalid arguments for tool create_tasks",
                    tool_name="create_tasks",
                    tool_call_id="call-1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="create_tasks",
                    content={"ok": True},
                    tool_call_id="call-2",
                )
            ]
        ),
    ]

    provider._publish_tool_call_events_from_messages(
        request=_request(),
        messages=messages,
    )
    provider._publish_committed_tool_outcome_events_from_messages(
        request=_request(),
        messages=messages,
    )

    event_types = [event.event_type for event in hub.events]
    assert event_types == [
        RunEventType.TOOL_CALL,
        RunEventType.TOOL_INPUT_VALIDATION_FAILED,
        RunEventType.TOOL_RESULT,
    ]

    tool_call_payload = json.loads(hub.events[0].payload_json)
    assert tool_call_payload["tool_name"] == "create_tasks"
    assert tool_call_payload["tool_call_id"] == "call-1"

    validation_payload = json.loads(hub.events[1].payload_json)
    assert validation_payload["tool_name"] == "create_tasks"
    assert validation_payload["tool_call_id"] == "call-1"
    assert (
        validation_payload["reason"] == "Input validation failed before tool execution."
    )
    assert validation_payload["details"] == "Invalid arguments for tool create_tasks"

    tool_result_payload = json.loads(hub.events[2].payload_json)
    assert tool_result_payload["tool_name"] == "create_tasks"
    assert tool_result_payload["tool_call_id"] == "call-2"
    assert tool_result_payload["error"] is False


def test_commit_ready_messages_defers_tool_call_event_until_safe_commit() -> None:
    hub = _FakeRunEventHub()
    provider = _provider_with_hub(hub)
    fake_repo = _FakeMessageRepository()
    provider._session._message_repo = cast(
        MessageRepository,
        cast(object, fake_repo),
    )

    history, pending, tool_events_published = provider._session._commit_ready_messages(
        request=_request(),
        history=[],
        pending_messages=[
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="create_tasks",
                        args={"objective": "x"},
                        tool_call_id="call-unsafe",
                    )
                ]
            )
        ],
    )

    assert history == []
    assert len(pending) == 1
    assert tool_events_published is False
    assert hub.events == []


def test_commit_ready_messages_publishes_only_tool_outcomes_after_safe_commit() -> None:
    hub = _FakeRunEventHub()
    provider = _provider_with_hub(hub)
    fake_repo = _FakeMessageRepository()
    provider._session._message_repo = cast(
        MessageRepository,
        cast(object, fake_repo),
    )

    history, pending, tool_events_published = provider._session._commit_ready_messages(
        request=_request(),
        history=[],
        pending_messages=[
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="create_tasks",
                        args={"objective": "x"},
                        tool_call_id="call-safe",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="create_tasks",
                        content={"ok": True},
                        tool_call_id="call-safe",
                    )
                ]
            ),
        ],
    )

    assert len(history) == 2
    assert pending == []
    assert tool_events_published is True
    assert [event.event_type for event in hub.events] == [RunEventType.TOOL_RESULT]


def test_publish_tool_call_events_deduplicates_published_tool_call_ids() -> None:
    hub = _FakeRunEventHub()
    provider = _provider_with_hub(hub)
    published_tool_call_ids: set[str] = set()

    emitted_first = provider._publish_tool_call_events_from_messages(
        request=_request(),
        messages=[
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="create_tasks",
                        args={"objective": "x"},
                        tool_call_id="call-live",
                    )
                ]
            )
        ],
        published_tool_call_ids=published_tool_call_ids,
    )
    emitted_second = provider._publish_tool_call_events_from_messages(
        request=_request(),
        messages=[
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="create_tasks",
                        args={"objective": "x"},
                        tool_call_id="call-live",
                    )
                ]
            )
        ],
        published_tool_call_ids=published_tool_call_ids,
    )

    assert emitted_first is True
    assert emitted_second is False
    assert [event.event_type for event in hub.events] == [RunEventType.TOOL_CALL]


def test_publish_tool_events_skips_retry_without_tool_name() -> None:
    hub = _FakeRunEventHub()
    provider = _provider_with_hub(hub)

    provider._publish_committed_tool_outcome_events_from_messages(
        request=_request(),
        messages=[ModelRequest(parts=[RetryPromptPart(content="retry output")])],
    )

    assert hub.events == []


def test_publish_tool_events_sanitizes_stale_task_status_error() -> None:
    hub = _FakeRunEventHub()
    provider = _provider_with_hub(hub)

    provider._publish_committed_tool_outcome_events_from_messages(
        request=_request(),
        messages=[
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="dispatch_task",
                        tool_call_id="dispatch_task:1",
                        content={
                            "ok": True,
                            "data": {
                                "task_status": {
                                    "ask_time": {
                                        "task_name": "ask_time",
                                        "task_id": "task-1",
                                        "role_id": "time",
                                        "instance_id": "inst-1",
                                        "status": "completed",
                                        "result": "Current time is 2026-03-07 00:41:29.",
                                        "error": "Task stopped by user",
                                    }
                                }
                            },
                        },
                    )
                ]
            )
        ],
    )

    payload = json.loads(hub.events[0].payload_json)
    task_status = payload["result"]["data"]["task_status"]["ask_time"]
    assert task_status["status"] == "completed"
    assert task_status["result"] == "Current time is 2026-03-07 00:41:29."
    assert "error" not in task_status


def test_build_model_api_error_message_surfaces_proxy_auth_failure() -> None:
    provider = _provider_with_hub(_FakeRunEventHub())

    try:
        raise ModelAPIError(model_name="gpt-test", message="Connection error.") from (
            httpx.ProxyError("407 Proxy Authentication Required")
        )
    except ModelAPIError as exc:
        message = provider._build_model_api_error_message(exc)

    assert "Proxy authentication failed (HTTP 407)." in message
    assert "HTTP_PROXY/HTTPS_PROXY credentials" in message


def test_build_model_api_error_message_surfaces_connect_timeout() -> None:
    provider = _provider_with_hub(_FakeRunEventHub())

    try:
        raise ModelAPIError(model_name="gpt-test", message="Request timed out.") from (
            httpx.ConnectTimeout("connect timed out")
        )
    except ModelAPIError as exc:
        message = provider._build_model_api_error_message(exc)

    assert "Connection to the model endpoint timed out." in message
    assert "increase connect_timeout_seconds" in message


def test_build_model_api_error_message_keeps_root_cause_context() -> None:
    provider = _provider_with_hub(_FakeRunEventHub())

    try:
        raise ModelAPIError(model_name="gpt-test", message="Connection error.") from (
            RuntimeError("TLS handshake failed")
        )
    except ModelAPIError as exc:
        message = provider._build_model_api_error_message(exc)

    assert message == "Connection error. Root cause: TLS handshake failed"
