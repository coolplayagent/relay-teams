# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from openai import APIError, APIStatusError
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPartDelta,
    TextPart,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
    UserPromptPart,
)

import relay_teams.agents.execution.llm_session as llm_module
from relay_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from relay_teams.agents.orchestration.task_execution_service import (
    TaskExecutionService,
)
from relay_teams.media import MediaAssetService
from relay_teams.mcp.mcp_models import McpToolSchema
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.agents.execution.system_prompts import PromptSkillInstruction
from relay_teams.agents.execution.conversation_compaction import (
    build_conversation_compaction_budget,
    ConversationCompactionService,
)
from relay_teams.agents.execution.subagent_reflection import SubagentReflectionService
from relay_teams.agents.execution.llm_session import _PreparedPromptContext
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.providers.openai_compatible import OpenAICompatibleProvider
import relay_teams.providers.llm_retry as llm_retry_module
from relay_teams.providers.model_config import ModelEndpointConfig, SamplingConfig
from relay_teams.roles import RoleMemoryService
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.sessions.runs.assistant_errors import AssistantRunError
from relay_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.tools.registry import ToolRegistry
from relay_teams.tools.runtime import ToolApprovalManager, ToolApprovalPolicy
from relay_teams.workspace import WorkspaceManager, build_conversation_id


class _FakeRunEventHub:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def publish(self, event: RunEvent) -> None:
        self.events.append(event)


class _FakeControlContext:
    def raise_if_cancelled(self) -> None:
        return


class _FakeRunControlManager:
    def context(
        self, *, run_id: str, instance_id: str | None = None
    ) -> _FakeControlContext:
        _ = (run_id, instance_id)
        return _FakeControlContext()


class _CountingRunControlManager:
    def __init__(self, *, cancel_after: int | None = None) -> None:
        self.cancel_after = cancel_after
        self.calls = 0

    def context(
        self, *, run_id: str, instance_id: str | None = None
    ) -> _FakeControlContext:
        _ = (run_id, instance_id)
        manager = self

        class _Ctx:
            def raise_if_cancelled(self) -> None:
                manager.calls += 1
                if (
                    manager.cancel_after is not None
                    and manager.calls >= manager.cancel_after
                ):
                    raise asyncio.CancelledError

        return cast(_FakeControlContext, cast(object, _Ctx()))


class _FakeInjectionManager:
    def drain_at_boundary(self, run_id: str, instance_id: str) -> list[object]:
        _ = (run_id, instance_id)
        return []


class _FakeConversationCompactionService:
    def __init__(self, prompt_sections: list[str]) -> None:
        self._prompt_sections = list(prompt_sections)
        self._build_calls = 0

    async def maybe_compact(
        self,
        *,
        session_id: str,
        role_id: str,
        conversation_id: str,
        history: list[ModelRequest | ModelResponse],
        source_history: list[ModelRequest | ModelResponse] | None = None,
        budget: object | None = None,
        estimated_tokens_before_microcompact: int | None = None,
        estimated_tokens_after_microcompact: int | None = None,
    ) -> list[ModelRequest | ModelResponse]:
        _ = (
            session_id,
            role_id,
            conversation_id,
            budget,
            source_history,
            estimated_tokens_before_microcompact,
            estimated_tokens_after_microcompact,
        )
        return list(history)

    def build_prompt_section(
        self,
        *,
        session_id: str,
        conversation_id: str,
    ) -> str:
        _ = (session_id, conversation_id)
        if not self._prompt_sections:
            return ""
        index = min(self._build_calls, len(self._prompt_sections) - 1)
        self._build_calls += 1
        return self._prompt_sections[index]


class _FakeSkillRegistry:
    def __init__(self, entries: tuple[PromptSkillInstruction, ...]) -> None:
        self._entries = entries
        self.requested: list[tuple[str, ...]] = []

    def get_instruction_entries(
        self, skill_names: tuple[str, ...]
    ) -> tuple[PromptSkillInstruction, ...]:
        self.requested.append(skill_names)
        return self._entries


class _FakeMcpSchemaRegistry:
    def __init__(
        self,
        schemas_by_server: dict[str, tuple[McpToolSchema, ...]],
        *,
        fail_on_list: bool = False,
    ) -> None:
        self._schemas_by_server = schemas_by_server
        self._fail_on_list = fail_on_list
        self.list_calls = 0

    def resolve_server_names(
        self,
        server_names: tuple[str, ...],
        *,
        strict: bool = True,
        consumer: str | None = None,
    ) -> tuple[str, ...]:
        _ = (strict, consumer)
        return tuple(
            server_name
            for server_name in server_names
            if server_name in self._schemas_by_server
        )

    async def list_tool_schemas(self, name: str) -> tuple[McpToolSchema, ...]:
        self.list_calls += 1
        if self._fail_on_list:
            raise AssertionError("list_tool_schemas should not be called")
        return self._schemas_by_server[name]

    def get_toolsets(self, server_names: tuple[str, ...]) -> tuple[object, ...]:
        return tuple(object() for _ in server_names)


class _FakeSubagentReflectionService:
    def __init__(self) -> None:
        self.calls: list[tuple[RoleDefinition, str, str, list[object]]] = []

    async def maybe_compact(
        self,
        *,
        role: RoleDefinition,
        workspace_id: str,
        conversation_id: str,
        history: list[object],
    ) -> list[object]:
        self.calls.append((role, workspace_id, conversation_id, list(history)))
        return history


class _FakeResult:
    def __init__(self) -> None:
        self.response = "ok"

    def new_messages(self) -> list[object]:
        return []

    def usage(self) -> SimpleNamespace:
        return SimpleNamespace(
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            requests=1,
            tool_calls=0,
        )


class _FakeAgentRun:
    def __init__(self) -> None:
        self.result = _FakeResult()

    async def __aenter__(self) -> _FakeAgentRun:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        _ = (exc_type, exc, tb)
        return False

    def __aiter__(self) -> _FakeAgentRun:
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    def new_messages(self) -> list[object]:
        return []


class _FakeAgent:
    def __init__(self) -> None:
        self.prompts: list[str | None] = []
        self.usage_limits: list[object] = []
        self.histories: list[list[object]] = []

    def iter(
        self,
        prompt: str | None,
        *,
        deps: object,
        message_history: object,
        usage_limits: object,
    ) -> _FakeAgentRun:
        _ = deps
        self.prompts.append(prompt)
        self.usage_limits.append(usage_limits)
        self.histories.append(list(cast(list[object], message_history)))
        return _FakeAgentRun()


class _StreamingTextNode:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    def stream(self, ctx: object):
        _ = ctx
        chunks = list(self._chunks)

        class _Stream:
            async def stream_text(self, *, delta: bool):
                _ = delta
                for chunk in chunks:
                    yield chunk

            def usage(self) -> SimpleNamespace:
                return SimpleNamespace(
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    requests=1,
                    tool_calls=0,
                )

        class _Ctx:
            async def __aenter__(self):
                return _Stream()

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                _ = (exc_type, exc, tb)
                return False

        return _Ctx()


class _ScriptedResult:
    def __init__(
        self,
        *,
        response: object,
        messages: list[object],
    ) -> None:
        self.response = response
        self._messages = messages

    def new_messages(self) -> list[object]:
        return list(self._messages)

    def usage(self) -> SimpleNamespace:
        return SimpleNamespace(
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            requests=1,
            tool_calls=0,
        )


class _ScriptedAgentRun:
    def __init__(
        self,
        *,
        nodes: list[object],
        messages_by_step: list[list[object]],
        result: _ScriptedResult,
        raise_on_exhaust: BaseException | None = None,
    ) -> None:
        self._nodes = list(nodes)
        self._messages_by_step = list(messages_by_step)
        self._yielded = 0
        self._raise_on_exhaust = raise_on_exhaust
        self.ctx = object()
        self.result = result

    async def __aenter__(self) -> _ScriptedAgentRun:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        _ = (exc_type, exc, tb)
        return False

    def __aiter__(self) -> _ScriptedAgentRun:
        return self

    async def __anext__(self):
        if self._yielded < len(self._nodes):
            node = self._nodes[self._yielded]
            self._yielded += 1
            return node
        if self._raise_on_exhaust is not None:
            exc = self._raise_on_exhaust
            self._raise_on_exhaust = None
            raise exc
        raise StopAsyncIteration

    def new_messages(self) -> list[object]:
        collected: list[object] = []
        for batch in self._messages_by_step[: self._yielded]:
            collected.extend(batch)
        return collected

    def usage(self) -> SimpleNamespace:
        return SimpleNamespace(
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            requests=0,
            tool_calls=0,
        )


class _SequentialAgent:
    def __init__(self, runs: list[_ScriptedAgentRun]) -> None:
        self._runs = list(runs)
        self.prompts: list[str | None] = []
        self.histories: list[list[object]] = []

    def iter(
        self,
        prompt: str | None,
        *,
        deps: object,
        message_history: object,
        usage_limits: object,
    ) -> _ScriptedAgentRun:
        _ = (deps, usage_limits)
        self.prompts.append(prompt)
        self.histories.append(list(cast(list[object], message_history)))
        if not self._runs:
            raise AssertionError("no scripted runs remaining")
        return self._runs.pop(0)


class _FakeNodeStream:
    def __init__(self, usage_snapshot: SimpleNamespace) -> None:
        self._usage_snapshot = usage_snapshot

    async def stream_text(self, *, delta: bool):
        _ = delta
        if False:
            yield ""

    def usage(self) -> SimpleNamespace:
        return self._usage_snapshot


class _FakeNodeStreamContext:
    def __init__(self, stream: _FakeNodeStream) -> None:
        self._stream = stream

    async def __aenter__(self) -> _FakeNodeStream:
        return self._stream

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        _ = (exc_type, exc, tb)
        return False


class _FakeModelRequestNode:
    def __init__(self, usage_after: SimpleNamespace) -> None:
        self._usage_after = usage_after

    def stream(self, ctx: object) -> _FakeNodeStreamContext:
        _ = ctx
        return _FakeNodeStreamContext(_FakeNodeStream(self._usage_after))


class _FakeResultLargeUsage:
    def __init__(self) -> None:
        self.response = "ok"

    def new_messages(self) -> list[object]:
        return []

    def usage(self) -> SimpleNamespace:
        return SimpleNamespace(
            input_tokens=999_999,
            cache_read_tokens=444_444,
            output_tokens=888_888,
            total_tokens=1_888_887,
            requests=9,
            tool_calls=5,
            details={"reasoning_tokens": 222_222},
        )


class _FakeAgentRunWithNode:
    def __init__(self, node: _FakeModelRequestNode) -> None:
        self._node = node
        self._yielded = False
        self.ctx = object()
        self.result = _FakeResultLargeUsage()

    async def __aenter__(self) -> _FakeAgentRunWithNode:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        _ = (exc_type, exc, tb)
        return False

    def __aiter__(self) -> _FakeAgentRunWithNode:
        return self

    async def __anext__(self) -> _FakeModelRequestNode:
        if self._yielded:
            raise StopAsyncIteration
        self._yielded = True
        return self._node

    def new_messages(self) -> list[object]:
        return []

    def usage(self) -> SimpleNamespace:
        return SimpleNamespace(
            input_tokens=100,
            output_tokens=10,
            total_tokens=110,
            requests=0,
            tool_calls=0,
        )


class _FakeAgentWithNode:
    def __init__(self, node: _FakeModelRequestNode) -> None:
        self._node = node

    def iter(
        self,
        prompt: str | None,
        *,
        deps: object,
        message_history: object,
        usage_limits: object,
    ) -> _FakeAgentRunWithNode:
        _ = (prompt, deps, message_history, usage_limits)
        return _FakeAgentRunWithNode(self._node)


class _FakeNodeStreamWithMutation:
    def __init__(self, usage_obj: SimpleNamespace) -> None:
        self._usage_obj = usage_obj

    async def stream_text(self, *, delta: bool):
        _ = delta
        if False:
            yield ""

    def usage(self) -> SimpleNamespace:
        return self._usage_obj


