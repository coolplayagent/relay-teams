from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic_ai import Agent

import relay_teams.hooks.executors.prompt_executor as prompt_module
from relay_teams.hooks.executors.prompt_executor import (
    PromptHookExecutor,
    _build_model,
    _model_settings,
    _run_streaming_prompt,
)
from relay_teams.hooks.hook_event_models import UserPromptSubmitInput
from relay_teams.hooks.hook_models import (
    HookDecision,
    HookDecisionType,
    HookEventName,
    HookHandlerConfig,
    HookHandlerType,
)
from relay_teams.providers.llm_retry import LlmRetryConfig
from relay_teams.providers.model_config import ModelEndpointConfig, SamplingConfig


class _FakeModelRequestNode:
    def __init__(self) -> None:
        self.streamed = False

    @asynccontextmanager
    async def stream(self, _ctx: object) -> AsyncIterator[object]:
        self.streamed = True
        yield _EmptyAsyncStream()


class _EmptyAsyncStream:
    def __aiter__(self) -> _EmptyAsyncStream:
        return self

    async def __anext__(self) -> object:
        raise StopAsyncIteration


class _EventfulAsyncStream:
    def __init__(self) -> None:
        self._emitted = False

    def __aiter__(self) -> _EventfulAsyncStream:
        return self

    async def __anext__(self) -> object:
        if self._emitted:
            raise StopAsyncIteration
        self._emitted = True
        return object()


class _EventfulModelRequestNode(_FakeModelRequestNode):
    @asynccontextmanager
    async def stream(self, _ctx: object) -> AsyncIterator[object]:
        self.streamed = True
        yield _EventfulAsyncStream()


class _FakeAgentRun:
    def __init__(self, *, output: HookDecision | None) -> None:
        self.ctx = object()
        self.result = SimpleNamespace(output=output) if output is not None else None
        self._nodes = (_FakeModelRequestNode(),)

    def __aiter__(self) -> _FakeAgentRun:
        self._index = 0
        return self

    async def __anext__(self) -> _FakeModelRequestNode:
        if self._index >= len(self._nodes):
            raise StopAsyncIteration
        node = self._nodes[self._index]
        self._index += 1
        return node


class _MixedAgentRun:
    def __init__(self) -> None:
        self.ctx = object()
        self.result = SimpleNamespace(
            output=HookDecision(decision=HookDecisionType.ALLOW)
        )
        self._nodes: list[object] = [object(), _EventfulModelRequestNode()]

    def __aiter__(self) -> _MixedAgentRun:
        self._index = 0
        return self

    async def __anext__(self) -> object:
        if self._index >= len(self._nodes):
            raise StopAsyncIteration
        node = self._nodes[self._index]
        self._index += 1
        return node


class _MixedAgent:
    def __class_getitem__(cls, _item: object) -> type[_MixedAgent]:
        return cls

    @asynccontextmanager
    async def iter(self, prompt: str) -> AsyncIterator[_MixedAgentRun]:
        _ = prompt
        yield _MixedAgentRun()


class _FakeAgent:
    last_init: dict[str, object] | None = None
    next_output: HookDecision | None = HookDecision(decision=HookDecisionType.ALLOW)
    prompts: list[str] = []

    def __class_getitem__(cls, _item: object) -> type[_FakeAgent]:
        return cls

    def __init__(self, **kwargs: object) -> None:
        type(self).last_init = dict(kwargs)

    @asynccontextmanager
    async def iter(self, prompt: str) -> AsyncIterator[_FakeAgentRun]:
        type(self).prompts.append(prompt)
        yield _FakeAgentRun(output=type(self).next_output)


def _config(
    *, max_tokens: int | None = None, temperature: float = 0.7
) -> ModelEndpointConfig:
    return ModelEndpointConfig(
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        sampling=SamplingConfig(
            temperature=temperature,
            max_tokens=max_tokens,
        ),
    )


def _event_input() -> UserPromptSubmitInput:
    return UserPromptSubmitInput(
        event_name=HookEventName.USER_PROMPT_SUBMIT,
        session_id="session-1",
        run_id="run-1",
        trace_id="run-1",
        user_prompt="hello",
    )


