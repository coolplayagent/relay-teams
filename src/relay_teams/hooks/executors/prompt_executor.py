from __future__ import annotations

from collections.abc import Callable

from pydantic_ai import Agent, ModelRequestNode
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.profiles.openai import OpenAIModelProfile

from relay_teams.hooks.hook_event_models import HookEventInput
from relay_teams.hooks.hook_models import HookDecision, HookHandlerConfig
from relay_teams.net.llm_client import build_llm_http_client
from relay_teams.providers.llm_retry import run_with_llm_retry
from relay_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
from relay_teams.providers.openai_model_profiles import (
    resolve_openai_chat_model_profile,
)
from relay_teams.providers.openai_support import build_openai_provider


class PromptHookExecutor:
    def __init__(
        self,
        *,
        resolve_model_config: Callable[
            [str | None], tuple[ModelEndpointConfig | None, str | None]
        ],
        retry_config: LlmRetryConfig,
    ) -> None:
        self._resolve_model_config = resolve_model_config
        self._retry_config = retry_config

    async def execute(
        self,
        *,
        handler: HookHandlerConfig,
        event_input: HookEventInput,
    ) -> HookDecision:
        prompt_template = str(handler.prompt or "").strip()
        if not prompt_template:
            raise ValueError("Prompt hook requires a prompt")
        config, _ = self._resolve_model_config(handler.model)
        if config is None:
            raise RuntimeError("Prompt hook could not resolve a model profile")
        agent = Agent[None, HookDecision](
            model=_build_model(config),
            output_type=HookDecision,
            instructions=(
                "Evaluate the runtime hook event and return only a valid HookDecision "
                "object. Use the narrowest decision that fits the event."
            ),
            model_settings=_model_settings(config),
            retries=2,
        )
        arguments_json = event_input.model_dump_json()
        prompt = prompt_template.replace("$ARGUMENTS", arguments_json)
        return await run_with_llm_retry(
            operation=lambda: _run_streaming_prompt(agent=agent, prompt=prompt),
            config=self._retry_config,
            is_retry_allowed=lambda: True,
            on_retry_scheduled=lambda _schedule: None,
        )


async def _run_streaming_prompt(
    *,
    agent: Agent[None, HookDecision],
    prompt: str,
) -> HookDecision:
    async with agent.iter(prompt) as agent_run:
        async for node in agent_run:
            if not isinstance(node, ModelRequestNode):
                continue
            async with node.stream(agent_run.ctx) as stream:
                async for _event in stream:
                    pass
        if agent_run.result is None:
            raise RuntimeError("Prompt hook evaluation did not complete")
        return agent_run.result.output


def _build_model(config: ModelEndpointConfig) -> OpenAIChatModel:
    profile: OpenAIModelProfile | None = resolve_openai_chat_model_profile(
        base_url=config.base_url,
        model_name=config.model,
    )
    return OpenAIChatModel(
        config.model,
        provider=build_openai_provider(
            config=config,
            http_client=build_llm_http_client(
                connect_timeout_seconds=config.connect_timeout_seconds,
                ssl_verify=config.ssl_verify,
            ),
        ),
        profile=profile,
    )


def _model_settings(config: ModelEndpointConfig) -> OpenAIChatModelSettings:
    configured_max_tokens = config.sampling.max_tokens
    max_tokens = (
        600 if configured_max_tokens is None else min(configured_max_tokens, 600)
    )
    return {
        "temperature": min(config.sampling.temperature, 0.2),
        "top_p": config.sampling.top_p,
        "max_tokens": max_tokens,
        "openai_continuous_usage_stats": True,
        "extra_body": {"response_format": {"type": "json_object"}},
    }