class _FakeNodeStreamMutationContext:
    def __init__(self, usage_obj: SimpleNamespace) -> None:
        self._usage_obj = usage_obj

    async def __aenter__(self) -> _FakeNodeStreamWithMutation:
        self._usage_obj.input_tokens = 130
        self._usage_obj.cache_read_tokens = 21
        self._usage_obj.output_tokens = 19
        self._usage_obj.total_tokens = 149
        self._usage_obj.requests = 1
        self._usage_obj.tool_calls = 5
        self._usage_obj.details = {"reasoning_tokens": 6}
        return _FakeNodeStreamWithMutation(self._usage_obj)

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        _ = (exc_type, exc, tb)
        return False


class _FakeModelRequestNodeMutatesUsage:
    def __init__(self, usage_obj: SimpleNamespace) -> None:
        self._usage_obj = usage_obj

    def stream(self, ctx: object) -> _FakeNodeStreamMutationContext:
        _ = ctx
        return _FakeNodeStreamMutationContext(self._usage_obj)


class _FakeAgentRunWithMutableUsage:
    def __init__(self) -> None:
        self._yielded = False
        self.ctx = object()
        self._usage = SimpleNamespace(
            input_tokens=100,
            cache_read_tokens=8,
            output_tokens=10,
            total_tokens=110,
            requests=0,
            tool_calls=0,
            details={"reasoning_tokens": 1},
        )
        self._node = _FakeModelRequestNodeMutatesUsage(self._usage)
        self.result = _FakeResultLargeUsage()

    async def __aenter__(self) -> _FakeAgentRunWithMutableUsage:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        _ = (exc_type, exc, tb)
        return False

    def __aiter__(self) -> _FakeAgentRunWithMutableUsage:
        return self

    async def __anext__(self) -> _FakeModelRequestNodeMutatesUsage:
        if self._yielded:
            raise StopAsyncIteration
        self._yielded = True
        return self._node

    def new_messages(self) -> list[object]:
        return []

    def usage(self) -> SimpleNamespace:
        return self._usage


class _FakeAgentWithMutableUsageNode:
    def iter(
        self,
        prompt: str | None,
        *,
        deps: object,
        message_history: object,
        usage_limits: object,
    ) -> _FakeAgentRunWithMutableUsage:
        _ = (prompt, deps, message_history, usage_limits)
        return _FakeAgentRunWithMutableUsage()


class _PartEventStream:
    def __init__(
        self,
        events: list[object],
        usage_snapshot: SimpleNamespace,
    ) -> None:
        self._events = list(events)
        self._usage_snapshot = usage_snapshot
        self._index = 0

    def __aiter__(self) -> _PartEventStream:
        return self

    async def __anext__(self) -> object:
        if self._index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._index]
        self._index += 1
        return event

    def usage(self) -> SimpleNamespace:
        return self._usage_snapshot


class _PartEventStreamContext:
    def __init__(
        self,
        events: list[object],
        usage_snapshot: SimpleNamespace,
    ) -> None:
        self._events = events
        self._usage_snapshot = usage_snapshot

    async def __aenter__(self) -> _PartEventStream:
        return _PartEventStream(self._events, self._usage_snapshot)

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        _ = (exc_type, exc, tb)
        return False


class _PartEventNode:
    def __init__(
        self,
        events: list[object],
        usage_snapshot: SimpleNamespace,
    ) -> None:
        self._events = events
        self._usage_snapshot = usage_snapshot

    def stream(self, ctx: object) -> _PartEventStreamContext:
        _ = ctx
        return _PartEventStreamContext(self._events, self._usage_snapshot)


def _build_provider(
    db_path: Path,
    hub: _FakeRunEventHub,
    *,
    allowed_tools: tuple[str, ...] = (),
    allowed_mcp_servers: tuple[str, ...] = (),
    allowed_skills: tuple[str, ...] = (),
    skill_registry: object | None = None,
    mcp_registry: object | None = None,
    run_control_manager: object | None = None,
    subagent_reflection_service: object | None = None,
    task_execution_service: object | None = None,
) -> tuple[OpenAICompatibleProvider, MessageRepository]:
    registry = (
        cast(SkillRegistry, skill_registry)
        if skill_registry is not None
        else cast(SkillRegistry, object())
    )
    resolved_mcp_registry = (
        cast(McpRegistry, mcp_registry)
        if mcp_registry is not None
        else cast(McpRegistry, object())
    )
    shared_store = SharedStateRepository(db_path)
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
    role_registry.register(
        RoleDefinition(
            role_id="time",
            name="time",
            description="Reports the current time.",
            version="1",
            tools=(),
            system_prompt="Tell time.",
        )
    )
    config = ModelEndpointConfig(
        model="gpt-test",
        base_url="http://localhost",
        api_key="test-key",
    )
    message_repo = MessageRepository(db_path)
    session_history_marker_repo = SessionHistoryMarkerRepository(db_path)
    provider = OpenAICompatibleProvider(
        config,
        profile_name=None,
        task_repo=TaskRepository(db_path),
        shared_store=shared_store,
        event_bus=EventLog(db_path),
        injection_manager=cast(
            RunInjectionManager, cast(object, _FakeInjectionManager())
        ),
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        agent_repo=AgentInstanceRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        run_intent_repo=RunIntentRepository(db_path),
        background_task_service=None,
        workspace_manager=WorkspaceManager(
            project_root=Path("."), shared_store=shared_store
        ),
        media_asset_service=cast(MediaAssetService, object()),
        role_memory_service=cast(RoleMemoryService | None, None),
        subagent_reflection_service=cast(
            SubagentReflectionService | None,
            subagent_reflection_service,
        ),
        tool_registry=cast(ToolRegistry, object()),
        mcp_registry=resolved_mcp_registry,
        skill_registry=registry,
        allowed_tools=allowed_tools,
        allowed_mcp_servers=allowed_mcp_servers,
        allowed_skills=allowed_skills,
        message_repo=message_repo,
        session_history_marker_repo=session_history_marker_repo,
        role_registry=role_registry,
        task_execution_service=cast(
            TaskExecutionService,
            cast(object, task_execution_service or object()),
        ),
        task_service=cast(TaskOrchestrationService, object()),
        run_control_manager=cast(
            RunControlManager,
            cast(object, run_control_manager or _FakeRunControlManager()),
        ),
        tool_approval_manager=cast(ToolApprovalManager, object()),
        tool_approval_policy=ToolApprovalPolicy(),
    )
    return provider, message_repo


@pytest.mark.asyncio
async def test_maybe_compact_history_returns_history_when_plan_does_not_trigger(
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, _ = _build_provider(
        tmp_path / "temporary_role_compaction.db",
        fake_hub,
    )

    history: list[ModelRequest | ModelResponse] = [
        ModelRequest(parts=[UserPromptPart(content="inspect temp role")])
    ]
    request = LLMRequest(
        run_id="run-temp-role",
        trace_id="run-temp-role",
        task_id="task-temp-role",
        session_id="session-temp-role",
        workspace_id="default",
        conversation_id="conv-temp-role",
        instance_id="inst-temp-role",
        role_id="time",
        system_prompt="Inspect runtime role behavior.",
        user_prompt="inspect temp role",
    )

    compacted = await provider._session._maybe_compact_history(
        request=request,
        history=history,
        conversation_id="conv-temp-role",
        budget=build_conversation_compaction_budget(
            context_window=32000,
            estimated_system_prompt_tokens=0,
            estimated_user_prompt_tokens=0,
            estimated_tool_context_tokens=0,
            estimated_output_reserve_tokens=0,
        ),
    )

    assert compacted == history


def _seed_request(
    message_repo: MessageRepository,
    *,
    session_id: str,
    instance_id: str,
    task_id: str,
    trace_id: str,
    content: str,
    role_id: str,
) -> None:
    message_repo.append(
        session_id=session_id,
        workspace_id="default",
        conversation_id=build_conversation_id(session_id, role_id),
        agent_role_id=role_id,
        instance_id=instance_id,
        task_id=task_id,
        trace_id=trace_id,
        messages=[ModelRequest(parts=[UserPromptPart(content=content)])],
    )


@pytest.mark.asyncio
async def test_generate_counts_current_user_prompt_in_context_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    fake_hub = _FakeRunEventHub()
    provider, _message_repo = _build_provider(
        tmp_path / "current_prompt_budget.db",
        fake_hub,
    )
    updated_config = provider._config.model_copy(
        update={
            "context_window": 128_000,
            "sampling": SamplingConfig(
                temperature=provider._config.sampling.temperature,
                top_p=provider._config.sampling.top_p,
                max_tokens=100_000,
                top_k=provider._config.sampling.top_k,
            ),
        }
    )
    provider._config_ref = updated_config
    provider._session._config = updated_config
    captured_kwargs: dict[str, object] = {}

    def _fake_builder(**kwargs: object) -> _FakeAgent:
        captured_kwargs.update(kwargs)
        return fake_agent

    monkeypatch.setattr(llm_module, "build_coordination_agent", _fake_builder)

    request = LLMRequest(
        run_id="run-current-prompt-budget",
        trace_id="run-current-prompt-budget",
        task_id="task-current-prompt-budget",
        session_id="session-current-prompt-budget",
        workspace_id="default",
        instance_id="inst-current-prompt-budget",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="x" * 124_200,
    )

    _ = await provider.generate(request)

    settings_obj = captured_kwargs.get("model_settings")
    assert isinstance(settings_obj, dict)
    bounded_max_tokens = settings_obj.get("max_tokens")
    assert isinstance(bounded_max_tokens, int)
    configured_max_tokens = provider._config.sampling.max_tokens
    assert isinstance(configured_max_tokens, int)
    assert 1 <= bounded_max_tokens < configured_max_tokens


@pytest.mark.asyncio
async def test_safe_max_output_tokens_does_not_double_count_persisted_user_prompt(
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, _message_repo = _build_provider(
        tmp_path / "persisted_prompt_budget.db",
        fake_hub,
    )
    updated_config = provider._config.model_copy(
        update={
            "context_window": 128_000,
            "sampling": SamplingConfig(
                temperature=provider._config.sampling.temperature,
                top_p=provider._config.sampling.top_p,
                max_tokens=100_000,
                top_k=provider._config.sampling.top_k,
            ),
        }
    )
    provider._config_ref = updated_config
    provider._session._config = updated_config

    request = LLMRequest(
        run_id="run-persisted-prompt-budget",
        trace_id="run-persisted-prompt-budget",
        task_id="task-persisted-prompt-budget",
        session_id="session-persisted-prompt-budget",
        workspace_id="default",
        instance_id="inst-persisted-prompt-budget",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="x" * 124_200,
    )
    history = [
        ModelRequest(parts=[UserPromptPart(content="x" * 124_200)]),
    ]

    persisted_budget = await provider._session._safe_max_output_tokens(
        request=request,
        history=history,
        system_prompt="system",
        reserve_user_prompt_tokens=False,
        allowed_tools=(),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )
    deduped_request = request.model_copy(update={"user_prompt": None})
    deduped_budget = await provider._session._safe_max_output_tokens(
        request=deduped_request,
        history=history,
        system_prompt="system",
        reserve_user_prompt_tokens=False,
        allowed_tools=(),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )

    assert persisted_budget == deduped_budget


@pytest.mark.asyncio
async def test_generate_recomputes_budget_after_injection_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, _message_repo = _build_provider(
        tmp_path / "injection_budget_restart.db",
        fake_hub,
    )
    updated_config = provider._config.model_copy(
        update={
            "context_window": 128_000,
            "sampling": SamplingConfig(
                temperature=provider._config.sampling.temperature,
                top_p=provider._config.sampling.top_p,
                max_tokens=100_000,
                top_k=provider._config.sampling.top_k,
            ),
        }
    )
    provider._config_ref = updated_config
    provider._session._config = updated_config

    class _InjectedMessage:
        def __init__(self, content: str) -> None:
            self.content = content

        def model_dump_json(self) -> str:
            return json.dumps({"content": self.content})

    class _OneShotInjectionManager:
        def __init__(self) -> None:
            self._drained = False

        def drain_at_boundary(self, run_id: str, instance_id: str) -> list[object]:
            _ = (run_id, instance_id)
            if self._drained:
                return []
            self._drained = True
            return [_InjectedMessage("y" * 124_200)]

    provider._session._injection_manager = cast(
        RunInjectionManager,
        cast(object, _OneShotInjectionManager()),
    )

    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[
                    _FakeModelRequestNode(
                        SimpleNamespace(
                            input_tokens=0,
                            output_tokens=0,
                            total_tokens=0,
                            requests=1,
                            tool_calls=0,
                        )
                    )
                ],
                messages_by_step=[[]],
                result=_ScriptedResult(response=ModelResponse(parts=[]), messages=[]),
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="ok")]),
                    messages=[ModelResponse(parts=[TextPart(content="ok")])],
                ),
            ),
        ]
    )

    monkeypatch.setattr(llm_module, "ModelRequestNode", _StreamingTextNode)
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )

    request = LLMRequest(
        run_id="run-injection-budget-restart",
        trace_id="run-injection-budget-restart",
        task_id="task-injection-budget-restart",
        session_id="session-injection-budget-restart",
        workspace_id="default",
        instance_id="inst-injection-budget-restart",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="start",
    )

    _ = await provider.generate(request)

    assert len(scripted_agent.histories) >= 2
    first_budget = await provider._session._safe_max_output_tokens(
        request=request,
        history=cast(list[ModelRequest | ModelResponse], scripted_agent.histories[0]),
        system_prompt="system",
        reserve_user_prompt_tokens=True,
        allowed_tools=(),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )
    second_budget = await provider._session._safe_max_output_tokens(
        request=request,
        history=cast(list[ModelRequest | ModelResponse], scripted_agent.histories[-1]),
        system_prompt="system",
        reserve_user_prompt_tokens=False,
        allowed_tools=(),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )
    assert isinstance(first_budget, int)
    assert isinstance(second_budget, int)
    assert second_budget < first_budget


