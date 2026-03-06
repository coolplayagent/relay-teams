# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart

import agent_teams.providers.llm as llm_module
from agent_teams.coordination.task_execution_service import TaskExecutionService
from agent_teams.providers.model_config import ModelEndpointConfig
from agent_teams.mcp.registry import McpRegistry
from agent_teams.providers.llm import LLMRequest, OpenAICompatibleProvider
from agent_teams.roles.registry import RoleRegistry
from agent_teams.runs.injection_queue import RunInjectionManager
from agent_teams.runs.control import RunControlManager
from agent_teams.runs.event_stream import RunEventHub
from agent_teams.tools.runtime import ToolApprovalManager
from agent_teams.prompting.provider_augment import PromptSkillInstruction
from agent_teams.skills.registry import SkillRegistry
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository
from agent_teams.runs.models import RunEvent
from agent_teams.tools.runtime import ToolApprovalPolicy
from agent_teams.tools.registry import ToolRegistry
from agent_teams.agents.management.instance_pool import InstancePool


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


class _FakeInjectionManager:
    def drain_at_boundary(self, run_id: str, instance_id: str) -> list[object]:
        _ = (run_id, instance_id)
        return []


class _FakeTaskRepository:
    pass


class _FakeInstancePool:
    pass


class _FakeSharedStore:
    pass


class _FakeEventLog:
    pass


class _FakeSkillRegistry:
    def __init__(self, entries: tuple[PromptSkillInstruction, ...]) -> None:
        self._entries = entries
        self.requested: list[tuple[str, ...]] = []

    def get_instruction_entries(
        self, skill_names: tuple[str, ...]
    ) -> tuple[PromptSkillInstruction, ...]:
        self.requested.append(skill_names)
        return self._entries


class _FakeMessageRepo:
    def __init__(self) -> None:
        self.history = [ModelRequest(parts=[UserPromptPart(content="previous turn")])]

    def get_history(self, instance_id: str) -> list[ModelRequest]:
        _ = instance_id
        return list(self.history)

    def append(self, **kwargs: object) -> None:
        _ = kwargs


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

    def iter(
        self, prompt: str | None, *, deps: object, message_history: object
    ) -> _FakeAgentRun:
        _ = (deps, message_history)
        self.prompts.append(prompt)
        return _FakeAgentRun()


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
            output_tokens=888_888,
            total_tokens=1_888_887,
            requests=9,
            tool_calls=5,
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
        self, prompt: str | None, *, deps: object, message_history: object
    ) -> _FakeAgentRunWithNode:
        _ = (prompt, deps, message_history)
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
        self._usage_obj.output_tokens = 19
        self._usage_obj.total_tokens = 149
        self._usage_obj.requests = 1
        self._usage_obj.tool_calls = 5
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
            output_tokens=10,
            total_tokens=110,
            requests=0,
            tool_calls=0,
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
        self, prompt: str | None, *, deps: object, message_history: object
    ) -> _FakeAgentRunWithMutableUsage:
        _ = (prompt, deps, message_history)
        return _FakeAgentRunWithMutableUsage()


def _build_provider(
    message_repo: _FakeMessageRepo,
    hub: _FakeRunEventHub,
    *,
    allowed_tools: tuple[str, ...] = (),
    allowed_skills: tuple[str, ...] = (),
    skill_registry: object | None = None,
) -> OpenAICompatibleProvider:
    registry = (
        cast(SkillRegistry, skill_registry)
        if skill_registry is not None
        else cast(SkillRegistry, object())
    )
    config = ModelEndpointConfig(
        model="gpt-test",
        base_url="http://localhost",
        api_key="test-key",
    )
    return OpenAICompatibleProvider(
        config,
        task_repo=cast(TaskRepository, cast(object, _FakeTaskRepository())),
        instance_pool=cast(InstancePool, cast(object, _FakeInstancePool())),
        shared_store=cast(SharedStore, cast(object, _FakeSharedStore())),
        event_bus=cast(EventLog, cast(object, _FakeEventLog())),
        injection_manager=cast(
            RunInjectionManager, cast(object, _FakeInjectionManager())
        ),
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        agent_repo=cast(AgentInstanceRepository, object()),
        workspace_root=Path("."),
        tool_registry=cast(ToolRegistry, object()),
        mcp_registry=cast(McpRegistry, object()),
        skill_registry=registry,
        allowed_tools=allowed_tools,
        allowed_mcp_servers=(),
        allowed_skills=allowed_skills,
        message_repo=cast(MessageRepository, cast(object, message_repo)),
        role_registry=cast(RoleRegistry, object()),
        task_execution_service=cast(TaskExecutionService, object()),
        run_control_manager=cast(
            RunControlManager,
            cast(object, _FakeRunControlManager()),
        ),
        tool_approval_manager=cast(ToolApprovalManager, object()),
        tool_approval_policy=cast(ToolApprovalPolicy, object()),
    )


