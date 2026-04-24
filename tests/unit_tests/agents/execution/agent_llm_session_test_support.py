# -*- coding: utf-8 -*-
from __future__ import annotations

import httpx
import relay_teams.agents.execution.agent_llm_session as llm_module
import relay_teams.agents.execution.recovery_flow as recovery_module
from typing import cast

from openai import APIStatusError
from pydantic import JsonValue
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (
    BinaryContent,
    ImageUrl,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
    UserPromptPart,
)

from relay_teams.agents.execution.agent_llm_session import (
    AgentLlmSession,
    _FallbackAttemptState,
    _FallbackAttemptStatus,
)
from relay_teams.agents.execution.conversation_compaction import (
    ConversationCompactionPlan,
    ConversationCompactionResult,
    ConversationCompactionService,
)
from relay_teams.agents.execution.conversation_microcompact import (
    ConversationMicrocompactResult,
    ConversationMicrocompactService,
)
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.hooks import HookDecisionBundle, HookDecisionType, HookEventName
from relay_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.media import MediaModality, MediaRefContentPart, TextContentPart
from relay_teams.providers.llm_retry import LlmRetryErrorInfo, LlmRetrySchedule
from relay_teams.providers.model_config import (
    LlmRetryConfig,
    ModelCapabilities,
    ModelEndpointConfig,
    ModelModalityMatrix,
)
from relay_teams.providers.model_fallback import LlmFallbackDecision
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.sessions.runs.assistant_errors import AssistantRunError
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.tools.runtime.persisted_state import (
    PersistedToolCallState,
    ToolExecutionStatus,
)

__all__ = [
    "APIStatusError",
    "AgentLlmSession",
    "AssistantRunError",
    "BinaryContent",
    "ConversationCompactionService",
    "ConversationCompactionPlan",
    "ConversationCompactionResult",
    "ConversationMicrocompactResult",
    "ConversationMicrocompactService",
    "HookDecisionBundle",
    "HookDecisionType",
    "HookEventName",
    "ImageUrl",
    "JsonValue",
    "LLMRequest",
    "LlmFallbackDecision",
    "LlmRetryConfig",
    "LlmRetryErrorInfo",
    "LlmRetrySchedule",
    "McpConfigScope",
    "McpRegistry",
    "McpServerSpec",
    "MediaModality",
    "MediaRefContentPart",
    "MessageRepository",
    "ModelAPIError",
    "ModelCapabilities",
    "ModelEndpointConfig",
    "ModelModalityMatrix",
    "ModelRequest",
    "ModelRequestPart",
    "ModelResponse",
    "PartDeltaEvent",
    "PartStartEvent",
    "PersistedToolCallState",
    "RetryPromptPart",
    "RunEvent",
    "RunEventType",
    "RunIntentRepository",
    "TextContentPart",
    "TextPart",
    "ThinkingPartDelta",
    "ToolCallPart",
    "ToolCallPartDelta",
    "ToolExecutionStatus",
    "ToolReturnPart",
    "UserPromptPart",
    "_FallbackAttemptState",
    "_FallbackAttemptStatus",
    "_FakeCompactionService",
    "_FakeMessageRepo",
    "_FakeMicrocompactService",
    "_FakePromptHookService",
    "_FakeRunEnvHookService",
    "_FakeRunIntentRepo",
    "_build_request",
    "_zero_mcp_context_tokens",
    "httpx",
    "llm_module",
    "recovery_module",
]


