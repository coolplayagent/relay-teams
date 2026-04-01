# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import cast

import pytest

from agent_teams.agents.execution import (
    coordination_agent_builder as coordination_agent,
)
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.tools.registry import ToolRegistry, ToolResolutionContext


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
        self.calls: list[
            tuple[tuple[str, ...], ToolResolutionContext | None, bool, str | None]
        ] = []

    def resolve_known(
        self,
        allowed_tools: tuple[str, ...],
        *,
        context: ToolResolutionContext | None = None,
        strict: bool = True,
        consumer: str | None = None,
    ) -> tuple[str, ...]:
        self.calls.append((allowed_tools, context, strict, consumer))
        return tuple(name for name in allowed_tools if name != "missing_tool")

    def require(self, allowed_tools: tuple[str, ...]):
        self.required = allowed_tools
        return ()


class _FakeSkillRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], bool, str | None]] = []

    def resolve_known(
        self,
        skill_names: tuple[str, ...],
        *,
        strict: bool = True,
        consumer: str | None = None,
    ) -> tuple[str, ...]:
        self.calls.append((skill_names, strict, consumer))
        return ("time",) if "time" in skill_names else ()

    def get_toolset_tools(self, skill_names: tuple[str, ...]) -> list[object]:
        return [object()] if skill_names else []


class _FakeMcpRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], bool, str | None]] = []

    def resolve_server_names(
        self,
        server_names: tuple[str, ...],
        *,
        strict: bool = True,
        consumer: str | None = None,
    ) -> tuple[str, ...]:
        self.calls.append((server_names, strict, consumer))
        return tuple(name for name in server_names if name != "missing_server")

    def get_toolsets(self, server_names: tuple[str, ...]) -> tuple[object, ...]:
        return tuple(object() for _ in server_names)


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
        "build_openai_provider_for_endpoint",
        lambda **kwargs: _fake_openai_provider(**kwargs),
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
    assert provider.kwargs["headers"] == ()
    assert provider.kwargs["http_client"] is sentinel_client
    assert captured["connect_timeout_seconds"] == 22.0
    assert captured["ssl_verify"] is None
    assert fake_tool_registry.required == ("dispatch_task",)
    assert fake_tool_registry.calls == [
        (
            ("dispatch_task",),
            None,
            False,
            "agents.execution.coordination_agent_builder",
        )
    ]
    assert agent is captured["agent"]
    built_agent = cast(_FakeAgent, captured["agent"])
    assert built_agent.kwargs["instructions"] == "system"
    assert "system_prompt" not in built_agent.kwargs


def test_build_coordination_agent_ignores_unknown_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    fake_tool_registry = _FakeToolRegistry()
    fake_skill_registry = _FakeSkillRegistry()

    monkeypatch.setattr(
        coordination_agent,
        "build_llm_http_client",
        lambda **_: object(),
    )
    monkeypatch.setattr(
        coordination_agent,
        "build_openai_provider_for_endpoint",
        lambda **kwargs: _FakeOpenAIProvider(**kwargs),
    )
    monkeypatch.setattr(
        coordination_agent,
        "OpenAIChatModel",
        lambda model_name, provider, profile=None: _FakeOpenAIChatModel(
            model_name, provider
        ),
    )

    def _fake_agent(**kwargs: object) -> _FakeAgent:
        agent = _FakeAgent(**kwargs)
        captured["agent"] = agent
        return agent

    monkeypatch.setattr(coordination_agent, "Agent", _fake_agent)

    coordination_agent.build_coordination_agent(
        model_name="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        system_prompt="system",
        allowed_tools=(),
        allowed_skills=("time", "missing_skill"),
        tool_registry=cast(ToolRegistry, fake_tool_registry),
        skill_registry=cast(SkillRegistry, fake_skill_registry),
    )

    built_agent = cast(_FakeAgent, captured["agent"])
    assert len(cast(list[object], built_agent.kwargs["tools"])) == 1
    assert fake_skill_registry.calls == [
        (
            ("time", "missing_skill"),
            False,
            "agents.execution.coordination_agent_builder",
        )
    ]


def test_build_coordination_agent_ignores_unknown_tools_and_mcp_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    fake_tool_registry = _FakeToolRegistry()
    fake_mcp_registry = _FakeMcpRegistry()

    monkeypatch.setattr(
        coordination_agent, "build_llm_http_client", lambda **_: object()
    )
    monkeypatch.setattr(
        coordination_agent,
        "build_openai_provider_for_endpoint",
        lambda **kwargs: _FakeOpenAIProvider(**kwargs),
    )
    monkeypatch.setattr(
        coordination_agent,
        "OpenAIChatModel",
        lambda model_name, provider, profile=None: _FakeOpenAIChatModel(
            model_name, provider
        ),
    )

    def _fake_agent(**kwargs: object) -> _FakeAgent:
        agent = _FakeAgent(**kwargs)
        captured["agent"] = agent
        return agent

    monkeypatch.setattr(coordination_agent, "Agent", _fake_agent)

    coordination_agent.build_coordination_agent(
        model_name="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        system_prompt="system",
        allowed_tools=("dispatch_task", "missing_tool"),
        allowed_mcp_servers=("docs", "missing_server"),
        tool_registry=cast(ToolRegistry, fake_tool_registry),
        mcp_registry=cast(McpRegistry, fake_mcp_registry),
    )

    built_agent = cast(_FakeAgent, captured["agent"])
    assert len(cast(list[object], built_agent.kwargs["toolsets"])) == 1
    assert fake_tool_registry.required == ("dispatch_task",)
    assert fake_mcp_registry.calls == [
        (
            ("docs", "missing_server"),
            False,
            "agents.execution.coordination_agent_builder",
        )
    ]
