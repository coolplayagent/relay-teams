# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

import agent_teams.providers.llm as llm_module
from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.coordination.task_execution_service import TaskExecutionService
from agent_teams.mcp.registry import McpRegistry
from agent_teams.prompting.provider_augment import PromptSkillInstruction
from agent_teams.providers.llm import LLMRequest, OpenAICompatibleProvider
from agent_teams.providers.model_config import ModelEndpointConfig
from agent_teams.roles.models import RoleDefinition
from agent_teams.roles.registry import RoleRegistry
from agent_teams.runs.control import RunControlManager
from agent_teams.runs.enums import RunEventType
from agent_teams.runs.event_stream import RunEventHub
from agent_teams.runs.injection_queue import RunInjectionManager
from agent_teams.runs.models import RunEvent
from agent_teams.skills.registry import SkillRegistry
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.run_runtime_repo import RunRuntimeRepository
from agent_teams.state.shared_state_repo import SharedStateRepository
from agent_teams.state.task_repo import TaskRepository
from agent_teams.state.workflow_graph_repo import WorkflowGraphRepository
from agent_teams.tools.registry import ToolRegistry
from agent_teams.tools.runtime import ToolApprovalManager, ToolApprovalPolicy
from agent_teams.workspace import (
    WorkspaceManager,
    build_conversation_id,
    build_workspace_id,
)


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

    def iter(
        self, prompt: str | None, *, deps: object, message_history: object
    ) -> _FakeAgentRun:
        _ = (deps, message_history)
        self.prompts.append(prompt)
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
        self, prompt: str | None, *, deps: object, message_history: object
    ) -> _ScriptedAgentRun:
        _ = deps
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
            version="1",
            tools=(),
            system_prompt="Coordinate work.",
        )
    )
    role_registry.register(
        RoleDefinition(
            role_id="time",
            name="time",
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
        instance_pool=cast(InstancePool, cast(object, InstancePool())),
        shared_store=shared_store,
        event_bus=EventLog(db_path),
        injection_manager=cast(
            RunInjectionManager, cast(object, _FakeInjectionManager())
        ),
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        agent_repo=AgentInstanceRepository(db_path),
        workflow_graph_repo=WorkflowGraphRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        workspace_manager=WorkspaceManager(
            project_root=Path("."), shared_store=shared_store
        ),
        tool_registry=cast(ToolRegistry, object()),
        mcp_registry=cast(McpRegistry, object()),
        skill_registry=registry,
        allowed_tools=allowed_tools,
        allowed_mcp_servers=(),
        allowed_skills=allowed_skills,
        message_repo=message_repo,
        role_registry=role_registry,
        task_execution_service=cast(TaskExecutionService, object()),
        run_control_manager=cast(
            RunControlManager,
            cast(object, run_control_manager or _FakeRunControlManager()),
        ),
        tool_approval_manager=cast(ToolApprovalManager, object()),
        tool_approval_policy=cast(ToolApprovalPolicy, object()),
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
        workspace_id=build_workspace_id(session_id),
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
        instance_id="inst-2",
        role_id="coordinator_agent",
        system_prompt="system",
        user_prompt="current turn",
    )

    _ = await provider.generate(request)

    history = message_repo.get_history("inst-2")
    assert fake_agent.prompts == [None]
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
        workspace_id=build_workspace_id("session-pending-tool"),
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
                        tool_name="create_workflow_graph",
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
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    fake_hub = _FakeRunEventHub()
    fake_skill_registry = _FakeSkillRegistry(
        (
            PromptSkillInstruction(
                name="time",
                instructions="Normalize all times to UTC.",
            ),
        )
    )
    provider, _ = _build_provider(
        tmp_path / "prompt_aug.db",
        fake_hub,
        allowed_tools=("dispatch_tasks",),
        allowed_skills=("time",),
        skill_registry=fake_skill_registry,
    )
    captured_kwargs: dict[str, object] = {}

    def _fake_builder(**kwargs: object) -> _FakeAgent:
        captured_kwargs.update(kwargs)
        return fake_agent

    monkeypatch.setattr(llm_module, "build_coordination_agent", _fake_builder)

    request = LLMRequest(
        run_id="run-augment",
        trace_id="run-augment",
        task_id="task-augment",
        session_id="session-augment",
        instance_id="inst-augment",
        role_id="coordinator_agent",
        system_prompt="## Role\nBase system prompt.",
        user_prompt="current turn",
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
    tmp_path: Path,
) -> None:
    fake_hub = _FakeRunEventHub()
    provider, _ = _build_provider(tmp_path / "token_usage.db", fake_hub)
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
        "build_coordination_agent",
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