class _FakeMessageRepo:
    def __init__(self, history: list[ModelRequest | ModelResponse]) -> None:
        self._history = history
        self.append_calls: list[list[ModelRequest | ModelResponse]] = []
        self.appended_system_prompts: list[str] = []
        self.pruned_conversation_ids: list[str] = []
        self.requested_conversation_ids: list[str] = []

    def get_history_for_conversation(
        self,
        conversation_id: str,
    ) -> list[ModelRequest | ModelResponse]:
        self.requested_conversation_ids.append(conversation_id)
        return list(self._history)

    def get_history_for_conversation_task(
        self,
        conversation_id: str,
        task_id: str,
    ) -> list[ModelRequest | ModelResponse]:
        _ = task_id
        return self.get_history_for_conversation(conversation_id)

    def prune_conversation_history_to_safe_boundary(self, conversation_id: str) -> None:
        self.pruned_conversation_ids.append(conversation_id)

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
    ) -> None:
        _ = (
            session_id,
            workspace_id,
            conversation_id,
            agent_role_id,
            instance_id,
            task_id,
            trace_id,
        )
        self.append_calls.append(list(messages))

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
    ) -> None:
        _ = (
            session_id,
            workspace_id,
            conversation_id,
            agent_role_id,
            instance_id,
            task_id,
            trace_id,
        )
        self.appended_system_prompts.append(content)

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
        content: str,
    ) -> bool:
        _ = (
            session_id,
            workspace_id,
            conversation_id,
            agent_role_id,
            instance_id,
            task_id,
            trace_id,
            content,
        )
        return False


class _FakeMicrocompactService:
    def __init__(self, result: ConversationMicrocompactResult) -> None:
        self.calls: list[object] = []
        self._result = result

    def apply(
        self,
        *,
        history: list[ModelRequest | ModelResponse],
        budget: object,
    ) -> ConversationMicrocompactResult:
        self.calls.append((list(history), budget))
        return self._result


class _FakeCompactionService:
    def __init__(
        self,
        prompt_section: str = "",
        *,
        plan: ConversationCompactionPlan | None = None,
        applied: bool = False,
        messages: tuple[ModelRequest | ModelResponse, ...] | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self._prompt_section = prompt_section
        self._plan = plan or ConversationCompactionPlan(
            should_compact=True,
            estimated_tokens_before=100,
            estimated_tokens_after=50,
            threshold_tokens=80,
            target_tokens=40,
            compacted_message_count=1,
            kept_message_count=1,
        )
        self._applied = applied
        self._messages = messages

    async def maybe_compact(
        self,
        **kwargs: object,
    ) -> list[ModelRequest | ModelResponse]:
        result = await self.maybe_compact_with_result(**kwargs)
        return list(result.messages)

    def plan_compaction(
        self,
        **kwargs: object,
    ) -> ConversationCompactionPlan:
        _ = kwargs
        return self._plan

    async def maybe_compact_with_result(
        self,
        **kwargs: object,
    ) -> ConversationCompactionResult:
        history = kwargs["history"]
        assert isinstance(history, list)
        self.calls.append(dict(kwargs))
        return ConversationCompactionResult(
            messages=self._messages or tuple(history),
            applied=self._applied,
            plan=self._plan,
        )

    def build_prompt_section(
        self,
        *,
        session_id: str,
        conversation_id: str,
    ) -> str:
        _ = (session_id, conversation_id)
        return self._prompt_section


class _FakeRunIntentRepo:
    def __init__(self, intent: str) -> None:
        self._intent = intent

    def get(self, run_id: str, *, fallback_session_id: str | None = None) -> object:
        _ = (run_id, fallback_session_id)
        return type("_Intent", (), {"intent": self._intent})()


async def _zero_mcp_context_tokens(
    *,
    allowed_mcp_servers: tuple[str, ...],
) -> int:
    _ = allowed_mcp_servers
    return 0


def _build_request(
    *,
    user_prompt: str | None = "User prompt",
    input: tuple[TextContentPart | MediaRefContentPart, ...] = (),
) -> LLMRequest:
    return LLMRequest(
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="default",
        conversation_id="conv-1",
        instance_id="inst-1",
        role_id="writer",
        system_prompt="System prompt",
        user_prompt=user_prompt,
        input=input,
    )


class _FakePromptHookService:
    def __init__(self, bundle: HookDecisionBundle) -> None:
        self.bundle = bundle
        self.events: list[HookEventName] = []

    async def execute(
        self,
        *,
        event_input: object,
        run_event_hub: object,
    ) -> HookDecisionBundle:
        _ = run_event_hub
        self.events.append(cast(HookEventName, getattr(event_input, "event_name")))
        return self.bundle


class _FakeRunEnvHookService:
    def __init__(self, run_env: dict[str, str]) -> None:
        self._run_env = run_env

    def get_run_env(self, run_id: str) -> dict[str, str]:
        _ = run_id
        return dict(self._run_env)
