# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import httpx
import pytest
from openai import APIError

import agent_teams.agents.execution.subagent_reflection as reflection_module
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.agents.execution.subagent_reflection import SubagentReflectionService
from agent_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.roles.role_models import RoleDefinition
from pydantic_ai.messages import ModelRequest, UserPromptPart


class _FakeRoleMemoryService:
    def build_injected_memory(self, *, role_id: str, workspace_id: str) -> str:
        _ = (role_id, workspace_id)
        return ""


class _FakeAgent:
    attempts = 0
    run_calls = 0

    def __class_getitem__(cls, _item):
        return cls

    def __init__(self, *args, **kwargs) -> None:
        _ = (args, kwargs)

    async def run(self, prompt: str) -> object:
        _ = prompt
        _FakeAgent.run_calls += 1
        raise AssertionError(
            "reflection rewrite should not use non-streaming agent.run"
        )

    @asynccontextmanager
    async def iter(self, prompt: str) -> AsyncIterator[_FakeAgentRun]:
        _ = prompt
        yield _FakeAgentRun()


class _FakeAgentRun:
    def __init__(self) -> None:
        self.ctx = object()
        self.result = type("_Result", (), {"output": "- stable memory"})()
        self._nodes = [_FakeModelRequestNode()]

    def __aiter__(self) -> _FakeAgentRun:
        return self

    async def __anext__(self) -> _FakeModelRequestNode:
        if not self._nodes:
            raise StopAsyncIteration
        return self._nodes.pop(0)


class _FakeStream:
    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> object:
        _FakeAgent.attempts += 1
        if _FakeAgent.attempts < 3:
            raise APIError(
                "provider error",
                request=httpx.Request(
                    "POST", "https://example.test/v1/chat/completions"
                ),
                body={"error": {"code": "2062", "message": "busy"}},
            )
        raise StopAsyncIteration


class _FakeModelRequestNode:
    @asynccontextmanager
    async def stream(self, _ctx: object) -> AsyncIterator[_FakeStream]:
        yield _FakeStream()


@pytest.mark.asyncio
async def test_rewrite_reflection_summary_retries_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _FakeAgent.attempts = 0
    _FakeAgent.run_calls = 0
    service = SubagentReflectionService(
        config=ModelEndpointConfig(
            model="gpt-test",
            base_url="https://example.test/v1",
            api_key="secret",
        ),
        retry_config=LlmRetryConfig(jitter=False, max_retries=5, initial_delay_ms=1),
        message_repo=MessageRepository(tmp_path / "reflection.db"),
        role_memory_service=cast(RoleMemoryService, _FakeRoleMemoryService()),
    )
    monkeypatch.setattr(reflection_module, "Agent", _FakeAgent)
    monkeypatch.setattr(reflection_module, "ModelRequestNode", _FakeModelRequestNode)
    monkeypatch.setattr(service, "_build_model", lambda: object())

    summary = await service._rewrite_reflection_summary(
        role=RoleDefinition(
            role_id="researcher",
            name="Researcher",
            description="Researches",
            version="1",
            system_prompt="Research",
        ),
        workspace_id="default",
        source_history=[ModelRequest(parts=[UserPromptPart(content="remember this")])],
        source_char_budget=16000,
    )

    assert summary == "- stable memory"
    assert _FakeAgent.attempts == 3
    assert _FakeAgent.run_calls == 0
