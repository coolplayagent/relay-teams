# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from relay_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from relay_teams.media import MediaAssetService
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.providers.model_config import ModelEndpointConfig
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.providers.openai_compatible import OpenAICompatibleProvider
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.tools.runtime.approval_state import ToolApprovalManager
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.execution.event_publishing import (
    EventPublishingService,
    _args_preview,
)
from relay_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.tools.runtime.policy import ToolApprovalPolicy
from relay_teams.tools.runtime.persisted_state import (
    ToolCallBatchStatus,
    ToolExecutionStatus,
    load_tool_call_batch_state,
    load_tool_call_state,
    merge_tool_call_state,
)
from relay_teams.tools.registry import ToolRegistry
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles import RoleMemoryService
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.agents.orchestration.task_execution_service import TaskExecutionService
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.workspace import WorkspaceManager


class _FakeRunEventHub:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


class _FakeAsyncRunEventHub(_FakeRunEventHub):
    async def publish_async(self, event) -> None:
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
        profile_name=None,
        task_repo=cast(TaskRepository, cast(object, _FakeTaskRepository())),
        shared_store=shared_store,
        event_bus=cast(EventLog, cast(object, _FakeEventLog())),
        injection_manager=cast(RunInjectionManager, object()),
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        agent_repo=cast(AgentInstanceRepository, object()),
        approval_ticket_repo=cast(ApprovalTicketRepository, object()),
        user_question_repo=None,
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
                    tool_name="orch_create_tasks",
                    args={"objective": "x"},
                    tool_call_id="call-1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                RetryPromptPart(
                    content="Invalid arguments for tool orch_create_tasks",
                    tool_name="orch_create_tasks",
                    tool_call_id="call-1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="orch_create_tasks",
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
        RunEventType.TOOL_CALL_BATCH_SEALED,
        RunEventType.TOOL_INPUT_VALIDATION_FAILED,
        RunEventType.TOOL_RESULT,
    ]

    tool_call_payload = json.loads(hub.events[0].payload_json)
    assert tool_call_payload["tool_name"] == "orch_create_tasks"
    assert tool_call_payload["tool_call_id"] == "call-1"
    assert tool_call_payload["batch_index"] == 0
    assert tool_call_payload["batch_size"] == 1

    batch_payload = json.loads(hub.events[1].payload_json)
    assert batch_payload["tool_calls"] == [
        {
            "tool_call_id": "call-1",
            "tool_name": "orch_create_tasks",
            "args": '{"objective": "x"}',
            "index": 0,
        }
    ]

    validation_payload = json.loads(hub.events[2].payload_json)
    assert validation_payload["tool_name"] == "orch_create_tasks"
    assert validation_payload["tool_call_id"] == "call-1"
    assert (
        validation_payload["reason"] == "Input validation failed before tool execution."
    )
    assert (
        validation_payload["details"] == "Invalid arguments for tool orch_create_tasks"
    )

    tool_result_payload = json.loads(hub.events[3].payload_json)
    assert tool_result_payload["tool_name"] == "orch_create_tasks"
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

    history, pending, tool_events_published, committed_tool_validation_failures = (
        provider._session._commit_ready_messages(
            request=_request(),
            history=[],
            pending_messages=[
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="orch_create_tasks",
                            args={"objective": "x"},
                            tool_call_id="call-unsafe",
                        )
                    ]
                )
            ],
        )
    )

    assert history == []
    assert len(pending) == 1
    assert tool_events_published is False
    assert committed_tool_validation_failures is False
    assert hub.events == []


def test_commit_ready_messages_publishes_only_tool_outcomes_after_safe_commit() -> None:
    hub = _FakeRunEventHub()
    provider = _provider_with_hub(hub)
    fake_repo = _FakeMessageRepository()
    provider._session._message_repo = cast(
        MessageRepository,
        cast(object, fake_repo),
    )

    history, pending, tool_events_published, committed_tool_validation_failures = (
        provider._session._commit_ready_messages(
            request=_request(),
            history=[],
            pending_messages=[
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="orch_create_tasks",
                            args={"objective": "x"},
                            tool_call_id="call-safe",
                        )
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="orch_create_tasks",
                            content={"ok": True},
                            tool_call_id="call-safe",
                        )
                    ]
                ),
            ],
        )
    )

    assert len(history) == 2
    assert pending == []
    assert tool_events_published is True
    assert committed_tool_validation_failures is False
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
                        tool_name="orch_create_tasks",
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
                        tool_name="orch_create_tasks",
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
    assert [event.event_type for event in hub.events] == [
        RunEventType.TOOL_CALL,
        RunEventType.TOOL_CALL_BATCH_SEALED,
    ]


