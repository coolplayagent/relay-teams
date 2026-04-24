# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Protocol, cast, runtime_checkable

from pydantic import BaseModel, ConfigDict
from pydantic_ai.messages import (
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    ToolReturnPart,
    UserPromptPart,
)

from relay_teams.agents.execution.conversation_compaction import (
    ConversationCompactionBudget,
    ConversationCompactionService,
    ConversationTokenEstimator,
    history_has_valid_tool_replay,
    is_replayable_history,
)
from relay_teams.agents.execution.conversation_microcompact import (
    ConversationMicrocompactService,
)
from relay_teams.agents.execution.prompt_budgeting import PromptBudgetingService
from relay_teams.agents.execution.prompt_modalities import (
    request_input_modalities,
    user_prompt_content_modalities,
    validate_input_modalities_capabilities,
)
from relay_teams.agents.execution.prompt_replay import (
    drop_duplicate_leading_request,
    extract_user_prompt_text,
    history_ends_with_user_prompt,
    mixed_tool_result_replay_parts,
    model_request_contains_only_tool_returns,
    model_request_contains_only_user_prompts,
    model_request_matches_tool_result_replay,
    model_requests_match_user_prompt,
    tool_result_replay_parts,
    tool_return_parts_match,
    user_prompt_parts_key,
)
from relay_teams.agents.execution.tool_call_history import (
    clone_model_request_with_parts,
)
from relay_teams.hooks import (
    HookDecisionBundle,
    HookDecisionType,
    HookEventName,
    PostCompactInput,
    PreCompactInput,
    UserPromptSubmitInput,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.mcp.mcp_models import McpToolSchema
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.media import (
    InlineMediaContentPart,
    MediaModality,
    MediaRefContentPart,
    TextContentPart,
    UserPromptContent,
    user_prompt_content_key,
    user_prompt_content_to_text,
)
from relay_teams.providers.model_config import ModelEndpointConfig
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.reminders import (
    ContextPressureObservation,
    ReminderKind,
    SystemReminderService,
)
from relay_teams.sessions.runs.assistant_errors import (
    AssistantRunError,
    AssistantRunErrorPayload,
)
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.workspace import build_conversation_id

LOGGER = get_logger(__name__)


@runtime_checkable
class PromptContentPersistenceService(Protocol):
    def to_persisted_user_prompt_content(
        self,
        *,
        parts: tuple[
            TextContentPart | MediaRefContentPart | InlineMediaContentPart,
            ...,
        ],
    ) -> UserPromptContent: ...


@runtime_checkable
class PromptContentHydrationService(Protocol):
    def hydrate_user_prompt_content(
        self,
        *,
        content: UserPromptContent,
    ) -> UserPromptContent: ...


@runtime_checkable
class PromptContentProviderService(Protocol):
    def to_provider_user_prompt_content(
        self,
        *,
        parts: tuple[
            TextContentPart | MediaRefContentPart | InlineMediaContentPart,
            ...,
        ],
    ) -> UserPromptContent: ...


class PromptHistoryMessageRepository(Protocol):
    def get_history_for_conversation(
        self,
        conversation_id: str,
    ) -> list[ModelRequest | ModelResponse]: ...

    def get_history_for_conversation_task(
        self,
        conversation_id: str,
        task_id: str,
    ) -> list[ModelRequest | ModelResponse]: ...

    def prune_conversation_history_to_safe_boundary(
        self,
        conversation_id: str,
    ) -> None: ...

    def append(
        self,
        *,
        session_id: str,
        workspace_id: str,
        conversation_id: str,
        agent_role_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        messages: list[ModelRequest | ModelResponse],
    ) -> None: ...

    def append_system_prompt_if_missing(
        self,
        *,
        session_id: str,
        workspace_id: str,
        conversation_id: str,
        agent_role_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        content: str,
    ) -> None: ...

    def replace_pending_user_prompt(
        self,
        *,
        session_id: str,
        workspace_id: str,
        conversation_id: str,
        agent_role_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        content: UserPromptContent,
    ) -> bool: ...


@runtime_checkable
class PromptHookService(Protocol):
    async def execute(
        self,
        *,
        event_input: UserPromptSubmitInput | PreCompactInput | PostCompactInput,
        run_event_hub: object,
    ) -> HookDecisionBundle: ...


class PreparedPromptContext(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    history: tuple[ModelRequest | ModelResponse, ...]
    system_prompt: str
    budget: ConversationCompactionBudget
    estimated_history_tokens_before_microcompact: int = 0
    estimated_history_tokens_after_microcompact: int = 0
    microcompact_compacted_message_count: int = 0
    microcompact_compacted_part_count: int = 0


class PromptHistoryService:
    def __init__(
        self,
        *,
        config: ModelEndpointConfig,
        run_intent_repo: RunIntentRepository,
        message_repo: PromptHistoryMessageRepository,
        conversation_compaction_service: ConversationCompactionService | None,
        conversation_microcompact_service: ConversationMicrocompactService | None,
        mcp_registry: McpRegistry,
        mcp_tool_context_token_cache: dict[str, int],
        media_asset_service: object | None,
        hook_service: object | None,
        reminder_service: SystemReminderService | None,
        run_event_hub: object,
        load_safe_history_for_conversation: Callable[
            [str], list[ModelRequest | ModelResponse]
        ],
    ) -> None:
        self._config = config
        self._run_intent_repo = run_intent_repo
        self._message_repo = message_repo
        self._conversation_compaction_service = conversation_compaction_service
        self._conversation_microcompact_service = conversation_microcompact_service
        self._mcp_registry = mcp_registry
        self._mcp_tool_context_token_cache = mcp_tool_context_token_cache
        self._media_asset_service = media_asset_service
        self._hook_service = hook_service
        self._reminder_service = reminder_service
        self._run_event_hub = run_event_hub
        self._load_safe_history_for_conversation = load_safe_history_for_conversation

    def _prompt_budgeting_service(self) -> PromptBudgetingService:
        return PromptBudgetingService(
            config=self._config,
            mcp_registry=self._mcp_registry,
            mcp_tool_context_token_cache=self._mcp_tool_context_token_cache,
        )

    async def prepare_prompt_context(
        self,
        *,
        request: LLMRequest,
        conversation_id: str,
        system_prompt: str,
        reserve_user_prompt_tokens: bool,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
    ) -> PreparedPromptContext:
        history = self._load_safe_history_for_conversation(conversation_id)
        history, protected_current_prompt = self._split_protected_current_prompt(
            request=request,
            conversation_id=conversation_id,
            history=history,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
        )
        source_history = list(history)
        provisional_system_prompt = self.inject_compaction_summary(
            session_id=request.session_id,
            conversation_id=conversation_id,
            system_prompt=system_prompt,
        )
        budget = await self.estimate_compaction_budget(
            request=request,
            history=history,
            system_prompt=provisional_system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )
        estimated_before_microcompact = (
            ConversationTokenEstimator().estimate_history_tokens(history)
        )
        estimated_after_microcompact = estimated_before_microcompact
        compacted_message_count = 0
        compacted_part_count = 0
        if self._conversation_microcompact_service is not None:
            microcompact_result = self._conversation_microcompact_service.apply(
                history=history,
                budget=budget,
            )
            history = list(microcompact_result.messages)
            estimated_before_microcompact = microcompact_result.estimated_tokens_before
            estimated_after_microcompact = microcompact_result.estimated_tokens_after
            compacted_message_count = microcompact_result.compacted_message_count
            compacted_part_count = microcompact_result.compacted_part_count
        history = await self.maybe_compact_history(
            request=request,
            history=history,
            source_history=source_history,
            conversation_id=conversation_id,
            budget=budget,
            estimated_tokens_before_microcompact=estimated_before_microcompact,
            estimated_tokens_after_microcompact=estimated_after_microcompact,
        )
        history = self.coerce_history_to_provider_safe_sequence(
            request=request,
            history=history,
        )
        if protected_current_prompt is not None:
            history.append(protected_current_prompt)
        final_system_prompt = self.inject_compaction_summary(
            session_id=request.session_id,
            conversation_id=conversation_id,
            system_prompt=system_prompt,
        )
        final_budget = await self.estimate_compaction_budget(
            request=request,
            history=history,
            system_prompt=final_system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )
        return PreparedPromptContext(
            history=tuple(history),
            system_prompt=final_system_prompt,
            budget=final_budget,
            estimated_history_tokens_before_microcompact=estimated_before_microcompact,
            estimated_history_tokens_after_microcompact=estimated_after_microcompact,
            microcompact_compacted_message_count=compacted_message_count,
            microcompact_compacted_part_count=compacted_part_count,
        )

    def _split_protected_current_prompt(
        self,
        *,
        request: LLMRequest,
        conversation_id: str,
        history: Sequence[ModelRequest | ModelResponse],
        reserve_user_prompt_tokens: bool,
    ) -> tuple[list[ModelRequest | ModelResponse], ModelRequest | None]:
        candidate_history = list(history)
        if not reserve_user_prompt_tokens:
            return candidate_history, None
        current_keys = self._current_request_prompt_keys(
            request=request,
            conversation_id=conversation_id,
        )
        if not current_keys:
            return candidate_history, None
        if not any(
            history_ends_with_user_prompt(candidate_history, current_key)
            for current_key in current_keys
        ):
            return candidate_history, None
        protected_prompt = candidate_history.pop()
        if not isinstance(protected_prompt, ModelRequest):
            return candidate_history, None
        return candidate_history, protected_prompt

    def _current_request_prompt_keys(
        self,
        *,
        request: LLMRequest,
        conversation_id: str,
    ) -> tuple[str, ...]:
        keys: list[str] = []
        current_content = self.current_request_prompt_content(request)
        if current_content is not None:
            current_key = user_prompt_content_key(current_content)
            if current_key and current_key not in keys:
                keys.append(current_key)
        task_history = self._message_repo.get_history_for_conversation_task(
            conversation_id,
            request.task_id,
        )
        for message in reversed(task_history):
            if not isinstance(message, ModelRequest):
                continue
            current_key = user_prompt_parts_key(parts=message.parts)
            if current_key:
                if current_key not in keys:
                    keys.append(current_key)
                break
        return tuple(keys)

    def coerce_history_to_provider_safe_sequence(
        self,
        *,
        request: LLMRequest,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        candidate_history = list(history)
        if is_replayable_history(candidate_history):
            return candidate_history
        replayable_start = self.first_tool_replayable_history_index(candidate_history)
        if replayable_start > 0:
            candidate_history = candidate_history[replayable_start:]
            log_event(
                LOGGER,
                logging.WARNING,
                event="llm.history.replayable_prefix.dropped",
                message=(
                    "Dropped a non-replayable history prefix before sending the "
                    "provider request"
                ),
                payload={
                    "role_id": request.role_id,
                    "instance_id": request.instance_id,
                    "dropped_message_count": replayable_start,
                },
            )
        if candidate_history and is_replayable_history(candidate_history):
            return candidate_history
        if (
            candidate_history
            and not request_has_prompt_content(request)
            and history_has_valid_tool_replay(candidate_history)
        ):
            bridge_message = self.build_history_replay_bridge_message(request=request)
            if bridge_message is not None:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="llm.history.replay_bridge.inserted",
                    message=(
                        "Inserted a synthetic user bridge before replaying "
                        "assistant/tool-only history"
                    ),
                    payload={
                        "role_id": request.role_id,
                        "instance_id": request.instance_id,
                        "message_count": len(candidate_history),
                    },
                )
                return [bridge_message, *candidate_history]
        if request_has_prompt_content(request):
            log_event(
                LOGGER,
                logging.WARNING,
                event="llm.history.invalid_suffix.dropped",
                message=(
                    "Dropped non-replayable history and relied on the current user "
                    "prompt to restart the provider request"
                ),
                payload={
                    "role_id": request.role_id,
                    "instance_id": request.instance_id,
                    "message_count": len(candidate_history),
                },
            )
            return []
        bridge_message = self.build_history_replay_bridge_message(request=request)
        if bridge_message is not None:
            log_event(
                LOGGER,
                logging.WARNING,
                event="llm.history.replay_bridge.synthetic_only",
                message=(
                    "Fell back to a synthetic user bridge because no replayable "
                    "history suffix remained"
                ),
                payload={
                    "role_id": request.role_id,
                    "instance_id": request.instance_id,
                },
            )
            return [bridge_message]
        return []

    def first_tool_replayable_history_index(
        self,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> int:
        if not history:
            return 0
        for index in range(len(history)):
            if history_has_valid_tool_replay(history[index:]):
                return index
        return len(history)

    def build_history_replay_bridge_message(
        self,
        *,
        request: LLMRequest,
    ) -> ModelRequest | None:
        prompt = self.build_history_replay_bridge_prompt(request=request)
        if not prompt:
            return None
        return ModelRequest(parts=[UserPromptPart(content=prompt)])

    def build_history_replay_bridge_prompt(
        self,
        *,
        request: LLMRequest,
    ) -> str:
        intent_text = ""
        try:
            intent_text = self._run_intent_repo.get(
                request.run_id,
                fallback_session_id=request.session_id,
            ).intent.strip()
        except KeyError:
            intent_text = ""
        lines = [
            "Continue the existing task using the compacted summary and preserved execution history.",
            "Resume from the latest in-progress state without discarding prior decisions or artifacts.",
        ]
        if intent_text:
            lines.extend(
                [
                    "",
                    "Original task intent:",
                    intent_text,
                ]
            )
        return "\n".join(lines).strip()

    async def estimate_compaction_budget(
        self,
        *,
        request: LLMRequest,
        history: Sequence[ModelRequest | ModelResponse],
        system_prompt: str,
        reserve_user_prompt_tokens: bool,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
    ) -> ConversationCompactionBudget:
        return await self._prompt_budgeting_service().estimate_compaction_budget(
            request=request,
            history=history,
            system_prompt=system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )

    async def safe_max_output_tokens(
        self,
        *,
        request: LLMRequest,
        history: Sequence[ModelRequest | ModelResponse],
        system_prompt: str,
        reserve_user_prompt_tokens: bool,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
    ) -> int | None:
        return await self._prompt_budgeting_service().safe_max_output_tokens(
            request=request,
            history=history,
            system_prompt=system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )

    def estimated_tool_context_tokens(
        self,
        *,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
        estimated_mcp_context_tokens: int | None = None,
    ) -> int:
        return self._prompt_budgeting_service().estimated_tool_context_tokens(
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
            estimated_mcp_context_tokens=estimated_mcp_context_tokens,
        )

    async def estimated_mcp_context_tokens(
        self,
        *,
        allowed_mcp_servers: tuple[str, ...],
    ) -> int:
        return await self._prompt_budgeting_service().estimated_mcp_context_tokens(
            allowed_mcp_servers=allowed_mcp_servers
        )

    def estimate_mcp_tool_schema_tokens(
        self,
        *,
        server_name: str,
        tool_schemas: tuple[McpToolSchema, ...],
    ) -> int:
        return self._prompt_budgeting_service().estimate_mcp_tool_schema_tokens(
            server_name=server_name,
            tool_schemas=tool_schemas,
        )

    def estimated_mcp_context_tokens_fallback(
        self,
        *,
        allowed_mcp_servers: tuple[str, ...],
    ) -> int:
        return self._prompt_budgeting_service().estimated_mcp_context_tokens_fallback(
            allowed_mcp_servers=allowed_mcp_servers
        )

    async def maybe_compact_history(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        source_history: Sequence[ModelRequest | ModelResponse] | None,
        conversation_id: str,
        budget: ConversationCompactionBudget,
        estimated_tokens_before_microcompact: int | None,
        estimated_tokens_after_microcompact: int | None,
    ) -> list[ModelRequest | ModelResponse]:
        if self._conversation_compaction_service is None:
            return history
        plan = self._conversation_compaction_service.plan_compaction(
            history=history,
            budget=budget,
        )
        if not plan.should_compact:
            return history
        if isinstance(self._hook_service, PromptHookService):
            _ = await self._hook_service.execute(
                event_input=PreCompactInput(
                    event_name=HookEventName.PRE_COMPACT,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    run_kind=request.run_kind.value,
                    conversation_id=conversation_id,
                    message_count_before=len(history),
                    estimated_tokens_before=estimated_tokens_before_microcompact or 0,
                    estimated_tokens_after_microcompact=(
                        estimated_tokens_after_microcompact or 0
                    ),
                    threshold_tokens=plan.threshold_tokens,
                    target_tokens=plan.target_tokens,
                ),
                run_event_hub=self._run_event_hub,
            )
        compacted_result = (
            await self._conversation_compaction_service.maybe_compact_with_result(
                session_id=request.session_id,
                role_id=request.role_id,
                conversation_id=conversation_id,
                history=history,
                source_history=source_history,
                budget=budget,
                estimated_tokens_before_microcompact=(
                    estimated_tokens_before_microcompact
                ),
                estimated_tokens_after_microcompact=estimated_tokens_after_microcompact,
                plan=plan,
            )
        )
        compacted_history = list(compacted_result.messages)
        if (
            isinstance(self._hook_service, PromptHookService)
            and compacted_result.applied
        ):
            _ = await self._hook_service.execute(
                event_input=PostCompactInput(
                    event_name=HookEventName.POST_COMPACT,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    run_kind=request.run_kind.value,
                    conversation_id=conversation_id,
                    message_count_before=len(history),
                    message_count_after=len(compacted_history),
                    estimated_tokens_before=estimated_tokens_before_microcompact or 0,
                    estimated_tokens_after=ConversationTokenEstimator().estimate_history_tokens(
                        compacted_history
                    ),
                ),
                run_event_hub=self._run_event_hub,
            )
        if compacted_result.applied and self._reminder_service is not None:
            _ = self._reminder_service.observe_context_pressure(
                ContextPressureObservation(
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    conversation_id=conversation_id,
                    kind=ReminderKind.POST_COMPACTION,
                    message_count_before=len(history),
                    message_count_after=len(compacted_history),
                    estimated_tokens_before=estimated_tokens_before_microcompact or 0,
                    estimated_tokens_after=ConversationTokenEstimator().estimate_history_tokens(
                        compacted_history
                    ),
                    threshold_tokens=plan.threshold_tokens,
                    target_tokens=plan.target_tokens,
                )
            )
        return compacted_history

    def inject_compaction_summary(
        self,
        *,
        session_id: str,
        conversation_id: str,
        system_prompt: str,
    ) -> str:
        if self._conversation_compaction_service is None:
            return system_prompt
        prompt_section = self._conversation_compaction_service.build_prompt_section(
            session_id=session_id,
            conversation_id=conversation_id,
        )
        if not prompt_section:
            return system_prompt
        return f"{system_prompt.rstrip()}\n\n{prompt_section}".strip()

    async def apply_user_prompt_hooks(
        self,
        request: LLMRequest,
    ) -> tuple[LLMRequest, tuple[str, ...]]:
        if not isinstance(self._hook_service, PromptHookService):
            return request, ()
        prompt_text = self.resolve_hook_prompt_text(request)
        if not prompt_text:
            return request, ()
        bundle = await self._hook_service.execute(
            event_input=UserPromptSubmitInput(
                event_name=HookEventName.USER_PROMPT_SUBMIT,
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                user_prompt=prompt_text,
                input_parts=tuple(
                    part.model_dump(mode="json") for part in request.input
                ),
                run_kind=request.run_kind.value,
            ),
            run_event_hub=self._run_event_hub,
        )
        if bundle.decision == HookDecisionType.DENY:
            message = bundle.reason or "The prompt was blocked by runtime hooks."
            raise AssistantRunError(
                AssistantRunErrorPayload(
                    trace_id=request.trace_id,
                    session_id=request.session_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    conversation_id=conversation_id(request),
                    assistant_message=message,
                    error_code="prompt_denied",
                    error_message=message,
                )
            )
        next_request = request
        if bundle.updated_input is not None and isinstance(bundle.updated_input, str):
            next_request = request.model_copy(
                update={
                    "user_prompt": bundle.updated_input,
                    "input": (),
                }
            )
        return next_request, bundle.additional_context

    def resolve_hook_prompt_text(self, request: LLMRequest) -> str:
        prompt_text = request.prompt_text.strip()
        if prompt_text:
            return prompt_text
        history = self._message_repo.get_history_for_conversation(
            conversation_id(request)
        )
        for message in reversed(history):
            if not isinstance(message, ModelRequest):
                continue
            resolved = extract_user_prompt_text(message)
            if resolved:
                return resolved
        return ""

    def persist_hook_system_context_if_needed(
        self,
        *,
        request: LLMRequest,
        contexts: tuple[str, ...],
    ) -> None:
        resolved_conversation_id = conversation_id(request)
        for context in contexts:
            text = str(context).strip()
            if not text:
                continue
            self._message_repo.append_system_prompt_if_missing(
                session_id=request.session_id,
                workspace_id=request.workspace_id,
                conversation_id=resolved_conversation_id,
                agent_role_id=request.role_id,
                instance_id=request.instance_id,
                task_id=request.task_id,
                trace_id=request.trace_id,
                content=text,
            )

    def persist_user_prompt_if_needed(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        content: UserPromptContent | None,
        filter_model_messages: Callable[
            [Sequence[ModelRequest | ModelResponse]],
            list[ModelRequest | ModelResponse],
        ],
    ) -> tuple[list[ModelRequest | ModelResponse], bool]:
        if content is None:
            return history, False
        prompt_text = user_prompt_content_to_text(content)
        if not prompt_text:
            return history, False
        prompt_key = user_prompt_content_key(content)
        if history_ends_with_user_prompt(history, prompt_key):
            return history, False
        resolved_conversation_id = conversation_id(request)
        self._message_repo.prune_conversation_history_to_safe_boundary(
            resolved_conversation_id
        )
        replaced = self._message_repo.replace_pending_user_prompt(
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            conversation_id=resolved_conversation_id,
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            content=content,
        )
        prompt_message = ModelRequest(parts=[UserPromptPart(content=content)])
        if replaced:
            return (
                filter_model_messages(
                    self._message_repo.get_history_for_conversation(
                        resolved_conversation_id
                    )
                ),
                True,
            )
        self._message_repo.append(
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            conversation_id=resolved_conversation_id,
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            messages=[prompt_message],
        )
        next_history = list(history)
        next_history.append(prompt_message)
        return next_history, False

    def drop_duplicate_leading_request(
        self,
        *,
        history: Sequence[ModelRequest | ModelResponse],
        new_messages: list[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        return drop_duplicate_leading_request(
            history=history, new_messages=new_messages
        )

    def history_ends_with_user_prompt(
        self,
        *,
        history: Sequence[ModelRequest | ModelResponse],
        content_key: str,
    ) -> bool:
        return history_ends_with_user_prompt(history=history, content_key=content_key)

    def model_requests_match_user_prompt(
        self,
        left: ModelRequest,
        right: ModelRequest,
    ) -> bool:
        return model_requests_match_user_prompt(left, right)

    def model_request_matches_tool_result_replay(
        self,
        *,
        history: Sequence[ModelRequest | ModelResponse],
        replayed_request: ModelRequest,
    ) -> bool:
        return model_request_matches_tool_result_replay(
            history=history,
            replayed_request=replayed_request,
        )

    def tool_result_replay_parts(
        self,
        *,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> tuple[list[ToolReturnPart], list[UserPromptPart]] | None:
        return tool_result_replay_parts(history=history)

    def mixed_tool_result_replay_parts(
        self,
        message: ModelRequest | ModelResponse,
    ) -> tuple[list[ToolReturnPart], list[UserPromptPart]] | None:
        return mixed_tool_result_replay_parts(message)

    def model_request_contains_only_tool_returns(
        self,
        message: ModelRequest,
    ) -> bool:
        return model_request_contains_only_tool_returns(message)

    def model_request_contains_only_user_prompts(
        self,
        message: ModelRequest,
    ) -> bool:
        return model_request_contains_only_user_prompts(message)

    def user_prompt_parts_key(
        self,
        *,
        parts: Sequence[ModelRequestPart],
    ) -> str | None:
        return user_prompt_parts_key(parts=parts)

    def tool_return_parts_match(
        self,
        *,
        expected_part: ToolReturnPart,
        actual_part: ToolReturnPart,
    ) -> bool:
        return tool_return_parts_match(
            expected_part=expected_part,
            actual_part=actual_part,
        )

    def extract_user_prompt_text(self, message: ModelRequest) -> str | None:
        return extract_user_prompt_text(message)

    def request_has_prompt_content(self, request: LLMRequest) -> bool:
        return request_has_prompt_content(request)

    def current_request_prompt_content(
        self,
        request: LLMRequest,
    ) -> UserPromptContent | None:
        if request.input:
            media_asset_service = self._prompt_content_persistence_service()
            if media_asset_service is not None:
                return media_asset_service.to_persisted_user_prompt_content(
                    parts=request.input
                )
        prompt = str(request.user_prompt or "").strip()
        return prompt or None

    def validate_request_input_capabilities(self, request: LLMRequest) -> None:
        validate_input_modalities_capabilities(
            config=self._config,
            modalities=request_input_modalities(request.input),
        )

    def validate_history_input_capabilities(
        self,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> None:
        modalities: list[MediaModality] = []
        for message in history:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if not isinstance(part, UserPromptPart):
                    continue
                modalities.extend(
                    user_prompt_content_modalities(
                        cast(UserPromptContent, part.content)
                    )
                )
        validate_input_modalities_capabilities(
            config=self._config,
            modalities=tuple(modalities),
        )

    def hydrate_history_media_content(
        self,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        media_asset_service = self._prompt_content_hydration_service()
        if media_asset_service is None:
            return list(history)
        hydrated_messages: list[ModelRequest | ModelResponse] = []
        for message in history:
            if not isinstance(message, ModelRequest):
                hydrated_messages.append(message)
                continue
            next_parts: list[ModelRequestPart] = []
            changed = False
            for part in message.parts:
                if not isinstance(part, UserPromptPart):
                    next_parts.append(part)
                    continue
                hydrated_content = media_asset_service.hydrate_user_prompt_content(
                    content=cast(UserPromptContent, part.content)
                )
                if hydrated_content != part.content:
                    changed = True
                next_parts.append(
                    UserPromptPart(
                        content=hydrated_content,
                        timestamp=part.timestamp,
                        part_kind=part.part_kind,
                    )
                )
            if changed:
                hydrated_messages.append(
                    clone_model_request_with_parts(message, next_parts)
                )
                continue
            hydrated_messages.append(message)
        return hydrated_messages

    def provider_history_for_model_turn(
        self,
        *,
        request: LLMRequest,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        provider_history, _ = self.provider_history_for_model_turn_details(
            request=request,
            history=history,
        )
        return provider_history

    def provider_history_for_model_turn_details(
        self,
        *,
        request: LLMRequest,
        history: Sequence[ModelRequest | ModelResponse],
        consumed_tool_call_ids: set[str] | None = None,
    ) -> tuple[list[ModelRequest | ModelResponse], tuple[str, ...]]:
        _ = (request, consumed_tool_call_ids)
        return self.hydrate_history_media_content(history), ()

    def prompt_content_provider_service(self) -> PromptContentProviderService | None:
        return self._prompt_content_provider_service()

    def _prompt_content_persistence_service(
        self,
    ) -> PromptContentPersistenceService | None:
        if not isinstance(self._media_asset_service, PromptContentPersistenceService):
            return None
        return self._media_asset_service

    def _prompt_content_hydration_service(
        self,
    ) -> PromptContentHydrationService | None:
        if not isinstance(self._media_asset_service, PromptContentHydrationService):
            return None
        return self._media_asset_service

    def _prompt_content_provider_service(
        self,
    ) -> PromptContentProviderService | None:
        if not isinstance(self._media_asset_service, PromptContentProviderService):
            return None
        return self._media_asset_service


def conversation_id(request: LLMRequest) -> str:
    return request.conversation_id or build_conversation_id(
        request.session_id,
        request.role_id,
    )


def request_has_prompt_content(request: LLMRequest) -> bool:
    return bool(request.prompt_text.strip())