@pytest.mark.asyncio
async def test_generate_passes_current_turn_prompt_even_with_existing_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent = _FakeAgent()
    fake_message_repo = _FakeMessageRepo()
    fake_hub = _FakeRunEventHub()
    provider = _build_provider(fake_message_repo, fake_hub)

    monkeypatch.setattr(
        llm_module,
        "build_collaboration_agent",
        lambda **kwargs: fake_agent,
    )

    request = LLMRequest(
        run_id="run-2",
        trace_id="run-2",
        task_id="task-2",
        session_id="session-2",
        instance_id="inst-2",
        role_id="coordinator_agent",
        system_prompt="system",
        user_prompt="current turn",
    )

    _ = await provider.generate(request)

    assert fake_agent.prompts == ["current turn"]


@pytest.mark.asyncio
async def test_generate_enables_continuous_stream_usage_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent = _FakeAgent()
    fake_message_repo = _FakeMessageRepo()
    fake_hub = _FakeRunEventHub()
    provider = _build_provider(fake_message_repo, fake_hub)
    captured_kwargs: dict[str, object] = {}

    def _fake_builder(**kwargs: object) -> _FakeAgent:
        captured_kwargs.update(kwargs)
        return fake_agent

    monkeypatch.setattr(llm_module, "build_collaboration_agent", _fake_builder)

    request = LLMRequest(
        run_id="run-3",
        trace_id="run-3",
        task_id="task-3",
        session_id="session-3",
        instance_id="inst-3",
        role_id="coordinator_agent",
        system_prompt="system",
        user_prompt="current turn",
    )

    _ = await provider.generate(request)

    settings_obj = captured_kwargs.get("model_settings")
    assert isinstance(settings_obj, dict)
    assert settings_obj.get("openai_continuous_usage_stats") is True
    assert "temperature" not in settings_obj
    assert "top_p" not in settings_obj
    assert "max_tokens" not in settings_obj


@pytest.mark.asyncio
async def test_generate_builds_augmented_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent = _FakeAgent()
    fake_message_repo = _FakeMessageRepo()
    fake_hub = _FakeRunEventHub()
    fake_skill_registry = _FakeSkillRegistry(
        (
            PromptSkillInstruction(
                name="time",
                instructions="Normalize all times to UTC.",
            ),
        )
    )
    provider = _build_provider(
        fake_message_repo,
        fake_hub,
        allowed_tools=("dispatch_tasks",),
        allowed_skills=("time",),
        skill_registry=fake_skill_registry,
    )
    captured_kwargs: dict[str, object] = {}

    def _fake_builder(**kwargs: object) -> _FakeAgent:
        captured_kwargs.update(kwargs)
        return fake_agent

    monkeypatch.setattr(llm_module, "build_collaboration_agent", _fake_builder)

    request = LLMRequest(
        run_id="run-augment",
        trace_id="run-augment",
        task_id="task-augment",
        session_id="session-augment",
        instance_id="inst-augment",
        role_id="coordinator_agent",
        system_prompt="## Role\nBase system prompt.",
        user_prompt="## Objective\ncurrent turn",
    )

    _ = await provider.generate(request)

    system_prompt_obj = captured_kwargs.get("system_prompt")
    assert isinstance(system_prompt_obj, str)
    assert "## Tool Rules" in system_prompt_obj
    assert "dispatch_tasks" in system_prompt_obj
    assert "## Skill Instructions" in system_prompt_obj
    assert "### Skill: time" in system_prompt_obj
    assert "Normalize all times to UTC." in system_prompt_obj
    assert fake_skill_registry.requested == [("time",)]


@pytest.mark.asyncio
async def test_generate_token_usage_tracks_request_level_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_message_repo = _FakeMessageRepo()
    fake_hub = _FakeRunEventHub()
    provider = _build_provider(fake_message_repo, fake_hub)
    usage_after_request = SimpleNamespace(
        input_tokens=130,
        output_tokens=19,
        total_tokens=149,
        requests=1,
        tool_calls=0,
    )
    fake_node = _FakeModelRequestNode(usage_after_request)
    fake_agent = _FakeAgentWithNode(fake_node)

    monkeypatch.setattr(llm_module, "ModelRequestNode", _FakeModelRequestNode)
    monkeypatch.setattr(
        llm_module,
        "build_collaboration_agent",
        lambda **kwargs: fake_agent,
    )

    request = LLMRequest(
        run_id="run-4",
        trace_id="run-4",
        task_id="task-4",
        session_id="session-4",
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
    assert payload["output_tokens"] == 9
    assert payload["total_tokens"] == 39
    assert payload["requests"] == 1
    assert payload["tool_calls"] == 5


@pytest.mark.asyncio
async def test_generate_token_usage_delta_works_with_mutated_usage_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_message_repo = _FakeMessageRepo()
    fake_hub = _FakeRunEventHub()
    provider = _build_provider(fake_message_repo, fake_hub)
    fake_agent = _FakeAgentWithMutableUsageNode()

    monkeypatch.setattr(
        llm_module, "ModelRequestNode", _FakeModelRequestNodeMutatesUsage
    )
    monkeypatch.setattr(
        llm_module,
        "build_collaboration_agent",
        lambda **kwargs: fake_agent,
    )

    request = LLMRequest(
        run_id="run-5",
        trace_id="run-5",
        task_id="task-5",
        session_id="session-5",
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
    assert payload["output_tokens"] == 9
    assert payload["total_tokens"] == 39
    assert payload["requests"] == 1
    assert payload["tool_calls"] == 5