@pytest.mark.asyncio
async def test_generate_defers_injection_restart_until_pending_tool_call_completes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(
        tmp_path / "pending_tool_call_injection_restart.db",
        fake_hub,
    )

    class _InjectedMessage:
        def __init__(self, content: str) -> None:
            self.content = content

        def model_dump_json(self) -> str:
            return json.dumps({"content": self.content})

    class _DeferredInjectionManager:
        def __init__(self) -> None:
            self.calls = 0

        def drain_at_boundary(self, run_id: str, instance_id: str) -> list[object]:
            _ = (run_id, instance_id)
            self.calls += 1
            if self.calls == 1:
                return [_InjectedMessage("background follow-up")]
            return []

    injection_manager = _DeferredInjectionManager()
    provider._session._injection_manager = cast(
        RunInjectionManager,
        cast(object, injection_manager),
    )

    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[
                    _FakeModelRequestNode(
                        SimpleNamespace(
                            input_tokens=0,
                            output_tokens=0,
                            total_tokens=0,
                            requests=1,
                            tool_calls=1,
                        )
                    ),
                    object(),
                ],
                messages_by_step=[
                    [
                        ModelResponse(
                            parts=[
                                ToolCallPart(
                                    tool_name="wait_background_task",
                                    args={"background_task_id": "bg-1"},
                                    tool_call_id="call-wait",
                                )
                            ]
                        )
                    ],
                    [
                        ModelRequest(
                            parts=[
                                ToolReturnPart(
                                    tool_name="wait_background_task",
                                    tool_call_id="call-wait",
                                    content={
                                        "ok": True,
                                        "data": {
                                            "background_task_id": "bg-1",
                                            "status": "completed",
                                            "completed": True,
                                            "output": "ok",
                                        },
                                        "error": None,
                                        "meta": {},
                                    },
                                )
                            ]
                        )
                    ],
                ],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[]),
                    messages=[],
                ),
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="done")]),
                    messages=[ModelResponse(parts=[TextPart(content="done")])],
                ),
            ),
        ]
    )

    monkeypatch.setattr(llm_module, "ModelRequestNode", _FakeModelRequestNode)
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )

    request = LLMRequest(
        run_id="run-pending-tool-injection",
        trace_id="run-pending-tool-injection",
        task_id="task-pending-tool-injection",
        session_id="session-pending-tool-injection",
        workspace_id="default",
        conversation_id=build_conversation_id(
            "session-pending-tool-injection",
            "Coordinator",
        ),
        instance_id="inst-pending-tool-injection",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="wait for the background task",
    )

    response = await provider.generate(request)

    assert response == "done"
    assert injection_manager.calls == 1
    assert len(scripted_agent.histories) == 2

    second_history = cast(
        list[ModelRequest | ModelResponse],
        scripted_agent.histories[1],
    )
    tool_call_index = next(
        index
        for index, message in enumerate(second_history)
        if isinstance(message, ModelResponse)
        and any(
            isinstance(part, ToolCallPart)
            and part.tool_name == "wait_background_task"
            and part.tool_call_id == "call-wait"
            for part in message.parts
        )
    )
    tool_return_index = next(
        index
        for index, message in enumerate(second_history)
        if isinstance(message, ModelRequest)
        and any(
            isinstance(part, ToolReturnPart)
            and part.tool_name == "wait_background_task"
            and part.tool_call_id == "call-wait"
            for part in message.parts
        )
    )
    injection_index = next(
        index
        for index, message in enumerate(second_history)
        if isinstance(message, ModelRequest)
        and any(
            isinstance(part, UserPromptPart) and part.content == "background follow-up"
            for part in message.parts
        )
    )
    assert tool_call_index < tool_return_index < injection_index

    stored_history = message_repo.get_history_for_conversation(
        request.conversation_id or ""
    )
    assert (
        sum(
            1
            for message in stored_history
            if isinstance(message, ModelRequest)
            and any(
                isinstance(part, UserPromptPart)
                and part.content == "background follow-up"
                for part in message.parts
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_generate_recovery_does_not_rereserve_original_user_prompt_tokens(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, _message_repo = _build_provider(
        tmp_path / "recovery_prompt_budget.db",
        fake_hub,
    )
    updated_config = provider._config.model_copy(
        update={
            "context_window": 128_000,
            "sampling": SamplingConfig(
                temperature=provider._config.sampling.temperature,
                top_p=provider._config.sampling.top_p,
                max_tokens=100_000,
                top_k=provider._config.sampling.top_k,
            ),
        }
    )
    provider._config_ref = updated_config
    provider._session._config = updated_config

    usage_after_request = SimpleNamespace(
        input_tokens=0,
        cache_read_tokens=0,
        output_tokens=0,
        total_tokens=0,
        requests=1,
        tool_calls=0,
        details={"reasoning_tokens": 0},
    )
    part_events = [
        PartStartEvent(
            index=0,
            part=ToolCallPart(
                tool_name="write",
                args='{"content":"broken"',
                tool_call_id="call-live-recovery-budget",
            ),
        ),
        PartDeltaEvent(
            index=0,
            delta=ToolCallPartDelta(args_delta=', path:"demo.html"}'),
        ),
    ]
    request_error = APIStatusError(
        "bad request",
        response=httpx.Response(
            400,
            request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
        ),
        body={
            "error": {
                "message": (
                    "litellm.BadRequestError: OpenAIException - "
                    "Expecting property name enclosed in double quotes: "
                    "line 1 column 2 (char 1)"
                )
            }
        },
    )
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[cast(object, _PartEventNode(part_events, usage_after_request))],
                messages_by_step=[[]],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=request_error,
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="recovered done")]),
                    messages=[
                        ModelResponse(parts=[TextPart(content="recovered done")])
                    ],
                ),
            ),
        ]
    )
    captured_model_settings: list[dict[str, object]] = []

    monkeypatch.setattr(llm_module, "ModelRequestNode", _PartEventNode)

    def _fake_builder(**kwargs: object) -> _SequentialAgent:
        model_settings = kwargs.get("model_settings")
        assert isinstance(model_settings, dict)
        captured_model_settings.append(dict(model_settings))
        return scripted_agent

    monkeypatch.setattr(llm_module, "build_coordination_agent", _fake_builder)

    request = LLMRequest(
        run_id="run-recovery-prompt-budget",
        trace_id="run-recovery-prompt-budget",
        task_id="task-recovery-prompt-budget",
        session_id="session-recovery-prompt-budget",
        workspace_id="default",
        instance_id="inst-recovery-prompt-budget",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="x" * 124_200,
    )

    result = await provider.generate(request)

    assert result == "recovered done"
    assert len(captured_model_settings) == 2
    second_history = cast(
        list[ModelRequest | ModelResponse],
        scripted_agent.histories[1],
    )
    assert any(
        isinstance(message, ModelRequest)
        and provider._session._extract_user_prompt_text(message) == request.user_prompt
        for message in second_history
    )
    assert not provider._session._history_ends_with_user_prompt(
        second_history,
        cast(str, request.user_prompt),
    )
    expected_without_rereserve = await provider._session._safe_max_output_tokens(
        request=request,
        history=second_history,
        system_prompt="system",
        reserve_user_prompt_tokens=False,
        allowed_tools=(),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )
    expected_with_rereserve = await provider._session._safe_max_output_tokens(
        request=request,
        history=second_history,
        system_prompt="system",
        reserve_user_prompt_tokens=True,
        allowed_tools=(),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )

    assert isinstance(expected_without_rereserve, int)
    assert isinstance(expected_with_rereserve, int)
    assert captured_model_settings[1]["max_tokens"] == expected_without_rereserve
    assert expected_without_rereserve > expected_with_rereserve


@pytest.mark.asyncio
async def test_generate_rebuilds_agent_when_restart_updates_compaction_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, _message_repo = _build_provider(
        tmp_path / "restart_compaction_prompt.db",
        fake_hub,
    )

    class _InjectedMessage:
        def __init__(self, content: str) -> None:
            self.content = content

        def model_dump_json(self) -> str:
            return json.dumps({"content": self.content})

    class _OneShotInjectionManager:
        def __init__(self) -> None:
            self._drained = False

        def drain_at_boundary(self, run_id: str, instance_id: str) -> list[object]:
            _ = (run_id, instance_id)
            if self._drained:
                return []
            self._drained = True
            return [_InjectedMessage("restart with compaction summary")]

    provider._session._injection_manager = cast(
        RunInjectionManager,
        cast(object, _OneShotInjectionManager()),
    )
    provider._session._conversation_compaction_service = cast(
        ConversationCompactionService,
        _FakeConversationCompactionService(
            ["", "## Compacted Conversation Summary\nsummary after restart"]
        ),
    )

    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[
                    _FakeModelRequestNode(
                        SimpleNamespace(
                            input_tokens=0,
                            output_tokens=0,
                            total_tokens=0,
                            requests=1,
                            tool_calls=0,
                        )
                    )
                ],
                messages_by_step=[[]],
                result=_ScriptedResult(response=ModelResponse(parts=[]), messages=[]),
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="ok")]),
                    messages=[ModelResponse(parts=[TextPart(content="ok")])],
                ),
            ),
        ]
    )
    captured_system_prompts: list[str] = []

    monkeypatch.setattr(llm_module, "ModelRequestNode", _FakeModelRequestNode)

    def _fake_builder(**kwargs: object) -> _SequentialAgent:
        system_prompt = kwargs.get("system_prompt")
        assert isinstance(system_prompt, str)
        captured_system_prompts.append(system_prompt)
        return scripted_agent

    monkeypatch.setattr(llm_module, "build_coordination_agent", _fake_builder)

    request = LLMRequest(
        run_id="run-restart-compaction-prompt",
        trace_id="run-restart-compaction-prompt",
        task_id="task-restart-compaction-prompt",
        session_id="session-restart-compaction-prompt",
        workspace_id="default",
        instance_id="inst-restart-compaction-prompt",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="start",
    )

    response = await provider.generate(request)

    assert response == "ok"
    assert len(captured_system_prompts) == 2
    assert captured_system_prompts[0].startswith("system")
    assert captured_system_prompts[0].endswith("summary after restart")
    assert captured_system_prompts[1].endswith("summary after restart")