def test_publish_tool_call_events_persists_sealed_parallel_batch() -> None:
    hub = _FakeRunEventHub()
    provider = _provider_with_hub(hub)
    request = _request()

    emitted = provider._publish_tool_call_events_from_messages(
        request=request,
        messages=[
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="read",
                        args={"path": "a.txt"},
                        tool_call_id="call-a",
                    ),
                    ToolCallPart(
                        tool_name="read",
                        args={"path": "b.txt"},
                        tool_call_id="call-b",
                    ),
                ]
            )
        ],
    )

    assert emitted is True
    batch_payloads = [
        json.loads(event.payload_json)
        for event in hub.events
        if event.event_type == RunEventType.TOOL_CALL_BATCH_SEALED
    ]
    assert len(batch_payloads) == 1
    batch_id = str(batch_payloads[0]["batch_id"])
    batch_state = load_tool_call_batch_state(
        shared_store=provider._session._shared_store,
        task_id=request.task_id,
        batch_id=batch_id,
    )
    assert batch_state is not None
    assert batch_state.status == ToolCallBatchStatus.SEALED
    assert [item.tool_call_id for item in batch_state.items] == ["call-a", "call-b"]

    first_call_state = load_tool_call_state(
        shared_store=provider._session._shared_store,
        task_id=request.task_id,
        tool_call_id="call-a",
    )
    second_call_state = load_tool_call_state(
        shared_store=provider._session._shared_store,
        task_id=request.task_id,
        tool_call_id="call-b",
    )
    assert first_call_state is not None
    assert second_call_state is not None
    assert first_call_state.batch_id == batch_id
    assert second_call_state.batch_id == batch_id
    assert first_call_state.batch_index == 0
    assert second_call_state.batch_index == 1
    assert first_call_state.batch_size == 2
    assert second_call_state.batch_size == 2


def test_event_publishing_service_covers_sync_batch_edge_cases(
    tmp_path: Path,
) -> None:
    hub = _FakeRunEventHub()
    shared_store = SharedStateRepository(tmp_path / "event-publishing-sync.db")
    service = EventPublishingService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        shared_store=shared_store,
    )
    request = _request()

    assert (
        service.publish_tool_call_events_from_messages(
            request=request,
            messages=[ModelRequest(parts=[]), ModelResponse(parts=[])],
        )
        is False
    )
    assert (
        service.publish_observed_tool_call_event(
            request=request,
            part=ToolCallPart(tool_name="", args={}, tool_call_id=""),
            batch_id="batch-empty",
            batch_index=0,
            batch_size=1,
        )
        is False
    )
    service.seal_tool_call_batch(request=request, batch_id="batch-empty", tool_calls=[])
    service.seal_tool_call_batch(
        request=request,
        batch_id="batch-invalid",
        tool_calls=[(0, ToolCallPart(tool_name="", args={}, tool_call_id=""))],
    )
    emitted = service.publish_observed_tool_call_event(
        request=request,
        part=ToolCallPart(
            tool_name="read", args={"path": "a.txt"}, tool_call_id="call-a"
        ),
        batch_id="batch-sealed",
        batch_index=0,
        batch_size=1,
    )
    service.seal_tool_call_batch(
        request=request,
        batch_id="batch-sealed",
        tool_calls=[
            (
                0,
                ToolCallPart(
                    tool_name="read",
                    args={"path": "a.txt"},
                    tool_call_id="call-a",
                ),
            )
        ],
    )
    event_count_after_first_seal = len(hub.events)
    service.seal_tool_call_batch(
        request=request,
        batch_id="batch-sealed",
        tool_calls=[
            (
                0,
                ToolCallPart(
                    tool_name="read",
                    args={"path": "a.txt"},
                    tool_call_id="call-a",
                ),
            )
        ],
    )
    circular: list[object] = []
    circular.append(circular)

    assert emitted is True
    assert len(hub.events) == event_count_after_first_seal
    assert (
        service._batch_id_for_tool_calls(
            request=request,
            tool_calls=[
                (0, ToolCallPart(tool_name="read", args={}, tool_call_id="")),
                (1, ToolCallPart(tool_name="read", args={}, tool_call_id="call-a")),
            ],
        )
        == "batch-sealed"
    )
    assert _args_preview(circular).startswith("[[")


