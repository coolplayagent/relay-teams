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
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
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
from relay_teams.sessions.session_history_marker_models import (
    SessionHistoryMarkerRecord,
    SessionHistoryMarkerType,
)
from relay_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)

LOGGER = get_logger(__name__)
_TRIGGER_RATIO = 0.8
_TARGET_RATIO = 0.5
_PROTECTED_TAIL_MESSAGES = 12
DEFAULT_PROTECTED_TAIL_MESSAGES = _PROTECTED_TAIL_MESSAGES
_SEVERE_HISTORY_PRESSURE_RATIO = 2.0
_MIN_HISTORY_TOKEN_BUDGET = 1
_ESTIMATED_TOKEN_BYTES = 4
_ESTIMATED_TOKEN_OVERHEAD = 8
_SOURCE_CHAR_BUDGET_PER_TARGET_TOKEN = 4
_MIN_SOURCE_CHAR_BUDGET = 12_000
_MAX_SOURCE_CHAR_BUDGET = 48_000
_SUMMARY_REWRITE_MAX_RETRIES = 3
_SUMMARY_RESPONSE_MAX_TOKENS = 400
_SUMMARY_TEMPERATURE = 0.2
_PROTECTED_TAIL_RATIO_NUMERATOR = 2
_PROTECTED_TAIL_RATIO_DENOMINATOR = 3
_SEVERE_PRESSURE_TAIL_DIVISOR = 4