@pytest.mark.asyncio
async def test_generate_reserves_context_for_registered_tools_and_skills(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    fake_hub = _FakeRunEventHub()
    provider, _message_repo = _build_provider(
        tmp_path / "tool_budget.db",
        fake_hub,
        allowed_tools=("dispatch_task",),
        allowed_skills=("time",),
    )
    updated_config = provider._config.model_copy(
        update={
            "context_window": 100_300,
            "sampling": SamplingConfig(
                temperature=provider._config.sampling.temperature,
                top_p=provider._config.sampling.top_p,
                max_tokens=100_000,
                top_k=provider._config.sampling.top_k,
            ),
        }
    )
    provider._config_ref = updated_config
    provider._session._config = updated_config
    captured_kwargs: dict[str, object] = {}

    def _fake_builder(**kwargs: object) -> _FakeAgent:
        captured_kwargs.update(kwargs)
        return fake_agent

    monkeypatch.setattr(llm_module, "build_coordination_agent", _fake_builder)

    request = LLMRequest(
        run_id="run-tool-budget",
        trace_id="run-tool-budget",
        task_id="task-tool-budget",
        session_id="session-tool-budget",
        workspace_id="default",
        instance_id="inst-tool-budget",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="current turn",
    )

    _ = await provider.generate(request)

    settings_obj = captured_kwargs.get("model_settings")
    assert isinstance(settings_obj, dict)
    bounded_max_tokens = settings_obj.get("max_tokens")
    assert isinstance(bounded_max_tokens, int)
    capped_with_tools = await provider._session._safe_max_output_tokens(
        request=request,
        history=[],
        system_prompt="system",
        reserve_user_prompt_tokens=True,
        allowed_tools=("dispatch_task",),
        allowed_mcp_servers=(),
        allowed_skills=("time",),
    )
    uncapped_without_tools = await provider._session._safe_max_output_tokens(
        request=request,
        history=[],
        system_prompt="system",
        reserve_user_prompt_tokens=True,
        allowed_tools=(),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )
    assert isinstance(capped_with_tools, int)
    assert isinstance(uncapped_without_tools, int)
    assert capped_with_tools < uncapped_without_tools
    assert bounded_max_tokens == capped_with_tools


@pytest.mark.asyncio
async def test_generate_scales_mcp_context_budget_with_actual_toolset_size(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    fake_registry = _FakeMcpSchemaRegistry(
        {
            "small": (
                McpToolSchema(
                    name="small_read",
                    description="Read a file.",
                    input_schema={
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                ),
            ),
            "large": tuple(
                McpToolSchema(
                    name=f"large_tool_{index}",
                    description="Tool with a larger schema payload.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            f"field_{field_index}": {
                                "type": "string",
                                "description": "x" * 400,
                            }
                            for field_index in range(8)
                        },
                    },
                )
                for index in range(10)
            ),
        }
    )
    provider_small, _ = _build_provider(
        tmp_path / "mcp_budget_small.db",
        fake_hub,
        allowed_mcp_servers=("small",),
        mcp_registry=fake_registry,
    )
    provider_large, _ = _build_provider(
        tmp_path / "mcp_budget_large.db",
        fake_hub,
        allowed_mcp_servers=("large",),
        mcp_registry=fake_registry,
    )
    for provider in (provider_small, provider_large):
        updated_config = provider._config.model_copy(
            update={
                "context_window": 100_300,
                "sampling": SamplingConfig(
                    temperature=provider._config.sampling.temperature,
                    top_p=provider._config.sampling.top_p,
                    max_tokens=100_000,
                    top_k=provider._config.sampling.top_k,
                ),
            }
        )
        provider._config_ref = updated_config
        provider._session._config = updated_config

    captured_settings: list[dict[str, object]] = []

    def _fake_builder(**kwargs: object) -> _FakeAgent:
        model_settings = kwargs.get("model_settings")
        assert isinstance(model_settings, dict)
        captured_settings.append(dict(model_settings))
        return _FakeAgent()

    monkeypatch.setattr(llm_module, "build_coordination_agent", _fake_builder)

    await provider_small.generate(
        LLMRequest(
            run_id="run-mcp-budget-small",
            trace_id="run-mcp-budget-small",
            task_id="task-mcp-budget-small",
            session_id="session-mcp-budget-small",
            workspace_id="default",
            instance_id="inst-mcp-budget-small",
            role_id="Coordinator",
            system_prompt="system",
            user_prompt="current turn",
        )
    )
    await provider_large.generate(
        LLMRequest(
            run_id="run-mcp-budget-large",
            trace_id="run-mcp-budget-large",
            task_id="task-mcp-budget-large",
            session_id="session-mcp-budget-large",
            workspace_id="default",
            instance_id="inst-mcp-budget-large",
            role_id="Coordinator",
            system_prompt="system",
            user_prompt="current turn",
        )
    )

    assert len(captured_settings) == 2
    small_budget = captured_settings[0]["max_tokens"]
    large_budget = captured_settings[1]["max_tokens"]
    assert isinstance(small_budget, int)
    assert isinstance(large_budget, int)
    assert large_budget < small_budget


@pytest.mark.asyncio
async def test_generate_skips_mcp_schema_probe_when_context_window_unset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    fake_registry = _FakeMcpSchemaRegistry(
        {
            "slow": (
                McpToolSchema(
                    name="slow_tool",
                    description="Would be expensive to probe.",
                    input_schema={"type": "object"},
                ),
            )
        },
        fail_on_list=True,
    )
    provider, _ = _build_provider(
        tmp_path / "mcp_budget_unset_context.db",
        fake_hub,
        allowed_mcp_servers=("slow",),
        mcp_registry=fake_registry,
    )
    updated_config = provider._config.model_copy(
        update={
            "context_window": None,
            "sampling": SamplingConfig(
                temperature=provider._config.sampling.temperature,
                top_p=provider._config.sampling.top_p,
                max_tokens=100_000,
                top_k=provider._config.sampling.top_k,
            ),
        }
    )
    provider._config_ref = updated_config
    provider._session._config = updated_config

    monkeypatch.setattr(
        llm_module, "build_coordination_agent", lambda **_: _FakeAgent()
    )

    result = await provider.generate(
        LLMRequest(
            run_id="run-mcp-budget-unset-context",
            trace_id="run-mcp-budget-unset-context",
            task_id="task-mcp-budget-unset-context",
            session_id="session-mcp-budget-unset-context",
            workspace_id="default",
            instance_id="inst-mcp-budget-unset-context",
            role_id="Coordinator",
            system_prompt="system",
            user_prompt="current turn",
        )
    )

    assert result == "ok"
    assert fake_registry.list_calls == 0


@pytest.mark.asyncio
async def test_generate_passes_reasoning_effort_when_thinking_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    fake_hub = _FakeRunEventHub()
    provider, _ = _build_provider(tmp_path / "thinking_settings.db", fake_hub)
    captured_kwargs: dict[str, object] = {}

    def _fake_builder(**kwargs: object) -> _FakeAgent:
        captured_kwargs.update(kwargs)
        return fake_agent

    monkeypatch.setattr(llm_module, "build_coordination_agent", _fake_builder)

    request = LLMRequest(
        run_id="run-thinking-settings",
        trace_id="run-thinking-settings",
        task_id="task-thinking-settings",
        session_id="session-thinking-settings",
        workspace_id="default",
        instance_id="inst-thinking-settings",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="current turn",
        thinking=RunThinkingConfig(enabled=True, effort="high"),
    )

    _ = await provider.generate(request)

    settings_obj = captured_kwargs.get("model_settings")
    assert isinstance(settings_obj, dict)
    assert settings_obj.get("openai_reasoning_effort") == "high"


@pytest.mark.asyncio
async def test_generate_omits_max_tokens_when_config_unset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    fake_hub = _FakeRunEventHub()
    provider, _message_repo = _build_provider(
        tmp_path / "unset_max_tokens.db", fake_hub
    )
    captured_kwargs: dict[str, object] = {}

    def _fake_builder(**kwargs: object) -> _FakeAgent:
        captured_kwargs.update(kwargs)
        return fake_agent

    monkeypatch.setattr(llm_module, "build_coordination_agent", _fake_builder)

    request = LLMRequest(
        run_id="run-unset-max-tokens",
        trace_id="run-unset-max-tokens",
        task_id="task-unset-max-tokens",
        session_id="session-unset-max-tokens",
        workspace_id="default",
        instance_id="inst-unset-max-tokens",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="current turn",
    )

    _ = await provider.generate(request)

    settings_obj = captured_kwargs.get("model_settings")
    assert isinstance(settings_obj, dict)
    assert "max_tokens" not in settings_obj
    assert (
        await provider._session._safe_max_output_tokens(
            request=request,
            history=[],
            system_prompt="system",
            reserve_user_prompt_tokens=True,
            allowed_tools=(),
            allowed_mcp_servers=(),
            allowed_skills=(),
        )
        is None
    )


@pytest.mark.asyncio
async def test_generate_uses_prepared_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    fake_hub = _FakeRunEventHub()
    fake_skill_registry = _FakeSkillRegistry(
        (
            PromptSkillInstruction(
                name="time",
                description="Normalize all times to UTC.",
            ),
        )
    )
    provider, _ = _build_provider(
        tmp_path / "prompt_aug.db",
        fake_hub,
        allowed_tools=("dispatch_task",),
        allowed_skills=("time",),
        skill_registry=fake_skill_registry,
    )
    captured_kwargs: dict[str, object] = {}
    captured_events: list[dict[str, object]] = []

    def _fake_builder(**kwargs: object) -> _FakeAgent:
        captured_kwargs.update(kwargs)
        return fake_agent

    def _fake_log_event(*args: object, **kwargs: object) -> None:
        _ = args
        captured_events.append(dict(kwargs))

    monkeypatch.setattr(llm_module, "build_coordination_agent", _fake_builder)
    monkeypatch.setattr(llm_module, "log_event", _fake_log_event)

    request = LLMRequest(
        run_id="run-augment",
        trace_id="run-augment",
        task_id="task-augment",
        session_id="session-augment",
        workspace_id="default",
        instance_id="inst-augment",
        role_id="Coordinator",
        system_prompt=(
            "## Role\nBase system prompt.\n\n"
            "## Available Skills\n"
            "- time: Normalize all times to UTC.\n\n"
            "## Runtime Environment Information\n"
            "- Working Directory: /tmp/project"
        ),
        user_prompt="current turn",
    )

    _ = await provider.generate(request)

    system_prompt_obj = captured_kwargs.get("system_prompt")
    assert isinstance(system_prompt_obj, str)
    assert system_prompt_obj == request.system_prompt
    assert fake_skill_registry.requested == []
    prepared_events = [
        event
        for event in captured_events
        if event.get("event") == "llm.system_prompt.prepared"
    ]
    assert len(prepared_events) == 1
    assert "## Role\nBase system prompt." in str(prepared_events[0].get("message", ""))
    assert prepared_events[0].get("payload") == {
        "role_id": "Coordinator",
        "instance_id": "inst-augment",
        "task_id": "task-augment",
        "length": len(system_prompt_obj),
    }


@pytest.mark.asyncio
async def test_generate_does_not_persist_duplicate_leading_user_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(tmp_path / "dedupe_prompt.db", fake_hub)
    _seed_request(
        message_repo,
        session_id="session-dedupe",
        instance_id="inst-dedupe",
        task_id="task-dedupe",
        trace_id="run-dedupe",
        content="dedupe request",
        role_id="Coordinator",
    )
    duplicated_request = ModelRequest(parts=[UserPromptPart(content="dedupe request")])
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="ok")]),
                    messages=[
                        duplicated_request,
                        ModelResponse(parts=[TextPart(content="ok")]),
                    ],
                ),
            )
        ]
    )

    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )

    request = LLMRequest(
        run_id="run-dedupe",
        trace_id="run-dedupe",
        task_id="task-dedupe",
        session_id="session-dedupe",
        workspace_id="default",
        instance_id="inst-dedupe",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt=None,
    )

    result = await provider.generate(request)

    assert result == "ok"
    history = message_repo.get_history("inst-dedupe")
    user_prompts = [
        message
        for message in history
        if isinstance(message, ModelRequest)
        and all(isinstance(part, UserPromptPart) for part in message.parts)
    ]
    assert len(user_prompts) == 1


