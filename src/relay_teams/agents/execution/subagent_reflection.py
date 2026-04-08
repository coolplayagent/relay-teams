# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, ModelRequestNode
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.profiles.openai import OpenAIModelProfile

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.logger import get_logger, log_event
from relay_teams.net.llm_client import build_llm_http_client
from relay_teams.providers.llm_retry import run_with_llm_retry
from relay_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
from relay_teams.providers.openai_model_profiles import (
    resolve_openai_chat_model_profile,
)
from relay_teams.providers.openai_support import build_openai_provider
from relay_teams.roles.memory_models import RoleMemoryRecord
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.roles.role_models import RoleDefinition

LOGGER = get_logger(__name__)


class SubagentCompactionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    should_compact: bool = False
    keep_recent_messages: int = Field(default=12, ge=1)
    source_char_budget: int = Field(default=16000, ge=1000)


class SubagentCompactionStrategy:
    def plan(
        self,
        *,
        history: Sequence[ModelRequest | ModelResponse],
        context_window: int | None,
    ) -> SubagentCompactionPlan:
        raise NotImplementedError


class DefaultSubagentCompactionStrategy(SubagentCompactionStrategy):
    def plan(
        self,
        *,
        history: Sequence[ModelRequest | ModelResponse],
        context_window: int | None,
    ) -> SubagentCompactionPlan:
        message_count = len(history)
        total_chars = sum(_message_text_size(message) for message in history)
        context_chars = max(24000, (context_window or 12000) * 3)
        keep_recent_messages = 12 if message_count >= 20 else 8
        should_compact = (
            message_count > (keep_recent_messages + 8) or total_chars > context_chars
        )
        return SubagentCompactionPlan(
            should_compact=should_compact,
            keep_recent_messages=keep_recent_messages,
            source_char_budget=min(max(context_chars // 2, 12000), 32000),
        )


class SubagentReflectionService:
    def __init__(
        self,
        *,
        config: ModelEndpointConfig,
        retry_config: LlmRetryConfig,
        message_repo: MessageRepository,
        role_memory_service: RoleMemoryService,
        strategy: SubagentCompactionStrategy | None = None,
    ) -> None:
        self._config = config
        self._retry_config = retry_config
        self._message_repo = message_repo
        self._role_memory_service = role_memory_service
        self._strategy = strategy or DefaultSubagentCompactionStrategy()

    async def maybe_compact(
        self,
        *,
        role: RoleDefinition,
        workspace_id: str,
        conversation_id: str,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        if role.memory_profile.enabled is False:
            return list(history)
        plan = self._strategy.plan(
            history=history,
            context_window=self._config.context_window,
        )
        if not plan.should_compact:
            return list(history)
        source_history = list(history[: -plan.keep_recent_messages])
        recent_history = list(history[-plan.keep_recent_messages :])
        if not source_history:
            return list(history)
        summary = await self._rewrite_reflection_summary(
            role=role,
            workspace_id=workspace_id,
            source_history=source_history,
            source_char_budget=plan.source_char_budget,
        )
        if summary:
            _ = self._role_memory_service.update_reflection_memory(
                role_id=role.role_id,
                workspace_id=workspace_id,
                content_markdown=summary,
            )
        self._message_repo.compact_conversation_history(
            conversation_id,
            keep_message_count=len(recent_history),
        )
        log_event(
            LOGGER,
            logging.INFO,
            event="subagent.context.compacted",
            message="Compacted subagent conversation history and refreshed reflection memory",
            payload={
                "role_id": role.role_id,
                "conversation_id": conversation_id,
                "kept_messages": len(recent_history),
                "source_messages": len(source_history),
            },
        )
        return recent_history

    async def refresh_reflection(
        self,
        *,
        role: RoleDefinition,
        workspace_id: str,
        conversation_id: str,
    ) -> RoleMemoryRecord:
        history = self._message_repo.get_history_for_conversation(conversation_id)
        if not history or role.memory_profile.enabled is False:
            return self._role_memory_service.get_reflection_record(
                role_id=role.role_id,
                workspace_id=workspace_id,
            )
        summary = await self._rewrite_reflection_summary(
            role=role,
            workspace_id=workspace_id,
            source_history=history,
            source_char_budget=24000,
        )
        if not summary:
            return self._role_memory_service.get_reflection_record(
                role_id=role.role_id,
                workspace_id=workspace_id,
            )
        record = self._role_memory_service.update_reflection_memory(
            role_id=role.role_id,
            workspace_id=workspace_id,
            content_markdown=summary,
        )
        log_event(
            LOGGER,
            logging.INFO,
            event="subagent.reflection.refreshed",
            message="Refreshed subagent reflection memory",
            payload={
                "role_id": role.role_id,
                "conversation_id": conversation_id,
            },
        )
        return record

    async def _rewrite_reflection_summary(
        self,
        *,
        role: RoleDefinition,
        workspace_id: str,
        source_history: Sequence[ModelRequest | ModelResponse],
        source_char_budget: int,
    ) -> str:
        existing_summary = self._role_memory_service.build_injected_memory(
            role_id=role.role_id,
            workspace_id=workspace_id,
        )
        transcript = _render_transcript(
            source_history,
            max_chars=source_char_budget,
        )
        if not transcript.strip():
            return existing_summary.strip()
        agent = Agent[None, str](
            model=self._build_model(),
            output_type=str,
            instructions=(
                "You maintain long-term reflection memory for a reusable subagent. "
                "Rewrite the memory as concise markdown bullets. Keep only stable strategies, preferences, workflow lessons, and recurring warnings that will help future sessions. "
                "Remove duplicates, stale points, task-specific facts, timestamps, and narration. Output only the final markdown. Keep it under 8 bullets."
            ),
            model_settings=self._model_settings(),
            retries=3,
        )
        prompt = (
            f"Role: {role.role_id}\n\n"
            f"Existing reflection memory:\n{existing_summary or '(empty)'}\n\n"
            f"Transcript to absorb:\n{transcript}"
        )
        result = await run_with_llm_retry(
            operation=lambda: self._run_streaming_reflection(
                agent=agent, prompt=prompt
            ),
            config=self._retry_config,
            is_retry_allowed=lambda: True,
            on_retry_scheduled=lambda _schedule: None,
        )
        return result.strip()

    async def _run_streaming_reflection(
        self,
        *,
        agent: Agent[None, str],
        prompt: str,
    ) -> str:
        async with agent.iter(prompt) as agent_run:
            async for node in agent_run:
                if not isinstance(node, ModelRequestNode):
                    continue
                async with node.stream(agent_run.ctx) as stream:
                    async for _event in stream:
                        pass
            if agent_run.result is None:
                raise RuntimeError("Reflection rewrite did not produce a final result")
            return agent_run.result.output.strip()

    def _build_model(self) -> OpenAIChatModel:
        profile: OpenAIModelProfile | None = resolve_openai_chat_model_profile(
            base_url=self._config.base_url,
            model_name=self._config.model,
        )
        return OpenAIChatModel(
            self._config.model,
            provider=build_openai_provider(
                config=self._config,
                http_client=build_llm_http_client(
                    connect_timeout_seconds=self._config.connect_timeout_seconds,
                    ssl_verify=self._config.ssl_verify,
                ),
            ),
            profile=profile,
        )

    def _model_settings(self) -> OpenAIChatModelSettings:
        configured_max_tokens = self._config.sampling.max_tokens
        max_tokens = (
            400 if configured_max_tokens is None else min(configured_max_tokens, 400)
        )
        return {
            "temperature": min(self._config.sampling.temperature, 0.2),
            "top_p": self._config.sampling.top_p,
            "max_tokens": max_tokens,
            "openai_continuous_usage_stats": True,
        }


def _render_transcript(
    history: Sequence[ModelRequest | ModelResponse],
    *,
    max_chars: int,
) -> str:
    remaining = max_chars
    lines: list[str] = []
    for message in history:
        rendered = _render_message(message)
        if not rendered:
            continue
        clipped = rendered[:remaining].strip()
        if clipped:
            lines.append(clipped)
            remaining -= len(clipped)
        if remaining <= 0:
            break
    return "\n\n".join(lines).strip()


def _render_message(message: ModelRequest | ModelResponse) -> str:
    prefix = "Assistant" if isinstance(message, ModelResponse) else "User/Tool"
    fragments: list[str] = []
    for part in message.parts:
        if isinstance(part, UserPromptPart):
            fragments.append(f"User: {str(part.content or '').strip()}")
        elif isinstance(part, TextPart):
            fragments.append(f"Assistant: {str(part.content or '').strip()}")
        elif isinstance(part, ThinkingPart):
            continue
        elif isinstance(part, ToolCallPart):
            tool_name = str(part.tool_name or "").strip() or "tool"
            fragments.append(f"Assistant tool call [{tool_name}]")
        elif isinstance(part, ToolReturnPart):
            tool_name = str(part.tool_name or "").strip() or "tool"
            fragments.append(
                f"Tool result [{tool_name}]: {str(part.content or '').strip()}"
            )
        elif isinstance(part, RetryPromptPart):
            fragments.append(f"Retry: {str(part.content or '').strip()}")
    content = "\n".join(fragment for fragment in fragments if fragment.strip()).strip()
    if not content:
        return ""
    return f"{prefix}\n{content}"


def _message_text_size(message: ModelMessage) -> int:
    return len(_render_message(message))