@pytest.mark.asyncio
async def test_event_publishing_service_covers_async_batch_edge_cases(
    tmp_path: Path,
) -> None:
    hub = _FakeAsyncRunEventHub()
    shared_store = SharedStateRepository(tmp_path / "event-publishing-async.db")
    service = EventPublishingService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        shared_store=shared_store,
    )
    request = _request()

    assert (
        await service.publish_observed_tool_call_event_async(
            request=request,
            part=ToolCallPart(tool_name="", args={}, tool_call_id=""),
            batch_id="batch-empty",
            batch_index=0,
            batch_size=1,
        )
        is False
    )
    published = {"call-a"}
    assert (
        await service.publish_observed_tool_call_event_async(
            request=request,
            part=ToolCallPart(tool_name="read", args={}, tool_call_id="call-a"),
            batch_id="batch-empty",
            batch_index=0,
            batch_size=1,
            published_tool_call_ids=published,
        )
        is False
    )
    await service.seal_tool_call_batch_async(
        request=request,
        batch_id="batch-empty",
        tool_calls=[],
    )
    await service.seal_tool_call_batch_async(
        request=request,
        batch_id="batch-invalid",
        tool_calls=[(0, ToolCallPart(tool_name="", args={}, tool_call_id=""))],
    )
    await service.publish_observed_tool_call_event_async(
        request=request,
        part=ToolCallPart(
            tool_name="read", args={"path": "a.txt"}, tool_call_id="call-b"
        ),
        batch_id="batch-async",
        batch_index=0,
        batch_size=1,
    )
    await service.seal_tool_call_batch_async(
        request=request,
        batch_id="batch-async",
        tool_calls=[
            (
                0,
                ToolCallPart(
                    tool_name="read",
                    args={"path": "a.txt"},
                    tool_call_id="call-b",
                ),
            )
        ],
    )
    event_count_after_first_seal = len(hub.events)
    await service.seal_tool_call_batch_async(
        request=request,
        batch_id="batch-async",
        tool_calls=[
            (
                0,
                ToolCallPart(
                    tool_name="read",
                    args={"path": "a.txt"},
                    tool_call_id="call-b",
                ),
            )
        ],
    )
    service_without_store = EventPublishingService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        shared_store=None,
    )

    assert len(hub.events) == event_count_after_first_seal
    assert (
        await service._batch_id_for_tool_calls_async(
            request=request,
            tool_calls=[
                (0, ToolCallPart(tool_name="read", args={}, tool_call_id="")),
                (1, ToolCallPart(tool_name="read", args={}, tool_call_id="call-b")),
            ],
        )
        == "batch-async"
    )
    assert (
        await service_without_store._tool_call_batch_is_sealed_async(
            request=request,
            batch_id="missing",
        )
        is False
    )
    assert (
        await service_without_store._tool_call_batch_has_observed_items_async(
            request=request,
            batch_id="missing",
        )
        is False
    )
    await service_without_store.seal_tool_call_batch_async(
        request=request,
        batch_id="batch-no-store",
        tool_calls=[
            (
                0,
                ToolCallPart(
                    tool_name="read",
                    args={"path": "z.txt"},
                    tool_call_id="call-z",
                ),
            )
        ],
    )


def test_publish_tool_events_skips_retry_without_tool_name() -> None:
    hub = _FakeRunEventHub()
    provider = _provider_with_hub(hub)

    provider._publish_committed_tool_outcome_events_from_messages(
        request=_request(),
        messages=[ModelRequest(parts=[RetryPromptPart(content="retry output")])],
    )

    assert hub.events == []


def test_publish_tool_events_skips_tool_result_already_emitted_from_runtime() -> None:
    hub = _FakeRunEventHub()
    provider = _provider_with_hub(hub)
    request = _request()
    merge_tool_call_state(
        shared_store=provider._session._shared_store,
        task_id=request.task_id,
        tool_call_id="orch_dispatch_task:1",
        tool_name="orch_dispatch_task",
        run_id=request.run_id,
        session_id=request.session_id,
        instance_id=request.instance_id,
        role_id=request.role_id,
        execution_status=ToolExecutionStatus.COMPLETED,
        result_envelope={
            "runtime_meta": {"tool_result_event_published": True},
        },
    )

    provider._publish_committed_tool_outcome_events_from_messages(
        request=request,
        messages=[
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="orch_dispatch_task",
                        tool_call_id="orch_dispatch_task:1",
                        content={"ok": True, "data": {"status": "queued"}},
                    )
                ]
            )
        ],
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
                        tool_name="orch_dispatch_task",
                        tool_call_id="orch_dispatch_task:1",
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