@pytest.mark.asyncio
async def test_generate_does_not_persist_duplicate_response_after_dropping_leading_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(tmp_path / "dedupe_response.db", fake_hub)
    _seed_request(
        message_repo,
        session_id="session-dedupe",
        instance_id="inst-dedupe",
        task_id="task-dedupe",
        trace_id="run-dedupe",
        content="hello",
        role_id="Coordinator",
    )
    duplicated_request = ModelRequest(parts=[UserPromptPart(content="hello")])
    final_response = ModelResponse(parts=[TextPart(content="ok")])
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[
                    _FakeModelRequestNode(
                        SimpleNamespace(
                            input_tokens=0,
                            output_tokens=0,
                            total_tokens=0,
                            requests=1,
                            tool_calls=0,
                        )
                    )
                ],
                messages_by_step=[[duplicated_request, final_response]],
                result=_ScriptedResult(
                    response=final_response,
                    messages=[duplicated_request, final_response],
                ),
            )
        ]
    )

    monkeypatch.setattr(llm_module, "ModelRequestNode", _FakeModelRequestNode)
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )

    request = LLMRequest(
        run_id="run-dedupe",
        trace_id="run-dedupe",
        task_id="task-dedupe",
        session_id="session-dedupe",
        workspace_id="default",
        instance_id="inst-dedupe",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt=None,
    )

    result = await provider.generate(request)

    assert result == "ok"
    history = message_repo.get_history("inst-dedupe")
    responses = [message for message in history if isinstance(message, ModelResponse)]
    assert len(responses) == 1


@pytest.mark.asyncio
async def test_generate_token_usage_tracks_request_level_delta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, _ = _build_provider(tmp_path / "token_usage.db", fake_hub)
    usage_after_request = SimpleNamespace(
        input_tokens=130,
        cache_read_tokens=21,
        output_tokens=19,
        total_tokens=149,
        requests=1,
        tool_calls=0,
        details={"reasoning_tokens": 6},
    )
    fake_node = _FakeModelRequestNode(usage_after_request)
    fake_agent = _FakeAgentWithNode(fake_node)

    monkeypatch.setattr(llm_module, "ModelRequestNode", _FakeModelRequestNode)
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: fake_agent,
    )

    request = LLMRequest(
        run_id="run-4",
        trace_id="run-4",
        task_id="task-4",
        session_id="session-4",
        workspace_id="default",
        instance_id="inst-4",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="current turn",
    )

    _ = await provider.generate(request)

    token_events = [
        e
        for e in fake_hub.events
        if getattr(getattr(e, "event_type", None), "value", "") == "token_usage"
    ]
    assert len(token_events) == 1
    payload = json.loads(token_events[0].payload_json)
    assert payload["input_tokens"] == 30
    assert payload["cached_input_tokens"] == 21
    assert payload["output_tokens"] == 9
    assert payload["reasoning_output_tokens"] == 6
    assert payload["total_tokens"] == 39
    assert payload["requests"] == 1
    assert payload["tool_calls"] == 5


@pytest.mark.asyncio
async def test_generate_token_usage_delta_works_with_mutated_usage_object(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, _ = _build_provider(tmp_path / "token_usage_mut.db", fake_hub)
    fake_agent = _FakeAgentWithMutableUsageNode()

    monkeypatch.setattr(
        llm_module, "ModelRequestNode", _FakeModelRequestNodeMutatesUsage
    )
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: fake_agent,
    )

    request = LLMRequest(
        run_id="run-5",
        trace_id="run-5",
        task_id="task-5",
        session_id="session-5",
        workspace_id="default",
        instance_id="inst-5",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="current turn",
    )

    _ = await provider.generate(request)

    token_events = [
        e
        for e in fake_hub.events
        if getattr(getattr(e, "event_type", None), "value", "") == "token_usage"
    ]
    assert len(token_events) == 1
    payload = json.loads(token_events[0].payload_json)
    assert payload["input_tokens"] == 30
    assert payload["cached_input_tokens"] == 13
    assert payload["output_tokens"] == 9
    assert payload["reasoning_output_tokens"] == 5
    assert payload["total_tokens"] == 39
    assert payload["requests"] == 1
    assert payload["tool_calls"] == 5


@pytest.mark.asyncio
async def test_generate_streams_thinking_events_and_excludes_thinking_from_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(tmp_path / "thinking_stream.db", fake_hub)
    final_response = ModelResponse(
        parts=[
            ThinkingPart(content="draft trace"),
            TextPart(content="answer done"),
        ]
    )
    usage_after_request = SimpleNamespace(
        input_tokens=0,
        cache_read_tokens=0,
        output_tokens=0,
        total_tokens=0,
        requests=1,
        tool_calls=0,
        details={"reasoning_tokens": 0},
    )
    part_events = [
        PartStartEvent(index=0, part=ThinkingPart(content="draft ")),
        PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta="trace")),
        PartEndEvent(index=0, part=ThinkingPart(content="draft trace")),
        PartStartEvent(index=1, part=TextPart(content="answer ")),
        PartDeltaEvent(index=1, delta=TextPartDelta(content_delta="done")),
        PartEndEvent(index=1, part=TextPart(content="answer done")),
    ]
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[_PartEventNode(part_events, usage_after_request)],
                messages_by_step=[[final_response]],
                result=_ScriptedResult(
                    response=final_response,
                    messages=[final_response],
                ),
            )
        ]
    )

    monkeypatch.setattr(llm_module, "ModelRequestNode", _PartEventNode)
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )

    request = LLMRequest(
        run_id="run-thinking-stream",
        trace_id="run-thinking-stream",
        task_id="task-thinking-stream",
        session_id="session-thinking-stream",
        workspace_id="default",
        instance_id="inst-thinking-stream",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="show work",
        thinking=RunThinkingConfig(enabled=True, effort="medium"),
    )

    result = await provider.generate(request)

    assert result == "answer done"
    history = message_repo.get_history("inst-thinking-stream")
    assert isinstance(history[-1], ModelResponse)
    assert isinstance(history[-1].parts[0], ThinkingPart)
    event_types = [event.event_type for event in fake_hub.events]
    assert RunEventType.THINKING_STARTED in event_types
    assert RunEventType.THINKING_DELTA in event_types
    assert RunEventType.THINKING_FINISHED in event_types
    text_payloads = [
        json.loads(event.payload_json)
        for event in fake_hub.events
        if event.event_type == RunEventType.TEXT_DELTA
    ]
    assert "".join(str(payload["text"]) for payload in text_payloads) == "answer done"


@pytest.mark.asyncio
async def test_subagent_resume_after_stream_cancellation_reuses_db_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "subagent_stream_cancel.db"
    cancel_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(
        db_path,
        cancel_hub,
        run_control_manager=_CountingRunControlManager(cancel_after=4),
    )
    _seed_request(
        message_repo,
        session_id="session-sub",
        instance_id="inst-sub",
        task_id="task-sub",
        trace_id="run-sub",
        content="query time",
        role_id="time",
    )

    cancelled_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[_StreamingTextNode(["partial ", "answer"])],
                messages_by_step=[[]],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=asyncio.CancelledError(),
            )
        ]
    )
    monkeypatch.setattr(llm_module, "ModelRequestNode", _StreamingTextNode)
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: cancelled_agent,
    )
    request = LLMRequest(
        run_id="run-sub",
        trace_id="run-sub",
        task_id="task-sub",
        session_id="session-sub",
        workspace_id="default",
        instance_id="inst-sub",
        role_id="time",
        system_prompt="system",
        user_prompt=None,
    )

    with pytest.raises(asyncio.CancelledError):
        await provider.generate(request)

    history_after_cancel = message_repo.get_history("inst-sub")
    assert len(history_after_cancel) == 1
    assert isinstance(history_after_cancel[0], ModelRequest)
    assert history_after_cancel[0].parts[0].content == "query time"

    resume_hub = _FakeRunEventHub()
    resume_provider, resume_repo = _build_provider(db_path, resume_hub)
    resumed_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="fresh answer")]),
                    messages=[ModelResponse(parts=[TextPart(content="fresh answer")])],
                ),
            )
        ]
    )
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: resumed_agent,
    )

    result = await resume_provider.generate(request)

    assert result == "fresh answer"
    assert resumed_agent.prompts == [None]
    history_after_resume = resume_repo.get_history("inst-sub")
    assert len(history_after_resume) == 2
    assert isinstance(history_after_resume[-1], ModelResponse)
    assert isinstance(history_after_resume[-1].parts[0], TextPart)
    assert history_after_resume[-1].parts[0].content == "fresh answer"


@pytest.mark.asyncio
async def test_subagent_resume_after_tool_call_cancellation_replays_from_safe_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "subagent_tool_call_cancel.db"
    cancel_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(db_path, cancel_hub)
    _seed_request(
        message_repo,
        session_id="session-sub",
        instance_id="inst-sub",
        task_id="task-sub",
        trace_id="run-sub",
        content="query time",
        role_id="time",
    )

    cancelled_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[object()],
                messages_by_step=[
                    [
                        ModelResponse(
                            parts=[
                                ToolCallPart(
                                    tool_name="current_time",
                                    args={"timezone": "UTC"},
                                    tool_call_id="call-pre",
                                )
                            ]
                        )
                    ]
                ],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=asyncio.CancelledError(),
            )
        ]
    )
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: cancelled_agent,
    )
    request = LLMRequest(
        run_id="run-sub",
        trace_id="run-sub",
        task_id="task-sub",
        session_id="session-sub",
        workspace_id="default",
        instance_id="inst-sub",
        role_id="time",
        system_prompt="system",
        user_prompt=None,
    )

    with pytest.raises(asyncio.CancelledError):
        await provider.generate(request)

    history_after_cancel = message_repo.get_history("inst-sub")
    assert len(history_after_cancel) == 1
    cancel_tool_call_payloads = [
        json.loads(event.payload_json)
        for event in cancel_hub.events
        if event.event_type == RunEventType.TOOL_CALL
    ]
    assert [payload["tool_call_id"] for payload in cancel_tool_call_payloads] == [
        "call-pre"
    ]
    assert not any(
        event.event_type == RunEventType.TOOL_RESULT for event in cancel_hub.events
    )

    resume_hub = _FakeRunEventHub()
    resume_provider, resume_repo = _build_provider(db_path, resume_hub)
    resumed_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="done")]),
                    messages=[
                        ModelResponse(
                            parts=[
                                ToolCallPart(
                                    tool_name="current_time",
                                    args={"timezone": "UTC"},
                                    tool_call_id="call-resume",
                                )
                            ]
                        ),
                        ModelRequest(
                            parts=[
                                ToolReturnPart(
                                    tool_name="current_time",
                                    tool_call_id="call-resume",
                                    content={"time": "2026-03-07T10:00:00Z"},
                                )
                            ]
                        ),
                        ModelResponse(parts=[TextPart(content="done")]),
                    ],
                ),
            )
        ]
    )
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: resumed_agent,
    )

    result = await resume_provider.generate(request)

    assert result == "done"
    history_after_resume = resume_repo.get_history("inst-sub")
    tool_calls = [
        part.tool_call_id
        for message in history_after_resume
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ]
    tool_returns = [
        part.tool_call_id
        for message in history_after_resume
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert tool_calls == ["call-resume"]
    assert tool_returns == ["call-resume"]


@pytest.mark.asyncio
async def test_subagent_resume_after_tool_result_before_commit_retries_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "subagent_tool_result_commit_cancel.db"
    cancel_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(db_path, cancel_hub)
    _seed_request(
        message_repo,
        session_id="session-sub",
        instance_id="inst-sub",
        task_id="task-sub",
        trace_id="run-sub",
        content="query time",
        role_id="time",
    )
    scripted_messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="current_time",
                    args={"timezone": "UTC"},
                    tool_call_id="call-once",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="current_time",
                    tool_call_id="call-once",
                    content={"time": "2026-03-07T10:00:00Z"},
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="done")]),
    ]
    completed_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="done")]),
                    messages=scripted_messages,
                ),
            )
        ]
    )
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: completed_agent,
    )
    request = LLMRequest(
        run_id="run-sub",
        trace_id="run-sub",
        task_id="task-sub",
        session_id="session-sub",
        workspace_id="default",
        instance_id="inst-sub",
        role_id="time",
        system_prompt="system",
        user_prompt=None,
    )

    def _interrupt_commit(*args, **kwargs):
        _ = (args, kwargs)
        raise asyncio.CancelledError

    monkeypatch.setattr(provider, "_commit_all_safe_messages", _interrupt_commit)

    with pytest.raises(asyncio.CancelledError):
        await provider.generate(request)

    history_after_cancel = message_repo.get_history("inst-sub")
    assert len(history_after_cancel) == 1

    resume_hub = _FakeRunEventHub()
    resume_provider, resume_repo = _build_provider(db_path, resume_hub)
    resumed_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="done")]),
                    messages=scripted_messages,
                ),
            )
        ]
    )
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: resumed_agent,
    )

    result = await resume_provider.generate(request)

    assert result == "done"
    history_after_resume = resume_repo.get_history("inst-sub")
    tool_returns = [
        part
        for message in history_after_resume
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert len(tool_returns) == 1
    assert tool_returns[0].tool_call_id == "call-once"
    assert isinstance(tool_returns[0].content, dict)
    assert tool_returns[0].content.get("time") == "2026-03-07T10:00:00Z"


@pytest.mark.asyncio
async def test_generate_retries_retryable_status_error_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(tmp_path / "retry_success.db", fake_hub)
    provider._session._retry_config.jitter = False
    provider._session._retry_config.initial_delay_ms = 1

    async def _fast_sleep(delay: float) -> None:
        _ = delay

    monkeypatch.setattr(llm_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(llm_retry_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(llm_module, "compute_retry_delay_ms", lambda **_: 0)
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    request_error = APIStatusError(
        "lock timeout",
        response=httpx.Response(409, request=request),
        body={"error": {"code": "conflict", "message": "busy"}},
    )
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=request_error,
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="after retry")]),
                    messages=[ModelResponse(parts=[TextPart(content="after retry")])],
                ),
            ),
        ]
    )
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )
    request = LLMRequest(
        run_id="run-retry-success",
        trace_id="run-retry-success",
        task_id="task-retry-success",
        session_id="session-retry-success",
        workspace_id="default",
        instance_id="inst-retry-success",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="retry me",
    )

    result = await provider.generate(request)

    assert result == "after retry"
    event_types = [event.event_type for event in fake_hub.events]
    assert event_types.count(RunEventType.MODEL_STEP_STARTED) == 2
    assert RunEventType.LLM_RETRY_SCHEDULED in event_types
    history = message_repo.get_history("inst-retry-success")
    assert len(history) == 2


