# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import cast

import pytest

import relay_teams.agents.execution.coordination_agent_builder as coordination_agent
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.roles.role_models import RoleDefinition, RoleMode
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.tools.registry import ToolRegistry, ToolResolutionContext
from relay_teams.tools.workspace_tools import register_spawn_subagent


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
        self.tools: dict[str, object] = {}
        self.tool_descriptions: dict[str, str] = {}

    def tool(self, *, description: str):
        def _decorator(func: object) -> object:
            name = getattr(func, "__name__", "")
            if isinstance(name, str) and name:
                self.tools[name] = func
                self.tool_descriptions[name] = description
            return func

        return _decorator


class _FakeToolRegistry:
    def __init__(self, registers: tuple[object, ...] = ()) -> None:
        self.required: tuple[str, ...] | None = None
        self.calls: list[
            tuple[tuple[str, ...], ToolResolutionContext | None, bool, str | None]
        ] = []
        self.registers = registers

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
        return self.registers


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


class _PartiallyFailingMcpRegistry(_FakeMcpRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.toolset_calls: list[tuple[str, ...]] = []

    def get_toolsets(self, server_names: tuple[str, ...]) -> tuple[object, ...]:
        self.toolset_calls.append(server_names)
        if server_names == ("broken",):
            raise RuntimeError("MCP startup failed")
        return tuple(f"toolset:{name}" for name in server_names)


class _MarkedFailedMcpRegistry(_FakeMcpRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.toolset_calls: list[tuple[str, ...]] = []

    def is_server_runtime_failed(self, name: str) -> bool:
        return name == "broken"

    def get_toolsets(self, server_names: tuple[str, ...]) -> tuple[object, ...]:
        self.toolset_calls.append(server_names)
        if server_names == ("broken",):
            raise AssertionError("failed MCP server should have been skipped")
        return tuple(f"toolset:{name}" for name in server_names)


def test_build_coordination_agent_passes_proxy_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel_client = object()
    fake_tool_registry = _FakeToolRegistry()

    def _fake_build_llm_http_client(
        *,
        connect_timeout_seconds: float,
        cache_scope: str | None = None,
        ssl_verify: bool | None = None,
        merged_env: object | None = None,
    ) -> object:
        captured["connect_timeout_seconds"] = connect_timeout_seconds
        captured["cache_scope"] = cache_scope
        captured["ssl_verify"] = ssl_verify
        captured["merged_env"] = merged_env
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
        allowed_tools=("orch_dispatch_task",),
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
    assert captured["cache_scope"] is None
    assert captured["ssl_verify"] is None
    assert fake_tool_registry.required == ("orch_dispatch_task",)
    assert fake_tool_registry.calls == [
        (
            ("orch_dispatch_task",),
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
        _FakeOpenAIProvider,
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
        allowed_tools=("orch_dispatch_task", "missing_tool"),
        allowed_mcp_servers=("docs", "missing_server"),
        tool_registry=cast(ToolRegistry, fake_tool_registry),
        mcp_registry=cast(McpRegistry, fake_mcp_registry),
    )

    built_agent = cast(_FakeAgent, captured["agent"])
    assert len(cast(list[object], built_agent.kwargs["toolsets"])) == 1
    assert fake_tool_registry.required == ("orch_dispatch_task",)
    assert fake_mcp_registry.calls == [
        (
            ("docs", "missing_server"),
            False,
            "agents.execution.coordination_agent_builder",
        )
    ]


def test_build_coordination_agent_skips_mcp_toolsets_that_fail_to_initialize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    fake_tool_registry = _FakeToolRegistry()
    fake_mcp_registry = _PartiallyFailingMcpRegistry()

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
        allowed_mcp_servers=("docs", "broken"),
        tool_registry=cast(ToolRegistry, fake_tool_registry),
        mcp_registry=cast(McpRegistry, fake_mcp_registry),
    )

    built_agent = cast(_FakeAgent, captured["agent"])
    assert built_agent.kwargs["toolsets"] == ["toolset:docs"]
    assert fake_mcp_registry.toolset_calls == [("docs",), ("broken",)]


def test_build_coordination_agent_skips_mcp_servers_marked_runtime_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    fake_tool_registry = _FakeToolRegistry()
    fake_mcp_registry = _MarkedFailedMcpRegistry()

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
        allowed_mcp_servers=("docs", "broken"),
        tool_registry=cast(ToolRegistry, fake_tool_registry),
        mcp_registry=cast(McpRegistry, fake_mcp_registry),
    )

    built_agent = cast(_FakeAgent, captured["agent"])
    assert built_agent.kwargs["toolsets"] == ["toolset:docs"]
    assert fake_mcp_registry.toolset_calls == [("docs",)]


def test_build_coordination_agent_injects_subagent_capabilities_into_spawn_subagent_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Crafter",
            name="Crafter",
            description="Implements requested changes.",
            version="1",
            tools=("read", "write"),
            mcp_servers=("docs",),
            skills=("time",),
            mode=RoleMode.SUBAGENT,
            model_profile="default",
            system_prompt="You are a crafter.",
        )
    )
    fake_tool_registry = _FakeToolRegistry(registers=(register_spawn_subagent,))

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
        allowed_tools=("spawn_subagent",),
        tool_registry=cast(ToolRegistry, fake_tool_registry),
        role_registry=registry,
    )

    built_agent = cast(_FakeAgent, captured["agent"])
    description = built_agent.tool_descriptions["spawn_subagent"]
    assert "Available Subagent Capabilities" in description
    assert "### Crafter" in description
    assert "- Description: Implements requested changes." in description
    assert "- Tools: read, write" in description
    assert "- MCP Servers: docs" in description
    assert "- Skills: time" in description
