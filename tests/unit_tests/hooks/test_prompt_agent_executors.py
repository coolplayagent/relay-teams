from __future__ import annotations

import pytest

from relay_teams.hooks import HookDecisionType, HookEventName, SessionStartInput
from relay_teams.hooks.executors.agent_executor import AgentHookExecutor
from relay_teams.hooks.executors.prompt_executor import PromptHookExecutor
from relay_teams.hooks.hook_models import HookHandlerConfig, HookHandlerType
from relay_teams.providers.provider_contracts import LLMProvider, LLMRequest
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry


class _CapturingProvider(LLMProvider):
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> str:
        self.requests.append(request)
        return '{"decision":"allow","reason":"ok"}'


class _ProviderFactory:
    def __init__(self) -> None:
        self.roles: list[RoleDefinition] = []
        self.providers: list[_CapturingProvider] = []

    def __call__(self, role: RoleDefinition, session_id: str | None) -> LLMProvider:
        _ = session_id
        self.roles.append(role)
        provider = _CapturingProvider()
        self.providers.append(provider)
        return provider


def _role_registry() -> RoleRegistry:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="Primary",
            version="1.0.0",
            tools=(),
            system_prompt="You are the main agent.",
            model_profile="default",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="Reviewer",
            name="Reviewer",
            description="Reviews decisions.",
            version="1.0.0",
            tools=(),
            system_prompt="Review policies.",
            model_profile="reviewer-default",
        )
    )
    return registry


@pytest.mark.asyncio
async def test_prompt_hook_executor_uses_main_agent_and_model_override() -> None:
    registry = _role_registry()
    factory = _ProviderFactory()
    executor = PromptHookExecutor(
        get_role_registry=lambda: registry,
        get_provider_factory=lambda: factory,
    )

    decision = await executor.execute(
        handler=HookHandlerConfig(
            type=HookHandlerType.PROMPT,
            prompt="Review the session start.",
            model_profile="hook-profile",
        ),
        event_input=SessionStartInput(
            event_name=HookEventName.SESSION_START,
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
        ),
    )

    assert decision.decision == HookDecisionType.ALLOW
    assert factory.roles[0].role_id == "MainAgent"
    assert factory.roles[0].model_profile == "hook-profile"
    request = factory.providers[0].requests[0]
    assert request.runtime_hooks_enabled is False
    assert request.persist_messages is False
    assert request.conversation_id != "writer-conversation"


@pytest.mark.asyncio
async def test_agent_hook_executor_uses_target_role_and_model_override() -> None:
    registry = _role_registry()
    factory = _ProviderFactory()
    executor = AgentHookExecutor(
        get_role_registry=lambda: registry,
        get_provider_factory=lambda: factory,
    )

    decision = await executor.execute(
        handler=HookHandlerConfig(
            type=HookHandlerType.AGENT,
            role_id="Reviewer",
            prompt="Review the session start.",
            model_profile="override-profile",
        ),
        event_input=SessionStartInput(
            event_name=HookEventName.SESSION_START,
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            conversation_id="writer-conversation",
        ),
    )

    assert decision.decision == HookDecisionType.ALLOW
    assert factory.roles[0].role_id == "Reviewer"
    assert factory.roles[0].model_profile == "override-profile"
    request = factory.providers[0].requests[0]
    assert request.runtime_hooks_enabled is False
    assert request.persist_messages is False
    assert request.conversation_id != "writer-conversation"
