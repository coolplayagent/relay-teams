# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import cast

import pytest

from agent_teams.agents.execution import agent_builder as coordination_agent
from agent_teams.tools.registry import ToolRegistry


class _FakeOpenAIProvider:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeOpenAIChatModel:
    def __init__(self, model_name: str, provider: object) -> None:
        self.model_name = model_name
        self.provider = provider


class _FakeAgent:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeToolRegistry:
    def __init__(self) -> None:
        self.required: tuple[str, ...] | None = None

    def require(self, allowed_tools: tuple[str, ...]):
        self.required = allowed_tools
        return ()


def test_build_coordination_agent_passes_proxy_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel_client = object()
    fake_tool_registry = _FakeToolRegistry()

    def _fake_build_llm_http_client(
        *,
        connect_timeout_seconds: float,
        ssl_verify: bool | None = None,
    ) -> object:
        captured["connect_timeout_seconds"] = connect_timeout_seconds
        captured["ssl_verify"] = ssl_verify
        return sentinel_client

    def _fake_openai_provider(**kwargs: object) -> _FakeOpenAIProvider:
        provider = _FakeOpenAIProvider(**kwargs)
        captured["provider"] = provider
        return provider

    def _fake_openai_chat_model(
        model_name: str,
        provider: object,
        profile: object | None = None,
    ) -> _FakeOpenAIChatModel:
        model = _FakeOpenAIChatModel(model_name, provider)
        captured["model"] = model
        captured["profile"] = profile
        return model

    def _fake_agent(**kwargs: object) -> _FakeAgent:
        agent = _FakeAgent(**kwargs)
        captured["agent"] = agent
        return agent

    monkeypatch.setattr(
        coordination_agent,
        "build_llm_http_client",
        _fake_build_llm_http_client,
    )
    monkeypatch.setattr(
        coordination_agent,
        "OpenAIProvider",
        _fake_openai_provider,
    )
    monkeypatch.setattr(
        coordination_agent,
        "OpenAIChatModel",
        _fake_openai_chat_model,
    )
    monkeypatch.setattr(
        coordination_agent,
        "Agent",
        _fake_agent,
    )

    agent = coordination_agent.build_coordination_agent(
        model_name="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        system_prompt="system",
        allowed_tools=("dispatch_task",),
        connect_timeout_seconds=22.0,
        tool_registry=cast(ToolRegistry, fake_tool_registry),
    )

    provider = captured["provider"]
    assert isinstance(provider, _FakeOpenAIProvider)
    assert provider.kwargs["base_url"] == "https://example.test/v1"
    assert provider.kwargs["api_key"] == "secret"
    assert provider.kwargs["http_client"] is sentinel_client
    assert captured["connect_timeout_seconds"] == 22.0
    assert captured["ssl_verify"] is None
    assert fake_tool_registry.required == ("dispatch_task",)
    assert agent is captured["agent"]
    built_agent = cast(_FakeAgent, captured["agent"])
    assert built_agent.kwargs["instructions"] == "system"
    assert "system_prompt" not in built_agent.kwargs
