# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from openai import APIError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPartDelta,
    TextPart,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

import agent_teams.agents.execution.llm_session as llm_module
from agent_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from agent_teams.agents.orchestration.task_execution_service import TaskExecutionService
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.agents.execution.system_prompts import PromptSkillInstruction
from agent_teams.providers.provider_contracts import LLMRequest
from agent_teams.providers.openai_compatible import OpenAICompatibleProvider
from agent_teams.providers.model_config import ModelEndpointConfig
from agent_teams.roles import RoleMemoryService
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.run_models import RunEvent
from agent_teams.sessions.runs.run_models import RunThinkingConfig
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.tools.registry import ToolRegistry
from agent_teams.tools.runtime import ToolApprovalManager, ToolApprovalPolicy
from agent_teams.workspace import WorkspaceManager, build_conversation_id


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


class _FakeSkillRegistry:
    def __init__(self, entries: tuple[PromptSkillInstruction, ...]) -> None:
        self._entries = entries
        self.requested: list[tuple[str, ...]] = []

    def get_instruction_entries(
        self, skill_names: tuple[str, ...]
    ) -> tuple[PromptSkillInstruction, ...]:
        self.requested.append(skill_names)
        return self._entries


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

    def iter(
        self,
        prompt: str | None,
        *,
        deps: object,
        message_history: object,
        usage_limits: object,
    ) -> _FakeAgentRun:
        _ = (deps, message_history)
        self.prompts.append(prompt)
        self.usage_limits.append(usage_limits)
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
    allowed_skills: tuple[str, ...] = (),
    skill_registry: object | None = None,
    run_control_manager: object | None = None,
) -> tuple[OpenAICompatibleProvider, MessageRepository]:
    registry = (
        cast(SkillRegistry, skill_registry)
        if skill_registry is not None
        else cast(SkillRegistry, object())
    )
    shared_store = SharedStateRepository(db_path)
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="coordinator_agent",
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
    provider = OpenAICompatibleProvider(
        config,
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
        workspace_manager=WorkspaceManager(
            project_root=Path("."), shared_store=shared_store
        ),
        role_memory_service=cast(RoleMemoryService | None, None),
        subagent_reflection_service=None,
        tool_registry=cast(ToolRegistry, object()),
        mcp_registry=cast(McpRegistry, object()),
        skill_registry=registry,
        allowed_tools=allowed_tools,
        allowed_mcp_servers=(),
        allowed_skills=allowed_skills,
        message_repo=message_repo,
        role_registry=role_registry,
        task_execution_service=cast(TaskExecutionService, object()),
        task_service=cast(TaskOrchestrationService, object()),
        run_control_manager=cast(
            RunControlManager,
            cast(object, run_control_manager or _FakeRunControlManager()),
        ),
        tool_approval_manager=cast(ToolApprovalManager, object()),
        tool_approval_policy=ToolApprovalPolicy(),
    )
    return provider, message_repo


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
async def test_generate_persists_current_turn_prompt_even_with_existing_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(tmp_path / "current_turn.db", fake_hub)

    _seed_request(
        message_repo,
        session_id="session-2",
        instance_id="inst-2",
        task_id="task-2",
        trace_id="run-2",
        content="previous turn",
        role_id="coordinator_agent",
    )

    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: fake_agent,
    )

    request = LLMRequest(
        run_id="run-2",
        trace_id="run-2",
        task_id="task-2",
        session_id="session-2",
        workspace_id="default",
        instance_id="inst-2",
        role_id="coordinator_agent",
        system_prompt="system",
        user_prompt="current turn",
    )

    _ = await provider.generate(request)

    history = message_repo.get_history("inst-2")
    assert fake_agent.prompts == [None]
    assert len(fake_agent.usage_limits) == 1
    usage_limits = fake_agent.usage_limits[0]
    assert isinstance(usage_limits, llm_module.UsageLimits)
    assert usage_limits.request_limit == llm_module.LLM_REQUEST_LIMIT == 500
    assert isinstance(history[-1], ModelRequest)
    assert history[-1].parts[0].content == "current turn"