@pytest.mark.asyncio
async def test_generate_retries_retryable_api_error_after_live_tool_call_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(
        tmp_path / "retry_live_tool_call_event.db", fake_hub
    )
    provider._session._retry_config.jitter = False
    provider._session._retry_config.initial_delay_ms = 1

    async def _fast_sleep(delay: float) -> None:
        _ = delay

    monkeypatch.setattr(llm_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(llm_retry_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(llm_module, "compute_retry_delay_ms", lambda **_: 0)
    request_error = APIError(
        "An error occurred during streaming",
        request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
        body={
            "error_msg": "Too many requests, the rate limit is 8000000 tokens per minute.",
            "error_code": "InferHub.ModelArts.81101.429",
        },
    )
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[object()],
                messages_by_step=[
                    [
                        ModelResponse(
                            parts=[
                                ToolCallPart(
                                    tool_name="write",
                                    args={"path": "notes.txt", "content": "hello"},
                                    tool_call_id="call-live-retry",
                                )
                            ]
                        )
                    ]
                ],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=request_error,
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="after retry")]),
                    messages=[ModelResponse(parts=[TextPart(content="after retry")])],
                ),
            ),
        ]
    )
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )
    request = LLMRequest(
        run_id="run-live-tool-retry",
        trace_id="run-live-tool-retry",
        task_id="task-live-tool-retry",
        session_id="session-live-tool-retry",
        workspace_id="default",
        instance_id="inst-live-tool-retry",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="retry me",
    )

    result = await provider.generate(request)

    assert result == "after retry"
    event_types = [event.event_type for event in fake_hub.events]
    assert RunEventType.TOOL_CALL in event_types
    assert RunEventType.TOOL_RESULT in event_types
    assert RunEventType.LLM_RETRY_SCHEDULED in event_types
    tool_result_event = next(
        event
        for event in fake_hub.events
        if event.event_type == RunEventType.TOOL_RESULT
    )
    tool_result_payload = json.loads(tool_result_event.payload_json)
    assert tool_result_payload["tool_call_id"] == "call-live-retry"
    assert tool_result_payload["error"] is True
    assert (
        tool_result_payload["result"]["error"]["code"]
        == "tool_call_superseded_by_retry"
    )
    history = message_repo.get_history("inst-live-tool-retry")
    assert len(history) == 2


@pytest.mark.asyncio
async def test_generate_resets_retry_budget_after_successful_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(
        tmp_path / "retry_budget_reset.db", fake_hub
    )
    provider._session._retry_config.jitter = False
    provider._session._retry_config.initial_delay_ms = 1
    provider._session._retry_config.max_retries = 1

    async def _fast_sleep(delay: float) -> None:
        _ = delay

    monkeypatch.setattr(llm_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(llm_retry_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(llm_module, "compute_retry_delay_ms", lambda **_: 0)
    monkeypatch.setattr(llm_module, "ModelRequestNode", _FakeModelRequestNode)
    request_error = APIError(
        "An error occurred during streaming",
        request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
        body={
            "error_msg": "Too many requests, the rate limit is 8000000 tokens per minute.",
            "error_code": "InferHub.ModelArts.81101.429",
        },
    )
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=request_error,
            ),
            _ScriptedAgentRun(
                nodes=[_StreamingTextNode([""])],
                messages_by_step=[
                    [
                        ModelResponse(
                            parts=[
                                ToolCallPart(
                                    tool_name="write",
                                    args={"path": "notes.txt", "content": "hello"},
                                    tool_call_id="call-reset-budget",
                                )
                            ]
                        )
                    ]
                ],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=request_error,
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(
                        parts=[TextPart(content="after second retry")]
                    ),
                    messages=[
                        ModelResponse(parts=[TextPart(content="after second retry")])
                    ],
                ),
            ),
        ]
    )
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )
    request = LLMRequest(
        run_id="run-retry-budget-reset",
        trace_id="run-retry-budget-reset",
        task_id="task-retry-budget-reset",
        session_id="session-retry-budget-reset",
        workspace_id="default",
        instance_id="inst-retry-budget-reset",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="retry me",
    )

    result = await provider.generate(request)

    assert result == "after second retry"
    retry_events = [
        event
        for event in fake_hub.events
        if event.event_type == RunEventType.LLM_RETRY_SCHEDULED
    ]
    assert len(retry_events) == 2
    first_payload = json.loads(retry_events[0].payload_json)
    second_payload = json.loads(retry_events[1].payload_json)
    assert first_payload["attempt_number"] == 2
    assert second_payload["attempt_number"] == 2
    tool_result_event = next(
        event
        for event in fake_hub.events
        if event.event_type == RunEventType.TOOL_RESULT
    )
    tool_result_payload = json.loads(tool_result_event.payload_json)
    assert tool_result_payload["tool_call_id"] == "call-reset-budget"
    assert (
        tool_result_payload["result"]["error"]["code"]
        == "tool_call_superseded_by_retry"
    )
    history = message_repo.get_history("inst-retry-budget-reset")
    assert len(history) == 2


@pytest.mark.asyncio
async def test_generate_rebuilds_prompt_context_after_tool_validation_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, _message_repo = _build_provider(
        tmp_path / "validation_restart_prompt_context.db",
        fake_hub,
    )

    validation_node = _FakeModelRequestNode(
        SimpleNamespace(
            input_tokens=0,
            cache_read_tokens=0,
            output_tokens=0,
            total_tokens=0,
            requests=1,
            tool_calls=0,
            details={},
        )
    )
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[validation_node],
                messages_by_step=[
                    [
                        ModelResponse(
                            parts=[
                                ToolCallPart(
                                    tool_name="shell",
                                    args='{"command":"echo hi"}',
                                    tool_call_id="call-1",
                                )
                            ]
                        ),
                        ModelRequest(
                            parts=[
                                RetryPromptPart(
                                    content="Tool arguments were not valid JSON.",
                                    tool_name="shell",
                                    tool_call_id="call-1",
                                )
                            ]
                        ),
                    ]
                ],
                result=_ScriptedResult(response="unused", messages=[]),
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="after restart")]),
                    messages=[ModelResponse(parts=[TextPart(content="after restart")])],
                ),
            ),
        ]
    )
    monkeypatch.setattr(llm_module, "ModelRequestNode", _FakeModelRequestNode)
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )

    prepare_reserve_flags: list[bool] = []
    rebuilt_history = [
        ModelRequest(parts=[UserPromptPart(content="rebuilt compacted history")])
    ]

    async def _prepare_prompt_context_for_restart(
        *,
        request: LLMRequest,
        conversation_id: str,
        system_prompt: str,
        reserve_user_prompt_tokens: bool,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
    ) -> _PreparedPromptContext:
        _ = (
            request,
            conversation_id,
            system_prompt,
            allowed_tools,
            allowed_mcp_servers,
            allowed_skills,
        )
        prepare_reserve_flags.append(reserve_user_prompt_tokens)
        history: tuple[ModelRequest | ModelResponse, ...]
        if len(prepare_reserve_flags) == 1:
            history = ()
        else:
            history = tuple(rebuilt_history)
        return _PreparedPromptContext(
            history=history,
            system_prompt="system",
            budget=build_conversation_compaction_budget(
                context_window=32_000,
                estimated_system_prompt_tokens=0,
                estimated_user_prompt_tokens=0,
                estimated_tool_context_tokens=0,
                estimated_output_reserve_tokens=0,
            ),
        )

    monkeypatch.setattr(
        provider._session,
        "_prepare_prompt_context",
        _prepare_prompt_context_for_restart,
    )

    request = LLMRequest(
        run_id="run-validation-restart",
        trace_id="run-validation-restart",
        task_id="task-validation-restart",
        session_id="session-validation-restart",
        workspace_id="default",
        instance_id="inst-validation-restart",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="retry me",
    )

    result = await provider.generate(request)

    assert result == "after restart"
    assert prepare_reserve_flags == [True, False]
    assert len(scripted_agent.histories) == 2
    assert scripted_agent.histories[1] == rebuilt_history


@pytest.mark.asyncio
async def test_generate_uses_parsed_provider_error_code_for_non_retryable_model_api_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(
        tmp_path / "non_retryable_model_api_error.db",
        fake_hub,
    )
    provider._session._retry_config.jitter = False
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=ModelAPIError(
                    model_name="fake-chat-model",
                    message="provider rejected request status_code: 401",
                ),
            )
        ]
    )
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )
    request = LLMRequest(
        run_id="run-non-retryable-model-api-error",
        trace_id="run-non-retryable-model-api-error",
        task_id="task-non-retryable-model-api-error",
        session_id="session-non-retryable-model-api-error",
        workspace_id="default",
        instance_id="inst-non-retryable-model-api-error",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="retry me",
    )

    with pytest.raises(AssistantRunError) as exc_info:
        await provider.generate(request)

    assert exc_info.value.payload.error_code == "auth_invalid"
    assert (
        exc_info.value.payload.error_message
        == "provider rejected request status_code: 401"
    )
    history = message_repo.get_history("inst-non-retryable-model-api-error")
    assert len(history) == 2
    final_message = history[-1]
    assert isinstance(final_message, ModelResponse)
    assert isinstance(final_message.parts[0], TextPart)
    assert "API key is invalid" in final_message.parts[0].content