@pytest.mark.asyncio
async def test_prompt_executor_runs_llm_prompt_and_returns_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run_with_llm_retry(**kwargs: object) -> HookDecision:
        operation = cast(Callable[[], Awaitable[HookDecision]], kwargs["operation"])
        assert callable(operation)
        return await operation()

    monkeypatch.setattr(prompt_module, "Agent", _FakeAgent)
    monkeypatch.setattr(prompt_module, "ModelRequestNode", _FakeModelRequestNode)
    monkeypatch.setattr(
        prompt_module, "_build_model", lambda config: ("model", config.model)
    )
    monkeypatch.setattr(prompt_module, "run_with_llm_retry", _run_with_llm_retry)
    _FakeAgent.last_init = None
    _FakeAgent.prompts = []
    _FakeAgent.next_output = HookDecision(
        decision=HookDecisionType.ALLOW,
        reason="ok",
    )

    executor = PromptHookExecutor(
        resolve_model_config=lambda model: (_config(), model),
        retry_config=LlmRetryConfig(),
    )

    result = await executor.execute(
        handler=HookHandlerConfig(
            type=HookHandlerType.PROMPT,
            prompt="Review this event: $ARGUMENTS",
            model="default",
        ),
        event_input=_event_input(),
    )

    assert result.decision == HookDecisionType.ALLOW
    assert _FakeAgent.last_init is not None
    assert _FakeAgent.prompts
    assert "Review this event:" in _FakeAgent.prompts[0]
    assert '"user_prompt":"hello"' in _FakeAgent.prompts[0]


@pytest.mark.asyncio
async def test_prompt_executor_requires_prompt() -> None:
    executor = PromptHookExecutor(
        resolve_model_config=lambda model: (_config(), model),
        retry_config=LlmRetryConfig(),
    )

    with pytest.raises(ValueError, match="requires a prompt"):
        await executor.execute(
            handler=HookHandlerConfig(type=HookHandlerType.PROMPT),
            event_input=_event_input(),
        )


@pytest.mark.asyncio
async def test_prompt_executor_requires_resolved_model_profile() -> None:
    executor = PromptHookExecutor(
        resolve_model_config=lambda model: (None, model),
        retry_config=LlmRetryConfig(),
    )

    with pytest.raises(RuntimeError, match="could not resolve"):
        await executor.execute(
            handler=HookHandlerConfig(
                type=HookHandlerType.PROMPT,
                prompt="Review $ARGUMENTS",
            ),
            event_input=_event_input(),
        )


@pytest.mark.asyncio
async def test_run_streaming_prompt_raises_when_result_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prompt_module, "ModelRequestNode", _FakeModelRequestNode)
    _FakeAgent.next_output = None
    agent = _FakeAgent()

    with pytest.raises(RuntimeError, match="did not complete"):
        await _run_streaming_prompt(
            agent=cast(Agent[None, HookDecision], agent),
            prompt="Review",
        )


@pytest.mark.asyncio
async def test_run_streaming_prompt_ignores_non_model_nodes_and_drains_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prompt_module, "ModelRequestNode", _FakeModelRequestNode)

    result = await _run_streaming_prompt(
        agent=cast(Agent[None, HookDecision], _MixedAgent()),
        prompt="Review",
    )

    assert result.decision == HookDecisionType.ALLOW


def test_build_model_wires_provider_http_client_and_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeOpenAIChatModel:
        def __init__(
            self, model_name: str, *, provider: object, profile: object
        ) -> None:
            captured["model_name"] = model_name
            captured["provider"] = provider
            captured["profile"] = profile

    monkeypatch.setattr(
        prompt_module,
        "resolve_openai_chat_model_profile",
        lambda **kwargs: ("profile", kwargs),
    )
    monkeypatch.setattr(
        prompt_module,
        "build_llm_http_client",
        lambda **kwargs: ("client", kwargs),
    )
    monkeypatch.setattr(
        prompt_module,
        "build_openai_provider",
        lambda **kwargs: ("provider", kwargs),
    )
    monkeypatch.setattr(prompt_module, "OpenAIChatModel", _FakeOpenAIChatModel)

    _ = _build_model(_config())

    assert captured["model_name"] == "gpt-test"
    provider = cast(tuple[str, dict[str, object]], captured["provider"])
    profile = cast(tuple[str, dict[str, object]], captured["profile"])
    assert provider[0] == "provider"
    assert profile[0] == "profile"


def test_model_settings_caps_max_tokens_and_temperature() -> None:
    settings = _model_settings(_config(max_tokens=1200, temperature=0.9))

    assert settings.get("max_tokens") == 600
    assert settings.get("temperature") == 0.2
    assert settings.get("openai_continuous_usage_stats") is True