@pytest.mark.asyncio
async def test_generate_prunes_pending_tool_call_tail_before_persisting_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(tmp_path / "pending_tail.db", fake_hub)
    message_repo.append(
        session_id="session-pending-tool",
        workspace_id="default",
        conversation_id=build_conversation_id(
            "session-pending-tool",
            "coordinator_agent",
        ),
        agent_role_id="coordinator_agent",
        instance_id="inst-pending-tool",
        task_id="task-pending-tool",
        trace_id="run-pending-tool",
        messages=[
            ModelRequest(parts=[UserPromptPart(content="previous turn")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="create_tasks",
                        args={"objective": "x"},
                        tool_call_id="call-1",
                    )
                ]
            ),
        ],
    )

    monkeypatch.setattr(
        llm_module,
        "build_coordination_agent",
        lambda **kwargs: fake_agent,
    )

    request = LLMRequest(
        run_id="run-pending-tool",
        trace_id="run-pending-tool",
        task_id="task-pending-tool",
        session_id="session-pending-tool",
        workspace_id="default",
        instance_id="inst-pending-tool",
        role_id="coordinator_agent",
        system_prompt="system",
        user_prompt="current turn",
    )

    _ = await provider.generate(request)

    history = message_repo.get_history("inst-pending-tool")
    assert fake_agent.prompts == [None]
    assert len(history) == 2
    assert isinstance(history[0], ModelRequest)
    assert isinstance(history[1], ModelRequest)
    assert history[1].parts[0].content == "current turn"


@pytest.mark.asyncio
async def test_generate_enables_continuous_stream_usage_stats(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    fake_hub = _FakeRunEventHub()
    provider, _ = _build_provider(tmp_path / "settings.db", fake_hub)
    captured_kwargs: dict[str, object] = {}

    def _fake_builder(**kwargs: object) -> _FakeAgent:
        captured_kwargs.update(kwargs)
        return fake_agent

    monkeypatch.setattr(llm_module, "build_coordination_agent", _fake_builder)

    request = LLMRequest(
        run_id="run-3",
        trace_id="run-3",
        task_id="task-3",
        session_id="session-3",
        workspace_id="default",
        instance_id="inst-3",
        role_id="coordinator_agent",
        system_prompt="system",
        user_prompt="current turn",
    )

    _ = await provider.generate(request)

    settings_obj = captured_kwargs.get("model_settings")
    assert isinstance(settings_obj, dict)
    assert settings_obj.get("openai_continuous_usage_stats") is True
    assert settings_obj.get("temperature") == provider._config.sampling.temperature
    assert settings_obj.get("top_p") == provider._config.sampling.top_p
    assert settings_obj.get("max_tokens") == provider._config.sampling.max_tokens


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
        role_id="coordinator_agent",
        system_prompt="system",
        user_prompt="current turn",
        thinking=RunThinkingConfig(enabled=True, effort="high"),
    )

    _ = await provider.generate(request)

    settings_obj = captured_kwargs.get("model_settings")
    assert isinstance(settings_obj, dict)
    assert settings_obj.get("openai_reasoning_effort") == "high"


@pytest.mark.asyncio
async def test_generate_builds_augmented_system_prompt(
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
        role_id="coordinator_agent",
        system_prompt="## Role\nBase system prompt.",
        user_prompt="current turn",
    )

    _ = await provider.generate(request)

    system_prompt_obj = captured_kwargs.get("system_prompt")
    assert isinstance(system_prompt_obj, str)
    assert system_prompt_obj.startswith("## Role\nBase system prompt.")
    assert "## Available Skills" in system_prompt_obj
    assert "- time: Normalize all times to UTC." in system_prompt_obj
    assert fake_skill_registry.requested == [("time",)]
    prepared_events = [
        event
        for event in captured_events
        if event.get("event") == "llm.system_prompt.prepared"
    ]
    assert len(prepared_events) == 1
    assert "## Role\nBase system prompt." in str(prepared_events[0].get("message", ""))
    assert prepared_events[0].get("payload") == {
        "role_id": "coordinator_agent",
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
        role_id="coordinator_agent",
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
        role_id="coordinator_agent",
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
        role_id="coordinator_agent",
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
        role_id="coordinator_agent",
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
        role_id="coordinator_agent",
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
        role_id="coordinator_agent",
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
        role_id="coordinator_agent",
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
    assert any(
        event.event_type == RunEventType.TOOL_CALL for event in cancel_hub.events
    )
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
async def test_generate_retries_provider_coded_error_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, message_repo = _build_provider(tmp_path / "retry_success.db", fake_hub)
    provider._session._retry_config.jitter = False
    request_error = APIError(
        "provider error",
        request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
        body={"error": {"code": "2062", "message": "busy"}},
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
        role_id="coordinator_agent",
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
async def test_generate_does_not_retry_after_streamed_text_side_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, _ = _build_provider(tmp_path / "retry_blocked.db", fake_hub)
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
        role_id="coordinator_agent",
        system_prompt="system",
        user_prompt="retry me",
    )

    with pytest.raises(APIError):
        await provider.generate(request)

    event_types = [event.event_type for event in fake_hub.events]
    assert RunEventType.LLM_RETRY_SCHEDULED not in event_types