@pytest.mark.asyncio
async def test_generate_does_not_retry_after_streamed_text_side_effect_for_non_transport_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(tmp_path / "retry_blocked.db", fake_hub)
    provider._session._retry_config.jitter = False
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[_StreamingTextNode(["partial "])],
                messages_by_step=[[]],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=APIError(
                    "provider error",
                    request=httpx.Request(
                        "POST",
                        "https://example.test/v1/chat/completions",
                    ),
                    body={"error": {"code": "2062", "message": "busy"}},
                ),
            )
        ]
    )
    monkeypatch.setattr(llm_module, "ModelRequestNode", _StreamingTextNode)
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )
    request = LLMRequest(
        run_id="run-retry-blocked",
        trace_id="run-retry-blocked",
        task_id="task-retry-blocked",
        session_id="session-retry-blocked",
        workspace_id="default",
        instance_id="inst-retry-blocked",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="retry me",
    )

    with pytest.raises(AssistantRunError) as exc_info:
        await provider.generate(request)

    event_types = [event.event_type for event in fake_hub.events]
    assert RunEventType.LLM_RETRY_SCHEDULED not in event_types
    assert exc_info.value.payload.error_message == "busy"
    history = message_repo.get_history("inst-retry-blocked")
    assert len(history) == 2
    final_message = history[-1]
    assert isinstance(final_message, ModelResponse)
    assert isinstance(final_message.parts[0], TextPart)
    assert "Continue from the latest successful conversation state" in (
        final_message.parts[0].content
    )


@pytest.mark.asyncio
async def test_generate_retries_midstream_provider_500_after_streamed_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(
        tmp_path / "retry_midstream_500.db", fake_hub
    )
    provider._session._retry_config.jitter = False
    provider._session._retry_config.initial_delay_ms = 1

    async def _fast_sleep(delay: float) -> None:
        _ = delay

    monkeypatch.setattr(llm_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(llm_retry_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(llm_module, "compute_retry_delay_ms", lambda **_: 0)
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[_StreamingTextNode(["partial "])],
                messages_by_step=[[]],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=APIStatusError(
                    "server error",
                    response=httpx.Response(
                        500,
                        request=httpx.Request(
                            "POST",
                            "https://example.test/v1/chat/completions",
                        ),
                    ),
                    body={"error": {"code": "provider_error", "message": "retry me"}},
                ),
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="after retry")]),
                    messages=[ModelResponse(parts=[TextPart(content="after retry")])],
                ),
            ),
        ]
    )
    monkeypatch.setattr(llm_module, "ModelRequestNode", _StreamingTextNode)
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )
    request = LLMRequest(
        run_id="run-midstream-500",
        trace_id="run-midstream-500",
        task_id="task-midstream-500",
        session_id="session-midstream-500",
        workspace_id="default",
        instance_id="inst-midstream-500",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="retry me",
    )

    result = await provider.generate(request)

    assert result == "after retry"
    event_types = [event.event_type for event in fake_hub.events]
    assert RunEventType.LLM_RETRY_SCHEDULED in event_types
    history = message_repo.get_history("inst-midstream-500")
    assert len(history) == 2


@pytest.mark.asyncio
async def test_generate_retries_midstream_provider_429_after_streamed_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(
        tmp_path / "retry_midstream_429.db", fake_hub
    )
    provider._session._retry_config.jitter = False
    provider._session._retry_config.initial_delay_ms = 1

    async def _fast_sleep(delay: float) -> None:
        _ = delay

    monkeypatch.setattr(llm_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(llm_retry_module.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(llm_module, "compute_retry_delay_ms", lambda **_: 0)
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[_StreamingTextNode(["partial "])],
                messages_by_step=[[]],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=APIStatusError(
                    "rate limited",
                    response=httpx.Response(
                        429,
                        request=httpx.Request(
                            "POST",
                            "https://example.test/v1/chat/completions",
                        ),
                    ),
                    body={"error": {"code": "rate_limited", "message": "retry me"}},
                ),
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="after retry")]),
                    messages=[ModelResponse(parts=[TextPart(content="after retry")])],
                ),
            ),
        ]
    )
    monkeypatch.setattr(llm_module, "ModelRequestNode", _StreamingTextNode)
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )
    request = LLMRequest(
        run_id="run-midstream-429",
        trace_id="run-midstream-429",
        task_id="task-midstream-429",
        session_id="session-midstream-429",
        workspace_id="default",
        instance_id="inst-midstream-429",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="retry me",
    )

    result = await provider.generate(request)

    assert result == "after retry"
    event_types = [event.event_type for event in fake_hub.events]
    assert RunEventType.LLM_RETRY_SCHEDULED in event_types
    history = message_repo.get_history("inst-midstream-429")
    assert len(history) == 2


@pytest.mark.asyncio
async def test_generate_resumes_after_committed_tool_result_for_retryable_api_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(
        tmp_path / "resume_after_tool_result.db", fake_hub
    )
    provider._session._retry_config.jitter = False
    request_error = APIError(
        "An error occurred during streaming",
        request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
        body={
            "error_msg": "Too many requests, the rate limit is 8000000 tokens per minute.",
            "error_code": "InferHub.ModelArts.81101.429",
        },
    )
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[object()],
                messages_by_step=[
                    [
                        ModelResponse(
                            parts=[
                                ToolCallPart(
                                    tool_name="write",
                                    args={"path": "notes.txt", "content": "hello"},
                                    tool_call_id="call-real-result",
                                )
                            ]
                        ),
                        ModelRequest(
                            parts=[
                                ToolReturnPart(
                                    tool_name="write",
                                    tool_call_id="call-real-result",
                                    content={"ok": True, "data": {"saved": True}},
                                )
                            ]
                        ),
                    ]
                ],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=request_error,
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(
                        parts=[TextPart(content="continued after tool result")]
                    ),
                    messages=[
                        ModelResponse(
                            parts=[TextPart(content="continued after tool result")]
                        )
                    ],
                ),
            ),
        ]
    )
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )
    request = LLMRequest(
        run_id="run-tool-result-resume",
        trace_id="run-tool-result-resume",
        task_id="task-tool-result-resume",
        session_id="session-tool-result-resume",
        workspace_id="default",
        instance_id="inst-tool-result-resume",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="retry me",
    )

    result = await provider.generate(request)

    assert result == "continued after tool result"
    event_types = [event.event_type for event in fake_hub.events]
    assert RunEventType.LLM_RETRY_SCHEDULED not in event_types
    assert event_types.count(RunEventType.TOOL_RESULT) == 1
    assert len(scripted_agent.histories) == 2
    resumed_history = scripted_agent.histories[1]
    assert isinstance(resumed_history[-1], ModelRequest)
    resumed_result_message = resumed_history[-1]
    assert isinstance(resumed_result_message.parts[0], ToolReturnPart)
    assert resumed_result_message.parts[0].tool_call_id == "call-real-result"
    history = message_repo.get_history("inst-tool-result-resume")
    assert len(history) == 4


@pytest.mark.asyncio
async def test_generate_resumes_after_failed_tool_result_for_retryable_api_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(
        tmp_path / "resume_after_failed_tool_result.db", fake_hub
    )
    provider._session._retry_config.jitter = False
    request_error = APIError(
        "An error occurred during streaming",
        request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
        body={
            "error_msg": "Too many requests, the rate limit is 8000000 tokens per minute.",
            "error_code": "InferHub.ModelArts.81101.429",
        },
    )
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[object()],
                messages_by_step=[
                    [
                        ModelResponse(
                            parts=[
                                ToolCallPart(
                                    tool_name="write",
                                    args={"path": "notes.txt", "content": "hello"},
                                    tool_call_id="call-failed-result",
                                )
                            ]
                        ),
                        ModelRequest(
                            parts=[
                                ToolReturnPart(
                                    tool_name="write",
                                    tool_call_id="call-failed-result",
                                    content={
                                        "ok": False,
                                        "error": {
                                            "code": "write_failed",
                                            "message": "disk full",
                                        },
                                    },
                                )
                            ]
                        ),
                    ]
                ],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=request_error,
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(
                        parts=[TextPart(content="continued after tool failure")]
                    ),
                    messages=[
                        ModelResponse(
                            parts=[TextPart(content="continued after tool failure")]
                        )
                    ],
                ),
            ),
        ]
    )
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )
    request = LLMRequest(
        run_id="run-failed-tool-result-resume",
        trace_id="run-failed-tool-result-resume",
        task_id="task-failed-tool-result-resume",
        session_id="session-failed-tool-result-resume",
        workspace_id="default",
        instance_id="inst-failed-tool-result-resume",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="retry me",
    )

    result = await provider.generate(request)

    assert result == "continued after tool failure"
    event_types = [event.event_type for event in fake_hub.events]
    assert RunEventType.LLM_RETRY_SCHEDULED not in event_types
    assert event_types.count(RunEventType.TOOL_RESULT) == 1
    assert len(scripted_agent.histories) == 2
    resumed_history = scripted_agent.histories[1]
    assert isinstance(resumed_history[-1], ModelRequest)
    resumed_result_message = resumed_history[-1]
    assert isinstance(resumed_result_message.parts[0], ToolReturnPart)
    assert resumed_result_message.parts[0].tool_call_id == "call-failed-result"
    history = message_repo.get_history("inst-failed-tool-result-resume")
    assert len(history) == 4


@pytest.mark.asyncio
async def test_generate_does_not_retry_after_committed_messages_for_retryable_api_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(
        tmp_path / "retry_blocked_committed_message.db", fake_hub
    )
    provider._session._retry_config.jitter = False
    request_error = APIError(
        "An error occurred during streaming",
        request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
        body={
            "error_msg": "Too many requests, the rate limit is 8000000 tokens per minute.",
            "error_code": "InferHub.ModelArts.81101.429",
        },
    )
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[object()],
                messages_by_step=[
                    [ModelResponse(parts=[TextPart(content="committed before error")])]
                ],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=request_error,
            )
        ]
    )
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )
    request = LLMRequest(
        run_id="run-committed-message-blocked",
        trace_id="run-committed-message-blocked",
        task_id="task-committed-message-blocked",
        session_id="session-committed-message-blocked",
        workspace_id="default",
        instance_id="inst-committed-message-blocked",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="retry me",
    )

    with pytest.raises(AssistantRunError):
        await provider.generate(request)

    event_types = [event.event_type for event in fake_hub.events]
    assert RunEventType.LLM_RETRY_SCHEDULED not in event_types
    assert len(scripted_agent.histories) == 1
    history = message_repo.get_history("inst-committed-message-blocked")
    assert len(history) == 3


@pytest.mark.asyncio
async def test_generate_pauses_on_invalid_tool_args_json_after_committed_tool_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(
        tmp_path / "retry_invalid_tool_args.db", fake_hub
    )
    provider._session._retry_config.jitter = False
    committed_tool_messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="current_time",
                    args={"timezone": "UTC"},
                    tool_call_id="call-safe",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="current_time",
                    tool_call_id="call-safe",
                    content={"time": "2026-03-27T09:37:00Z"},
                )
            ]
        ),
    ]
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[object()],
                messages_by_step=[committed_tool_messages],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=json.JSONDecodeError(
                    "Expecting property name enclosed in double quotes",
                    "{invalid: true}",
                    1,
                ),
            )
        ]
    )
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )
    request = LLMRequest(
        run_id="run-invalid-tool-args",
        trace_id="run-invalid-tool-args",
        task_id="task-invalid-tool-args",
        session_id="session-invalid-tool-args",
        workspace_id="default",
        instance_id="inst-invalid-tool-args",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="retry me",
    )

    with pytest.raises(AssistantRunError) as exc_info:
        await provider.generate(request)

    event_types = [event.event_type for event in fake_hub.events]
    assert RunEventType.LLM_RETRY_SCHEDULED not in event_types
    assert RunEventType.TOOL_CALL in event_types
    assert RunEventType.TOOL_RESULT in event_types
    assert exc_info.value.payload.error_code == "model_tool_args_invalid_json"
    assert "Expecting property name enclosed in double quotes" in (
        exc_info.value.payload.error_message
    )
    history = message_repo.get_history("inst-invalid-tool-args")
    assert len(history) == 4
    final_message = history[-1]
    assert isinstance(final_message, ModelResponse)
    assert isinstance(final_message.parts[0], TextPart)
    assert "not valid JSON" in final_message.parts[0].content


