# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, ModelRequestNode
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
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

from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.logger import get_logger, log_event
from agent_teams.net.llm_client import build_llm_http_client
from agent_teams.providers.llm_retry import run_with_llm_retry
from agent_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
from agent_teams.providers.openai_model_profiles import (
    resolve_openai_chat_model_profile,
)
from agent_teams.providers.openai_support import build_openai_provider
from agent_teams.sessions.session_history_marker_models import (
    SessionHistoryMarkerRecord,
    SessionHistoryMarkerType,
)
from agent_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)

LOGGER = get_logger(__name__)
_TRIGGER_RATIO = 0.8
_TARGET_RATIO = 0.5
_PROTECTED_TAIL_MESSAGES = 12


class ConversationTokenEstimator:
    def estimate_history_tokens(
        self,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> int:
        return sum(self.estimate_message_tokens(message) for message in history)

    def estimate_message_tokens(self, message: ModelRequest | ModelResponse) -> int:
        payload = ModelMessagesTypeAdapter.dump_json([message])
        serialized_size = len(payload)
        return max(1, (serialized_size // 4) + 8)


class ConversationCompactionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    should_compact: bool = False
    estimated_tokens_before: int = Field(default=0, ge=0)
    threshold_tokens: int = Field(default=0, ge=0)
    target_tokens: int = Field(default=0, ge=0)
    compacted_message_count: int = Field(default=0, ge=0)
    kept_message_count: int = Field(default=0, ge=0)
    source_char_budget: int = Field(default=0, ge=0)


class ConversationCompactionStrategy:
    def plan(
        self,
        *,
        history: Sequence[ModelRequest | ModelResponse],
        context_window: int | None,
    ) -> ConversationCompactionPlan:
        raise NotImplementedError


class DefaultConversationCompactionStrategy(ConversationCompactionStrategy):
    def __init__(
        self,
        *,
        estimator: ConversationTokenEstimator | None = None,
    ) -> None:
        self._estimator = estimator or ConversationTokenEstimator()

    def plan(
        self,
        *,
        history: Sequence[ModelRequest | ModelResponse],
        context_window: int | None,
    ) -> ConversationCompactionPlan:
        message_count = len(history)
        estimated_tokens = self._estimator.estimate_history_tokens(history)
        threshold_tokens = (
            int(context_window * _TRIGGER_RATIO)
            if context_window is not None and context_window > 0
            else 0
        )
        target_tokens = (
            int(context_window * _TARGET_RATIO)
            if context_window is not None and context_window > 0
            else 0
        )
        if (
            threshold_tokens <= 0
            or target_tokens <= 0
            or estimated_tokens < threshold_tokens
            or message_count <= (_PROTECTED_TAIL_MESSAGES + 1)
        ):
            return ConversationCompactionPlan(
                should_compact=False,
                estimated_tokens_before=estimated_tokens,
                threshold_tokens=threshold_tokens,
                target_tokens=target_tokens,
                kept_message_count=message_count,
            )

        message_tokens = [
            self._estimator.estimate_message_tokens(message) for message in history
        ]
        max_compactable = max(0, message_count - _PROTECTED_TAIL_MESSAGES)
        pending_tool_call_ids: set[str] = set()
        remaining_tokens = estimated_tokens
        latest_safe_split = 0
        target_safe_split = 0

        for index, message in enumerate(history, start=1):
            if index > max_compactable:
                break
            remaining_tokens = max(0, remaining_tokens - message_tokens[index - 1])
            _update_pending_tool_call_ids(pending_tool_call_ids, message)
            if pending_tool_call_ids:
                continue
            latest_safe_split = index
            if remaining_tokens <= target_tokens:
                target_safe_split = index
                break

        split_index = target_safe_split or latest_safe_split
        if split_index <= 0:
            return ConversationCompactionPlan(
                should_compact=False,
                estimated_tokens_before=estimated_tokens,
                threshold_tokens=threshold_tokens,
                target_tokens=target_tokens,
                kept_message_count=message_count,
            )

        kept_message_count = max(0, message_count - split_index)
        return ConversationCompactionPlan(
            should_compact=True,
            estimated_tokens_before=estimated_tokens,
            threshold_tokens=threshold_tokens,
            target_tokens=target_tokens,
            compacted_message_count=split_index,
            kept_message_count=kept_message_count,
            source_char_budget=min(max(target_tokens * 4, 12000), 48000),
        )


class ConversationCompactionService:
    def __init__(
        self,
        *,
        config: ModelEndpointConfig,
        retry_config: LlmRetryConfig,
        message_repo: MessageRepository,
        session_history_marker_repo: SessionHistoryMarkerRepository,
        strategy: ConversationCompactionStrategy | None = None,
    ) -> None:
        self._config = config
        self._retry_config = retry_config
        self._message_repo = message_repo
        self._session_history_marker_repo = session_history_marker_repo
        self._strategy = strategy or DefaultConversationCompactionStrategy()

    async def maybe_compact(
        self,
        *,
        session_id: str,
        role_id: str,
        conversation_id: str,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        plan = self._strategy.plan(
            history=history,
            context_window=self._config.context_window,
        )
        if not plan.should_compact:
            return list(history)

        source_history = list(history[: plan.compacted_message_count])
        if not source_history:
            return list(history)
        existing_summary = self.get_latest_summary(
            session_id=session_id,
            conversation_id=conversation_id,
        )
        summary = await self._rewrite_summary(
            role_id=role_id,
            existing_summary=existing_summary,
            source_history=source_history,
            source_char_budget=plan.source_char_budget,
        )
        if not summary:
            return list(history)

        marker = self._session_history_marker_repo.create(
            session_id=session_id,
            marker_type=SessionHistoryMarkerType.COMPACTION,
            metadata={
                "conversation_id": conversation_id,
                "role_id": role_id,
                "summary_markdown": summary,
                "estimated_tokens_before": str(plan.estimated_tokens_before),
                "threshold_tokens": str(plan.threshold_tokens),
                "target_tokens": str(plan.target_tokens),
                "compacted_message_count": str(plan.compacted_message_count),
            },
        )
        hidden_count = self._message_repo.hide_conversation_messages_for_compaction(
            conversation_id=conversation_id,
            hide_message_count=plan.compacted_message_count,
            hidden_marker_id=marker.marker_id,
        )
        if hidden_count <= 0:
            return list(history)

        log_event(
            LOGGER,
            logging.INFO,
            event="conversation.history.compacted",
            message="Compacted conversation history into a logical summary marker",
            payload={
                "role_id": role_id,
                "conversation_id": conversation_id,
                "estimated_tokens_before": plan.estimated_tokens_before,
                "threshold_tokens": plan.threshold_tokens,
                "target_tokens": plan.target_tokens,
                "compacted_message_count": hidden_count,
                "kept_message_count": plan.kept_message_count,
                "marker_id": marker.marker_id,
            },
        )
        return self._message_repo.get_history_for_conversation(conversation_id)

    def get_latest_summary(
        self,
        *,
        session_id: str,
        conversation_id: str,
    ) -> str:
        marker = self._get_latest_active_compaction_marker(
            session_id=session_id,
            conversation_id=conversation_id,
        )
        if marker is None:
            return ""
        return str(marker.metadata.get("summary_markdown") or "").strip()

    def build_prompt_section(
        self,
        *,
        session_id: str,
        conversation_id: str,
    ) -> str:
        summary = self.get_latest_summary(
            session_id=session_id,
            conversation_id=conversation_id,
        )
        if not summary:
            return ""
        return (
            "## Compacted Conversation Summary\n"
            "Older conversation turns were compacted out of the live context. "
            "Use this summary as the authoritative record for those hidden turns.\n\n"
            f"{summary.strip()}"
        ).strip()

    async def _rewrite_summary(
        self,
        *,
        role_id: str,
        existing_summary: str,
        source_history: Sequence[ModelRequest | ModelResponse],
        source_char_budget: int,
    ) -> str:
        transcript = _render_transcript(source_history, max_chars=source_char_budget)
        if not transcript.strip():
            return existing_summary.strip()
        agent = Agent[None, str](
            model=self._build_model(),
            output_type=str,
            instructions=(
                "You maintain a rolling compact summary for one ongoing agent conversation. "
                "Rewrite the summary as concise markdown that preserves the information needed to continue the conversation after older turns are hidden. "
                "Keep objectives, decisions, constraints, tool outcomes, artifacts, unresolved threads, important paths, filenames, commands, identifiers, and concrete next-step instructions whenever they matter. "
                "Preserve exact technical details when they are needed for future execution, especially filesystem paths, branch names, API names, config keys, error messages, and artifact locations. "
                "Drop chatter, repetition, timestamps, and redundant narration. "
                "Output only the final markdown."
            ),
            model_settings=self._model_settings(),
            retries=3,
        )
        prompt = (
            f"Role: {role_id}\n\n"
            f"Existing compacted summary:\n{existing_summary or '(empty)'}\n\n"
            f"Transcript to absorb:\n{transcript}"
        )
        result = await run_with_llm_retry(
            operation=lambda: self._run_streaming_summary(agent=agent, prompt=prompt),
            config=self._retry_config,
            is_retry_allowed=lambda: True,
            on_retry_scheduled=lambda _schedule: None,
        )
        return result.strip()

    async def _run_streaming_summary(
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
                raise RuntimeError("Conversation compaction summary did not complete")
            return agent_run.result.output.strip()

    def _get_latest_active_compaction_marker(
        self,
        *,
        session_id: str,
        conversation_id: str,
    ) -> SessionHistoryMarkerRecord | None:
        latest_clear = self._session_history_marker_repo.get_latest(
            session_id,
            marker_type=SessionHistoryMarkerType.CLEAR,
        )
        latest_clear_at = latest_clear.created_at if latest_clear is not None else None
        latest_marker: SessionHistoryMarkerRecord | None = None
        for marker in self._session_history_marker_repo.list_by_session(session_id):
            if marker.marker_type != SessionHistoryMarkerType.COMPACTION:
                continue
            if marker.metadata.get("conversation_id") != conversation_id:
                continue
            if latest_clear_at is not None and marker.created_at <= latest_clear_at:
                continue
            latest_marker = marker
        return latest_marker

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


def _update_pending_tool_call_ids(
    pending_tool_call_ids: set[str],
    message: ModelMessage,
) -> None:
    if isinstance(message, ModelResponse):
        for part in message.parts:
            if not isinstance(part, ToolCallPart):
                continue
            tool_call_id = str(part.tool_call_id or "").strip()
            if tool_call_id:
                pending_tool_call_ids.add(tool_call_id)
        return
    if not isinstance(message, ModelRequest):
        return
    for part in message.parts:
        tool_call_id = str(getattr(part, "tool_call_id", "") or "").strip()
        if not tool_call_id:
            continue
        if isinstance(part, (ToolReturnPart, RetryPromptPart)):
            pending_tool_call_ids.discard(tool_call_id)