class ConversationTokenEstimator:
    def estimate_history_tokens(
        self,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> int:
        return sum(self.estimate_message_tokens(message) for message in history)

    def estimate_message_tokens(self, message: ModelRequest | ModelResponse) -> int:
        payload = ModelMessagesTypeAdapter.dump_json([message])
        serialized_size = len(payload)
        return max(
            1,
            (serialized_size // _ESTIMATED_TOKEN_BYTES) + _ESTIMATED_TOKEN_OVERHEAD,
        )


class ConversationCompactionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    should_compact: bool = False
    estimated_tokens_before: int = Field(default=0, ge=0)
    estimated_tokens_after: int = Field(default=0, ge=0)
    threshold_tokens: int = Field(default=0, ge=0)
    target_tokens: int = Field(default=0, ge=0)
    compacted_message_count: int = Field(default=0, ge=0)
    kept_message_count: int = Field(default=0, ge=0)
    protected_tail_messages: int = Field(default=DEFAULT_PROTECTED_TAIL_MESSAGES, ge=0)
    source_char_budget: int = Field(default=0, ge=0)


class ConversationCompactionStrategy:
    def plan(
        self,
        *,
        history: Sequence[ModelRequest | ModelResponse],
        budget: "ConversationCompactionBudget",
    ) -> ConversationCompactionPlan:
        raise NotImplementedError


class ConversationCompactionBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    context_window: int | None = Field(default=None)
    estimated_system_prompt_tokens: int = Field(default=0, ge=0)
    estimated_user_prompt_tokens: int = Field(default=0, ge=0)
    estimated_tool_context_tokens: int = Field(default=0, ge=0)
    estimated_output_reserve_tokens: int = Field(default=0, ge=0)
    estimated_non_history_tokens: int = Field(default=0, ge=0)
    history_trigger_tokens: int = Field(default=0, ge=0)
    history_target_tokens: int = Field(default=0, ge=0)


def build_conversation_compaction_budget(
    *,
    context_window: int | None,
    estimated_system_prompt_tokens: int,
    estimated_user_prompt_tokens: int,
    estimated_tool_context_tokens: int,
    estimated_output_reserve_tokens: int,
) -> ConversationCompactionBudget:
    estimated_non_history_tokens = (
        estimated_system_prompt_tokens
        + estimated_user_prompt_tokens
        + estimated_tool_context_tokens
        + estimated_output_reserve_tokens
    )
    if context_window is None or context_window <= 0:
        return ConversationCompactionBudget(
            context_window=context_window,
            estimated_system_prompt_tokens=estimated_system_prompt_tokens,
            estimated_user_prompt_tokens=estimated_user_prompt_tokens,
            estimated_tool_context_tokens=estimated_tool_context_tokens,
            estimated_output_reserve_tokens=estimated_output_reserve_tokens,
            estimated_non_history_tokens=estimated_non_history_tokens,
        )
    history_trigger_tokens = max(
        _MIN_HISTORY_TOKEN_BUDGET,
        int(context_window * _TRIGGER_RATIO) - estimated_non_history_tokens,
    )
    history_target_tokens = max(
        _MIN_HISTORY_TOKEN_BUDGET,
        int(context_window * _TARGET_RATIO) - estimated_non_history_tokens,
    )
    return ConversationCompactionBudget(
        context_window=context_window,
        estimated_system_prompt_tokens=estimated_system_prompt_tokens,
        estimated_user_prompt_tokens=estimated_user_prompt_tokens,
        estimated_tool_context_tokens=estimated_tool_context_tokens,
        estimated_output_reserve_tokens=estimated_output_reserve_tokens,
        estimated_non_history_tokens=estimated_non_history_tokens,
        history_trigger_tokens=history_trigger_tokens,
        history_target_tokens=history_target_tokens,
    )


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
        budget: ConversationCompactionBudget,
    ) -> ConversationCompactionPlan:
        message_count = len(history)
        estimated_tokens = self._estimator.estimate_history_tokens(history)
        threshold_tokens = budget.history_trigger_tokens
        target_tokens = budget.history_target_tokens
        protected_tail_messages = _resolve_protected_tail_messages(
            message_count=message_count,
            estimated_tokens=estimated_tokens,
            threshold_tokens=threshold_tokens,
        )
        if (
            threshold_tokens <= 0
            or target_tokens <= 0
            or estimated_tokens < threshold_tokens
            or message_count <= 1
        ):
            return ConversationCompactionPlan(
                should_compact=False,
                estimated_tokens_before=estimated_tokens,
                estimated_tokens_after=estimated_tokens,
                threshold_tokens=threshold_tokens,
                target_tokens=target_tokens,
                kept_message_count=message_count,
                protected_tail_messages=protected_tail_messages,
            )

        message_tokens = [
            self._estimator.estimate_message_tokens(message) for message in history
        ]
        replayable_suffix_starts = _compute_replayable_suffix_starts(history)
        max_compactable = max(0, message_count - protected_tail_messages)
        if max_compactable <= 0:
            return ConversationCompactionPlan(
                should_compact=False,
                estimated_tokens_before=estimated_tokens,
                estimated_tokens_after=estimated_tokens,
                threshold_tokens=threshold_tokens,
                target_tokens=target_tokens,
                kept_message_count=message_count,
                protected_tail_messages=protected_tail_messages,
            )
        pending_tool_call_ids: set[str] = set()
        remaining_tokens = estimated_tokens
        latest_safe_split = 0
        latest_safe_remaining_tokens = estimated_tokens
        target_safe_split = 0
        target_safe_remaining_tokens = estimated_tokens

        for index, message in enumerate(history, start=1):
            if index > max_compactable:
                break
            remaining_tokens = max(0, remaining_tokens - message_tokens[index - 1])
            _update_pending_tool_call_ids(pending_tool_call_ids, message)
            if pending_tool_call_ids:
                continue
            if not replayable_suffix_starts[index]:
                continue
            latest_safe_split = index
            latest_safe_remaining_tokens = remaining_tokens
            if remaining_tokens <= target_tokens:
                target_safe_split = index
                target_safe_remaining_tokens = remaining_tokens
                break

        split_index = target_safe_split or latest_safe_split
        if split_index <= 0:
            return ConversationCompactionPlan(
                should_compact=False,
                estimated_tokens_before=estimated_tokens,
                estimated_tokens_after=estimated_tokens,
                threshold_tokens=threshold_tokens,
                target_tokens=target_tokens,
                kept_message_count=message_count,
                protected_tail_messages=protected_tail_messages,
            )

        kept_message_count = max(0, message_count - split_index)
        return ConversationCompactionPlan(
            should_compact=True,
            estimated_tokens_before=estimated_tokens,
            estimated_tokens_after=max(
                0,
                target_safe_remaining_tokens
                if target_safe_split > 0
                else latest_safe_remaining_tokens,
            ),
            threshold_tokens=threshold_tokens,
            target_tokens=target_tokens,
            compacted_message_count=split_index,
            kept_message_count=kept_message_count,
            protected_tail_messages=protected_tail_messages,
            source_char_budget=min(
                max(
                    target_tokens * _SOURCE_CHAR_BUDGET_PER_TARGET_TOKEN,
                    _MIN_SOURCE_CHAR_BUDGET,
                ),
                _MAX_SOURCE_CHAR_BUDGET,
            ),
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
        source_history: Sequence[ModelRequest | ModelResponse] | None = None,
        budget: ConversationCompactionBudget,
        estimated_tokens_before_microcompact: int | None = None,
        estimated_tokens_after_microcompact: int | None = None,
    ) -> list[ModelRequest | ModelResponse]:
        summary_history = _resolve_summary_source_history(
            history=history,
            source_history=source_history,
        )
        plan = self._strategy.plan(
            history=history,
            budget=budget,
        )
        if not plan.should_compact:
            return list(history)
        compacted_message_count = coerce_replayable_compaction_count(
            history=history,
            proposed_count=plan.compacted_message_count,
        )
        if compacted_message_count != plan.compacted_message_count:
            if compacted_message_count <= 0:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="conversation.history.compaction.skipped_invalid_suffix",
                    message=(
                        "Skipped conversation compaction because the proposed suffix "
                        "was not replayable"
                    ),
                    payload={
                        "role_id": role_id,
                        "conversation_id": conversation_id,
                        "proposed_compacted_message_count": plan.compacted_message_count,
                    },
                )
                return list(history)
            estimator = ConversationTokenEstimator()
            plan = plan.model_copy(
                update={
                    "compacted_message_count": compacted_message_count,
                    "kept_message_count": len(history) - compacted_message_count,
                    "estimated_tokens_after": estimator.estimate_history_tokens(
                        history[compacted_message_count:]
                    ),
                }
            )

        compacted_source_history = list(summary_history[: plan.compacted_message_count])
        if not compacted_source_history:
            return list(history)
        existing_summary = self.get_latest_summary(
            session_id=session_id,
            conversation_id=conversation_id,
        )
        summary = await self._rewrite_summary(
            role_id=role_id,
            existing_summary=existing_summary,
            source_history=compacted_source_history,
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
                "compaction_strategy": "rolling_summary",
                "estimated_tokens_before": str(
                    estimated_tokens_before_microcompact
                    if estimated_tokens_before_microcompact is not None
                    else plan.estimated_tokens_before
                ),
                "estimated_tokens_after_microcompact": str(
                    estimated_tokens_after_microcompact
                    if estimated_tokens_after_microcompact is not None
                    else plan.estimated_tokens_before
                ),
                "estimated_tokens_after_compact": str(plan.estimated_tokens_after),
                "threshold_tokens": str(plan.threshold_tokens),
                "target_tokens": str(plan.target_tokens),
                "compacted_message_count": str(plan.compacted_message_count),
                "kept_message_count": str(plan.kept_message_count),
                "protected_tail_messages": str(plan.protected_tail_messages),
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
                "estimated_tokens_after_compact": plan.estimated_tokens_after,
                "threshold_tokens": plan.threshold_tokens,
                "target_tokens": plan.target_tokens,
                "compacted_message_count": hidden_count,
                "kept_message_count": plan.kept_message_count,
                "protected_tail_messages": plan.protected_tail_messages,
                "marker_id": marker.marker_id,
            },
        )
        return list(history[hidden_count:])

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
            retries=_SUMMARY_REWRITE_MAX_RETRIES,
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
        emitted_text_chunks: list[str] = []
        text_lengths: dict[int, int] = {}
        async with agent.iter(prompt) as agent_run:
            async for node in agent_run:
                if not isinstance(node, ModelRequestNode):
                    continue
                async with node.stream(agent_run.ctx) as stream:
                    async for event in stream:
                        self._capture_streamed_summary_text(
                            event=event,
                            emitted_text_chunks=emitted_text_chunks,
                            text_lengths=text_lengths,
                        )
            if agent_run.result is None:
                raise RuntimeError("Conversation compaction summary did not complete")
            result_output = agent_run.result.output.strip()
            streamed_output = "".join(emitted_text_chunks).strip()
            if streamed_output and streamed_output != result_output:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="conversation.compaction.stream_text_fallback_applied",
                    message=("Repairing compacted summary with streamed text fallback"),
                    payload={
                        "result_output_length": len(result_output),
                        "streamed_output_length": len(streamed_output),
                    },
                )
                return streamed_output
            return result_output

    def _capture_streamed_summary_text(
        self,
        *,
        event: object,
        emitted_text_chunks: list[str],
        text_lengths: dict[int, int],
    ) -> None:
        if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
            self._append_streamed_summary_text(
                part_index=event.index,
                content=event.part.content,
                emitted_text_chunks=emitted_text_chunks,
                text_lengths=text_lengths,
            )
            return
        if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
            text = str(event.delta.content_delta or "")
            if not text:
                return
            text_lengths[event.index] = text_lengths.get(event.index, 0) + len(text)
            emitted_text_chunks.append(text)
            return
        if isinstance(event, PartEndEvent) and isinstance(event.part, TextPart):
            self._append_streamed_summary_text(
                part_index=event.index,
                content=event.part.content,
                emitted_text_chunks=emitted_text_chunks,
                text_lengths=text_lengths,
            )

    def _append_streamed_summary_text(
        self,
        *,
        part_index: int,
        content: str,
        emitted_text_chunks: list[str],
        text_lengths: dict[int, int],
    ) -> None:
        previous_length = text_lengths.get(part_index, 0)
        suffix = content[previous_length:]
        text_lengths[part_index] = len(content)
        if not suffix:
            return
        emitted_text_chunks.append(suffix)

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
            _SUMMARY_RESPONSE_MAX_TOKENS
            if configured_max_tokens is None
            else min(configured_max_tokens, _SUMMARY_RESPONSE_MAX_TOKENS)
        )
        return {
            "temperature": min(self._config.sampling.temperature, _SUMMARY_TEMPERATURE),
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


def message_has_replay_anchor(message: ModelRequest | ModelResponse) -> bool:
    if not isinstance(message, ModelRequest):
        return False
    for part in message.parts:
        if isinstance(part, UserPromptPart):
            return True
        if isinstance(part, RetryPromptPart) and not str(part.tool_name or "").strip():
            return True
    return False


def history_has_valid_tool_replay(
    history: Sequence[ModelRequest | ModelResponse],
) -> bool:
    pending_tool_call_ids: set[str] = set()
    for message in history:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if not isinstance(part, ToolCallPart):
                    continue
                tool_call_id = str(part.tool_call_id or "").strip()
                if not tool_call_id:
                    return False
                pending_tool_call_ids.add(tool_call_id)
            continue
        for part in message.parts:
            if (
                isinstance(part, RetryPromptPart)
                and not str(part.tool_name or "").strip()
            ):
                continue
            if not isinstance(part, (ToolReturnPart, RetryPromptPart)):
                continue
            tool_call_id = str(part.tool_call_id or "").strip()
            if not tool_call_id or tool_call_id not in pending_tool_call_ids:
                return False
            pending_tool_call_ids.discard(tool_call_id)
    return not pending_tool_call_ids


def is_replayable_history(
    history: Sequence[ModelRequest | ModelResponse],
) -> bool:
    if not history:
        return True
    if not message_has_replay_anchor(history[0]):
        return False
    return history_has_valid_tool_replay(history)


def coerce_replayable_compaction_count(
    *,
    history: Sequence[ModelRequest | ModelResponse],
    proposed_count: int,
) -> int:
    if proposed_count <= 0 or proposed_count >= len(history):
        return 0
    replayable_suffix_starts = _compute_replayable_suffix_starts(history)
    for compacted_count in range(proposed_count, 0, -1):
        if replayable_suffix_starts[compacted_count]:
            return compacted_count
    return 0


def _compute_replayable_suffix_starts(
    history: Sequence[ModelRequest | ModelResponse],
) -> tuple[bool, ...]:
    if not history:
        return ()
    replayable_suffix_starts = [False] * len(history)
    required_tool_call_ids: set[str] = set()
    seen_tool_call_ids: set[str] = set()
    invalid_suffix = False
    for index in range(len(history) - 1, -1, -1):
        invalid_suffix = _update_reverse_replay_state(
            required_tool_call_ids=required_tool_call_ids,
            seen_tool_call_ids=seen_tool_call_ids,
            message=history[index],
            invalid_suffix=invalid_suffix,
        )
        replayable_suffix_starts[index] = (
            not invalid_suffix
            and not required_tool_call_ids
            and message_has_replay_anchor(history[index])
        )
    return tuple(replayable_suffix_starts)


def _update_reverse_replay_state(
    *,
    required_tool_call_ids: set[str],
    seen_tool_call_ids: set[str],
    message: ModelMessage,
    invalid_suffix: bool,
) -> bool:
    if invalid_suffix:
        return True
    if isinstance(message, ModelResponse):
        for part in message.parts:
            if not isinstance(part, ToolCallPart):
                continue
            tool_call_id = str(part.tool_call_id or "").strip()
            if not tool_call_id:
                return True
            if tool_call_id in required_tool_call_ids:
                required_tool_call_ids.discard(tool_call_id)
                seen_tool_call_ids.add(tool_call_id)
                continue
            if tool_call_id not in seen_tool_call_ids:
                return True
            seen_tool_call_ids.add(tool_call_id)
        return False
    if not isinstance(message, ModelRequest):
        return False
    for part in message.parts:
        if isinstance(part, RetryPromptPart) and not str(part.tool_name or "").strip():
            continue
        if not isinstance(part, (ToolReturnPart, RetryPromptPart)):
            continue
        tool_call_id = str(part.tool_call_id or "").strip()
        if not tool_call_id or tool_call_id in required_tool_call_ids:
            return True
        required_tool_call_ids.add(tool_call_id)
    return False


def _resolve_summary_source_history(
    *,
    history: Sequence[ModelRequest | ModelResponse],
    source_history: Sequence[ModelRequest | ModelResponse] | None,
) -> Sequence[ModelRequest | ModelResponse]:
    if source_history is None or len(source_history) != len(history):
        return history
    return source_history


def _resolve_protected_tail_messages(
    *,
    message_count: int,
    estimated_tokens: int,
    threshold_tokens: int,
    default_protected_tail_messages: int = DEFAULT_PROTECTED_TAIL_MESSAGES,
) -> int:
    if message_count <= 0:
        return 0
    if message_count == 1:
        return 1
    protected_tail_messages = min(
        default_protected_tail_messages,
        max(
            1,
            (message_count * _PROTECTED_TAIL_RATIO_NUMERATOR)
            // _PROTECTED_TAIL_RATIO_DENOMINATOR,
        ),
    )
    if threshold_tokens > 0 and estimated_tokens >= int(
        threshold_tokens * _SEVERE_HISTORY_PRESSURE_RATIO
    ):
        protected_tail_messages = min(
            protected_tail_messages,
            max(1, message_count // _SEVERE_PRESSURE_TAIL_DIVISOR),
        )
    return min(protected_tail_messages, message_count - 1)