@pytest.mark.asyncio
async def test_generate_salvages_streamed_tool_call_parse_failure_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(
        tmp_path / "stream_tool_salvage.db", fake_hub
    )
    usage_after_request = SimpleNamespace(
        input_tokens=0,
        cache_read_tokens=0,
        output_tokens=0,
        total_tokens=0,
        requests=1,
        tool_calls=0,
        details={"reasoning_tokens": 0},
    )
    part_events = [
        PartStartEvent(index=0, part=TextPart(content="继续生成第5和第6页。")),
        PartStartEvent(
            index=1,
            part=ToolCallPart(
                tool_name="write",
                args='{"content":"broken"',
                tool_call_id="call-live",
            ),
        ),
        PartDeltaEvent(
            index=1, delta=ToolCallPartDelta(args_delta=', path:"demo.html"}')
        ),
    ]
    request_error = APIStatusError(
        "bad request",
        response=httpx.Response(
            400,
            request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
        ),
        body={
            "error": {
                "message": (
                    "litellm.BadRequestError: OpenAIException - "
                    "Expecting property name enclosed in double quotes: "
                    "line 1 column 2 (char 1)"
                )
            }
        },
    )
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[_PartEventNode(part_events, usage_after_request)],
                messages_by_step=[[]],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=request_error,
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="recovered done")]),
                    messages=[
                        ModelResponse(parts=[TextPart(content="recovered done")])
                    ],
                ),
            ),
        ]
    )

    monkeypatch.setattr(llm_module, "ModelRequestNode", _PartEventNode)
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )

    request = LLMRequest(
        run_id="run-stream-tool-salvage",
        trace_id="run-stream-tool-salvage",
        task_id="task-stream-tool-salvage",
        session_id="session-stream-tool-salvage",
        workspace_id="default",
        instance_id="inst-stream-tool-salvage",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="continue",
    )

    result = await provider.generate(request)

    assert result == "recovered done"
    history = message_repo.get_history("inst-stream-tool-salvage")
    assert len(history) == 4
    assert isinstance(history[1], ModelResponse)
    salvaged_response = history[1]
    assert isinstance(salvaged_response.parts[0], TextPart)
    assert isinstance(salvaged_response.parts[1], ToolCallPart)
    assert salvaged_response.parts[1].tool_call_id == "call-live"
    assert salvaged_response.parts[1].args == {
        "content": "broken",
        "path": "demo.html",
    }
    assert isinstance(history[2], ModelRequest)
    salvaged_request = history[2]
    assert isinstance(salvaged_request.parts[0], ToolReturnPart)
    assert salvaged_request.parts[0].tool_call_id == "call-live"
    result_payload = cast(dict[str, object], salvaged_request.parts[0].content)
    assert result_payload["ok"] is False
    error_payload = cast(dict[str, object], result_payload["error"])
    assert error_payload["code"] == "tool_input_validation_failed"
    error_message = cast(str, error_payload["message"])
    assert "Tool arguments were not valid JSON." in error_message
    assert "Expecting property name enclosed in double quotes" in error_message
    event_types = [event.event_type for event in fake_hub.events]
    assert RunEventType.TOOL_CALL in event_types
    assert RunEventType.TOOL_RESULT in event_types
    assert len(scripted_agent.histories) == 2
    second_history = scripted_agent.histories[1]
    assert any(
        isinstance(message, ModelRequest)
        and any(isinstance(part, ToolReturnPart) for part in message.parts)
        for message in second_history
    )


@pytest.mark.asyncio
async def test_generate_bounds_repeated_streamed_tool_call_parse_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, _message_repo = _build_provider(
        tmp_path / "stream_tool_salvage_bounded.db",
        fake_hub,
    )
    provider._session._retry_config.max_retries = 1
    provider._session._retry_config.jitter = False
    usage_after_request = SimpleNamespace(
        input_tokens=0,
        cache_read_tokens=0,
        output_tokens=0,
        total_tokens=0,
        requests=1,
        tool_calls=0,
        details={"reasoning_tokens": 0},
    )
    part_events = [
        PartStartEvent(
            index=0,
            part=ToolCallPart(
                tool_name="write",
                args='{"content":"broken"',
                tool_call_id="call-live-bounded",
            ),
        ),
        PartDeltaEvent(
            index=0,
            delta=ToolCallPartDelta(args_delta=', path:"demo.html"}'),
        ),
    ]
    request_error = APIStatusError(
        "bad request",
        response=httpx.Response(
            400,
            request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
        ),
        body={
            "error": {
                "message": (
                    "litellm.BadRequestError: OpenAIException - "
                    "Expecting property name enclosed in double quotes: "
                    "line 1 column 2 (char 1)"
                )
            }
        },
    )
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[_PartEventNode(part_events, usage_after_request)],
                messages_by_step=[[]],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=request_error,
            ),
            _ScriptedAgentRun(
                nodes=[_PartEventNode(part_events, usage_after_request)],
                messages_by_step=[[]],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=request_error,
            ),
        ]
    )
    monkeypatch.setattr(llm_module, "ModelRequestNode", _PartEventNode)
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )
    request = LLMRequest(
        run_id="run-stream-tool-salvage-bounded",
        trace_id="run-stream-tool-salvage-bounded",
        task_id="task-stream-tool-salvage-bounded",
        session_id="session-stream-tool-salvage-bounded",
        workspace_id="default",
        instance_id="inst-stream-tool-salvage-bounded",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="retry me",
    )

    with pytest.raises(AssistantRunError) as exc_info:
        await provider.generate(request)

    assert exc_info.value.payload.error_code == "model_tool_args_invalid_json"
    assert len(scripted_agent.histories) == 2
    event_types = [event.event_type for event in fake_hub.events]
    assert RunEventType.LLM_RETRY_SCHEDULED not in event_types


@pytest.mark.asyncio
async def test_generate_continues_after_tool_input_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(
        tmp_path / "tool_input_validation_continue.db",
        fake_hub,
    )
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[object()],
                messages_by_step=[
                    [
                        ModelResponse(
                            parts=[
                                ToolCallPart(
                                    tool_name="write",
                                    args={"content": "broken"},
                                    tool_call_id="call-invalid-write",
                                )
                            ]
                        ),
                        ModelRequest(
                            parts=[
                                RetryPromptPart(
                                    tool_name="write",
                                    tool_call_id="call-invalid-write",
                                    content="Field required: path",
                                )
                            ]
                        ),
                    ]
                ],
                result=_ScriptedResult(response="unused", messages=[]),
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="continued")]),
                    messages=[ModelResponse(parts=[TextPart(content="continued")])],
                ),
            ),
        ]
    )
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )
    request = LLMRequest(
        run_id="run-tool-input-validation-continue",
        trace_id="run-tool-input-validation-continue",
        task_id="task-tool-input-validation-continue",
        session_id="session-tool-input-validation-continue",
        workspace_id="default",
        instance_id="inst-tool-input-validation-continue",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="continue",
    )

    result = await provider.generate(request)

    assert result == "continued"
    assert len(scripted_agent.histories) == 2
    second_history = scripted_agent.histories[1]
    normalized_tool_errors = [
        part
        for message in second_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert len(normalized_tool_errors) == 1
    assert normalized_tool_errors[0].tool_call_id == "call-invalid-write"
    assert isinstance(normalized_tool_errors[0].content, dict)
    error_result = cast(dict[str, object], normalized_tool_errors[0].content)
    assert error_result["ok"] is False
    error_payload = cast(dict[str, object], error_result["error"])
    assert error_payload["code"] == "tool_input_validation_failed"
    assert error_payload["message"] == "Field required: path"
    event_types = [event.event_type for event in fake_hub.events]
    assert RunEventType.TOOL_RESULT in event_types
    assert RunEventType.TOOL_INPUT_VALIDATION_FAILED not in event_types
    history = message_repo.get_history("inst-tool-input-validation-continue")
    assert any(
        isinstance(message, ModelRequest)
        and any(isinstance(part, ToolReturnPart) for part in message.parts)
        for message in history
    )


@pytest.mark.asyncio
async def test_generate_salvages_unrepairable_streamed_tool_call_with_invalid_json_wrapper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(
        tmp_path / "stream_tool_salvage_invalid_wrapper.db",
        fake_hub,
    )
    usage_after_request = SimpleNamespace(
        input_tokens=0,
        cache_read_tokens=0,
        output_tokens=0,
        total_tokens=0,
        requests=1,
        tool_calls=0,
        details={"reasoning_tokens": 0},
    )
    part_events: list[object] = [
        PartStartEvent(
            index=0,
            part=ToolCallPart(
                tool_name="write",
                args="not-json-at-all",
                tool_call_id="call-live-invalid",
            ),
        )
    ]
    request_error = APIStatusError(
        "bad request",
        response=httpx.Response(
            400,
            request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
        ),
        body={
            "error": {
                "message": (
                    "litellm.BadRequestError: OpenAIException - "
                    "Expecting value: line 1 column 1 (char 0)"
                )
            }
        },
    )
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[cast(object, _PartEventNode(part_events, usage_after_request))],
                messages_by_step=[[]],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=request_error,
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(
                    response=ModelResponse(parts=[TextPart(content="recovered done")]),
                    messages=[
                        ModelResponse(parts=[TextPart(content="recovered done")])
                    ],
                ),
            ),
        ]
    )

    monkeypatch.setattr(llm_module, "ModelRequestNode", _PartEventNode)
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )

    request = LLMRequest(
        run_id="run-stream-tool-salvage-invalid",
        trace_id="run-stream-tool-salvage-invalid",
        task_id="task-stream-tool-salvage-invalid",
        session_id="session-stream-tool-salvage-invalid",
        workspace_id="default",
        instance_id="inst-stream-tool-salvage-invalid",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="continue",
    )

    result = await provider.generate(request)

    assert result == "recovered done"
    history = message_repo.get_history("inst-stream-tool-salvage-invalid")
    assert len(history) == 4
    salvaged_response = cast(ModelResponse, history[1])
    assert isinstance(salvaged_response.parts[0], ToolCallPart)
    assert salvaged_response.parts[0].args == {"INVALID_JSON": "not-json-at-all"}


@pytest.mark.asyncio
async def test_generate_publishes_retry_exhausted_event_on_final_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(tmp_path / "retry_exhausted.db", fake_hub)
    provider._session._retry_config.jitter = False
    provider._session._retry_config.initial_delay_ms = 10
    provider._session._retry_config.max_retries = 2
    request_error = APIStatusError(
        "timeout",
        response=httpx.Response(
            408,
            request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
        ),
        body={"error": {"code": "request_timeout", "message": "busy"}},
    )
    scripted_agent = _SequentialAgent(
        [
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=request_error,
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=request_error,
            ),
            _ScriptedAgentRun(
                nodes=[],
                messages_by_step=[],
                result=_ScriptedResult(response="unused", messages=[]),
                raise_on_exhaust=request_error,
            ),
        ]
    )
    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: scripted_agent,
    )
    request = LLMRequest(
        run_id="run-retry-exhausted",
        trace_id="run-retry-exhausted",
        task_id="task-retry-exhausted",
        session_id="session-retry-exhausted",
        workspace_id="default",
        instance_id="inst-retry-exhausted",
        role_id="Coordinator",
        system_prompt="system",
        user_prompt="retry me",
    )

    with pytest.raises(AssistantRunError) as exc_info:
        await provider.generate(request)

    event_types = [event.event_type for event in fake_hub.events]
    assert event_types.count(RunEventType.LLM_RETRY_SCHEDULED) == 2
    assert RunEventType.LLM_RETRY_EXHAUSTED in event_types
    assert exc_info.value.payload.error_message == "busy"
    exhausted_event = next(
        event
        for event in fake_hub.events
        if event.event_type == RunEventType.LLM_RETRY_EXHAUSTED
    )
    payload = json.loads(exhausted_event.payload_json)
    assert payload["attempt_number"] == 3
    assert payload["total_attempts"] == 3
    assert payload["error_message"] == "busy"
    history = message_repo.get_history("inst-retry-exhausted")
    assert len(history) == 2
    final_message = history[-1]
    assert isinstance(final_message, ModelResponse)
    assert isinstance(final_message.parts[0], TextPart)
    assert "Continue from the latest successful conversation state" in (
        final_message.parts[0].content
    )
