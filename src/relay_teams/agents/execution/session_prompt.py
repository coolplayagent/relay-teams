# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import cast

from pydantic import JsonValue
from pydantic_ai.models.openai import OpenAIChatModelSettings
from pydantic_ai.messages import (
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from relay_teams.agents.execution.conversation_compaction import (
    ConversationCompactionBudget,
)
from relay_teams.agents.execution.coordination_agent_builder import (
    build_coordination_agent,
)
from relay_teams.agents.execution.message_commit import MessageCommitService
from relay_teams.agents.execution.prompt_history import (
    PreparedPromptContext,
)
from relay_teams.agents.execution.llm_transport_scope import (
    llm_http_client_cache_scope_for_request,
)
from relay_teams.agents.execution.session_mixin_base import AgentLlmSessionMixinBase
from relay_teams.agents.execution.tool_args_repair import repair_tool_args
from relay_teams.computer import (
    ComputerActionDescriptor,
    build_computer_tool_payload,
    describe_builtin_tool,
    describe_mcp_tool,
)
from relay_teams.media import UserPromptContent
from relay_teams.mcp.mcp_models import McpToolSchema
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.providers.openai_model_profiles import (
    resolve_openai_chat_model_profile,
)
from relay_teams.tools.runtime.persisted_state import (
    load_tool_call_state,
    load_tool_call_state_async,
)
from relay_teams.tools.runtime.policy import ToolApprovalPolicy
from relay_teams.workspace import build_conversation_id


def _display_text(value: JsonValue | None) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, default=str)


def _object_payload(value: JsonValue | None) -> dict[str, JsonValue] | None:
    if not isinstance(value, dict):
        return None
    return {
        str(key): cast(JsonValue, item)
        for key, item in value.items()
        if isinstance(key, str)
    }


def _content_payload(value: JsonValue | None) -> tuple[dict[str, JsonValue], ...]:
    if not isinstance(value, list):
        return ()
    items: list[dict[str, JsonValue]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                str(key): cast(JsonValue, element)
                for key, element in item.items()
                if isinstance(key, str)
            }
        )
    return tuple(items)


class _NullCommitMessageRepo:
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
            messages,
        )

    def get_history_for_conversation(
        self,
        conversation_id: str,
    ) -> list[ModelRequest | ModelResponse]:
        _ = conversation_id
        return []

    async def append_async(
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
        self.append(
            session_id=session_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_role_id=agent_role_id,
            instance_id=instance_id,
            task_id=task_id,
            trace_id=trace_id,
            messages=messages,
        )

    async def get_history_for_conversation_async(
        self,
        conversation_id: str,
    ) -> list[ModelRequest | ModelResponse]:
        return self.get_history_for_conversation(conversation_id)


class SessionPromptMixin(AgentLlmSessionMixinBase):
    async def _prepare_prompt_context(
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
        return await self._prompt_history_service().prepare_prompt_context(
            request=request,
            conversation_id=conversation_id,
            system_prompt=system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )

    async def _build_agent_iteration_context(
        self,
        *,
        request: LLMRequest,
        conversation_id: str,
        system_prompt: str,
        reserve_user_prompt_tokens: bool,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
    ) -> tuple[
        PreparedPromptContext,
        list[ModelRequest | ModelResponse],
        str,
        object,
    ]:
        prepared_prompt = await self._prepare_prompt_context(
            request=request,
            conversation_id=conversation_id,
            system_prompt=system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )
        history = list(prepared_prompt.history)
        self._validate_history_input_capabilities(history)
        prepared_system_prompt = prepared_prompt.system_prompt
        model_settings = await self._build_model_settings(
            request=request,
            history=history,
            system_prompt=prepared_system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )
        hook_service = getattr(self, "_hook_service", None)
        agent = build_coordination_agent(
            model_name=self._config.model,
            base_url=self._config.base_url,
            api_key=self._config.api_key,
            headers=self._config.headers,
            provider_type=self._config.provider,
            maas_auth=self._config.maas_auth,
            codeagent_auth=self._config.codeagent_auth,
            system_prompt=prepared_system_prompt,
            allowed_tools=allowed_tools,
            model_settings=model_settings,
            model_profile=resolve_openai_chat_model_profile(
                base_url=self._config.base_url,
                model_name=self._config.model,
            ),
            ssl_verify=self._config.ssl_verify,
            connect_timeout_seconds=self._config.connect_timeout_seconds,
            merged_env=(
                resolved_hook_env
                if (
                    hook_service is not None
                    and (resolved_hook_env := hook_service.get_run_env(request.run_id))
                )
                else None
            ),
            llm_http_client_cache_scope=llm_http_client_cache_scope_for_request(
                request
            ),
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
            tool_registry=self._tool_registry,
            role_registry=self._role_registry,
            mcp_registry=self._mcp_registry,
            skill_registry=self._skill_registry,
        )
        return prepared_prompt, history, prepared_system_prompt, cast(object, agent)

    def _coerce_history_to_provider_safe_sequence(
        self,
        *,
        request: LLMRequest,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        return self._prompt_history_service().coerce_history_to_provider_safe_sequence(
            request=request,
            history=history,
        )

    def _first_tool_replayable_history_index(
        self,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> int:
        return self._prompt_history_service().first_tool_replayable_history_index(
            history
        )

    def _build_history_replay_bridge_message(
        self,
        *,
        request: LLMRequest,
    ) -> ModelRequest | None:
        return self._prompt_history_service().build_history_replay_bridge_message(
            request=request
        )

    def _build_history_replay_bridge_prompt(
        self,
        *,
        request: LLMRequest,
    ) -> str:
        return self._prompt_history_service().build_history_replay_bridge_prompt(
            request=request
        )

    async def _estimate_compaction_budget(
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
        return await self._prompt_history_service().estimate_compaction_budget(
            request=request,
            history=history,
            system_prompt=system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )

    async def _build_model_settings(
        self,
        *,
        request: LLMRequest,
        history: Sequence[ModelRequest | ModelResponse],
        system_prompt: str,
        reserve_user_prompt_tokens: bool,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
    ) -> OpenAIChatModelSettings:
        model_settings: OpenAIChatModelSettings = {
            "openai_continuous_usage_stats": True,
            "temperature": self._config.sampling.temperature,
            "top_p": self._config.sampling.top_p,
        }
        max_tokens = await self._safe_max_output_tokens(
            request=request,
            history=history,
            system_prompt=system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )
        if max_tokens is not None:
            model_settings["max_tokens"] = max_tokens
        if request.thinking.enabled and request.thinking.effort is not None:
            model_settings["openai_reasoning_effort"] = request.thinking.effort
        return model_settings

    async def _safe_max_output_tokens(
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
        return await self._prompt_history_service().safe_max_output_tokens(
            request=request,
            history=history,
            system_prompt=system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )

    def _estimated_tool_context_tokens(
        self,
        *,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
        estimated_mcp_context_tokens: int | None = None,
    ) -> int:
        return self._prompt_history_service().estimated_tool_context_tokens(
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
            estimated_mcp_context_tokens=estimated_mcp_context_tokens,
        )

    async def _estimated_mcp_context_tokens(
        self,
        *,
        allowed_mcp_servers: tuple[str, ...],
    ) -> int:
        return await self._prompt_history_service().estimated_mcp_context_tokens(
            allowed_mcp_servers=allowed_mcp_servers
        )

    def _estimate_mcp_tool_schema_tokens(
        self,
        *,
        server_name: str,
        tool_schemas: tuple[McpToolSchema, ...],
    ) -> int:
        return self._prompt_history_service().estimate_mcp_tool_schema_tokens(
            server_name=server_name,
            tool_schemas=tool_schemas,
        )

    def _estimated_mcp_context_tokens_fallback(
        self,
        *,
        allowed_mcp_servers: tuple[str, ...],
    ) -> int:
        return self._prompt_history_service().estimated_mcp_context_tokens_fallback(
            allowed_mcp_servers=allowed_mcp_servers
        )

    async def _maybe_compact_history(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        source_history: Sequence[ModelRequest | ModelResponse] | None = None,
        conversation_id: str,
        budget: ConversationCompactionBudget,
        estimated_tokens_before_microcompact: int | None = None,
        estimated_tokens_after_microcompact: int | None = None,
    ) -> list[ModelRequest | ModelResponse]:
        return await self._prompt_history_service().maybe_compact_history(
            request=request,
            history=history,
            source_history=source_history,
            conversation_id=conversation_id,
            budget=budget,
            estimated_tokens_before_microcompact=estimated_tokens_before_microcompact,
            estimated_tokens_after_microcompact=estimated_tokens_after_microcompact,
        )

    def _inject_compaction_summary(
        self,
        *,
        session_id: str,
        conversation_id: str,
        system_prompt: str,
    ) -> str:
        return self._prompt_history_service().inject_compaction_summary(
            session_id=session_id,
            conversation_id=conversation_id,
            system_prompt=system_prompt,
        )

    async def _apply_user_prompt_hooks(
        self,
        request: LLMRequest,
    ) -> tuple[LLMRequest, tuple[str, ...]]:
        return await self._prompt_history_service().apply_user_prompt_hooks(request)

    def _resolve_hook_prompt_text(self, request: LLMRequest) -> str:
        return self._prompt_history_service().resolve_hook_prompt_text(request)

    def _persist_hook_system_context_if_needed(
        self,
        *,
        request: LLMRequest,
        contexts: tuple[str, ...],
    ) -> None:
        self._prompt_history_service().persist_hook_system_context_if_needed(
            request=request,
            contexts=contexts,
        )

    async def _persist_hook_system_context_if_needed_async(
        self,
        *,
        request: LLMRequest,
        contexts: tuple[str, ...],
    ) -> None:
        await (
            self._prompt_history_service().persist_hook_system_context_if_needed_async(
                request=request,
                contexts=contexts,
            )
        )

    def _persist_user_prompt_if_needed(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        content: UserPromptContent | None,
    ) -> tuple[list[ModelRequest | ModelResponse], bool]:
        return self._prompt_history_service().persist_user_prompt_if_needed(
            request=request,
            history=history,
            content=content,
            filter_model_messages=self._filter_model_messages,
        )

    async def _persist_user_prompt_if_needed_async(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        content: UserPromptContent | None,
    ) -> tuple[list[ModelRequest | ModelResponse], bool]:
        return await self._prompt_history_service().persist_user_prompt_if_needed_async(
            request=request,
            history=history,
            content=content,
            filter_model_messages=self._filter_model_messages,
        )

    def _history_ends_with_user_prompt(
        self,
        history: Sequence[ModelRequest | ModelResponse],
        content_key: str,
    ) -> bool:
        return self._prompt_history_service().history_ends_with_user_prompt(
            history=history,
            content_key=content_key,
        )

    def _drop_duplicate_leading_request(
        self,
        *,
        history: Sequence[ModelRequest | ModelResponse],
        new_messages: list[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        return self._prompt_history_service().drop_duplicate_leading_request(
            history=history,
            new_messages=new_messages,
        )

    def _model_requests_match_user_prompt(
        self,
        left: ModelRequest,
        right: ModelRequest,
    ) -> bool:
        return self._prompt_history_service().model_requests_match_user_prompt(
            left,
            right,
        )

    def _model_request_matches_tool_result_replay(
        self,
        *,
        history: Sequence[ModelRequest | ModelResponse],
        replayed_request: ModelRequest,
    ) -> bool:
        return self._prompt_history_service().model_request_matches_tool_result_replay(
            history=history,
            replayed_request=replayed_request,
        )

    def _tool_result_replay_parts(
        self,
        *,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> tuple[list[ToolReturnPart], list[UserPromptPart]] | None:
        return self._prompt_history_service().tool_result_replay_parts(history=history)

    def _mixed_tool_result_replay_parts(
        self,
        message: ModelRequest | ModelResponse,
    ) -> tuple[list[ToolReturnPart], list[UserPromptPart]] | None:
        return self._prompt_history_service().mixed_tool_result_replay_parts(message)

    def _model_request_contains_only_tool_returns(
        self,
        message: ModelRequest,
    ) -> bool:
        return self._prompt_history_service().model_request_contains_only_tool_returns(
            message
        )

    def _model_request_contains_only_user_prompts(
        self,
        message: ModelRequest,
    ) -> bool:
        return self._prompt_history_service().model_request_contains_only_user_prompts(
            message
        )

    def _user_prompt_parts_key(
        self,
        *,
        parts: Sequence[ModelRequestPart],
    ) -> str | None:
        return self._prompt_history_service().user_prompt_parts_key(parts=parts)

    def _tool_return_parts_match(
        self,
        *,
        expected_part: ToolReturnPart,
        actual_part: ToolReturnPart,
    ) -> bool:
        return self._prompt_history_service().tool_return_parts_match(
            expected_part=expected_part,
            actual_part=actual_part,
        )

    def _extract_user_prompt_text(self, message: ModelRequest) -> str | None:
        return self._prompt_history_service().extract_user_prompt_text(message)

    def _current_request_prompt_content(
        self,
        request: LLMRequest,
    ) -> UserPromptContent | None:
        return self._prompt_history_service().current_request_prompt_content(request)

    def _request_has_prompt_content(self, request: LLMRequest) -> bool:
        return self._prompt_history_service().request_has_prompt_content(request)

    def _validate_request_input_capabilities(self, request: LLMRequest) -> None:
        self._prompt_history_service().validate_request_input_capabilities(request)

    def _validate_history_input_capabilities(
        self,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> None:
        self._prompt_history_service().validate_history_input_capabilities(history)

    def _hydrate_history_media_content(
        self,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        return self._prompt_history_service().hydrate_history_media_content(history)

    def _provider_history_for_model_turn(
        self,
        *,
        request: LLMRequest,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        return self._prompt_history_service().provider_history_for_model_turn(
            request=request,
            history=history,
        )

    def _provider_history_for_model_turn_details(
        self,
        *,
        request: LLMRequest,
        history: Sequence[ModelRequest | ModelResponse],
        consumed_tool_call_ids: set[str] | None = None,
    ) -> tuple[list[ModelRequest | ModelResponse], tuple[str, ...]]:
        return self._prompt_history_service().provider_history_for_model_turn_details(
            request=request,
            history=history,
            consumed_tool_call_ids=consumed_tool_call_ids,
        )

    def _prompt_content_provider_service(self) -> object | None:
        return self._prompt_history_service().prompt_content_provider_service()

    def _commit_ready_messages(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
        published_tool_outcome_ids: set[str] | None = None,
    ) -> tuple[
        list[ModelRequest | ModelResponse],
        list[ModelRequest | ModelResponse],
        bool,
        bool,
    ]:
        return self._message_commit_service().commit_ready_messages(
            request=request,
            history=history,
            pending_messages=pending_messages,
            last_committable_index=self._last_committable_index,
            has_tool_input_validation_failures=(
                self._has_tool_input_validation_failures
            ),
            normalize_committable_messages=self._normalize_committable_messages,
            workspace_id=self._workspace_id,
            conversation_id=self._conversation_id,
            publish_committed_tool_outcome_events_from_messages=(
                self._publish_committed_tool_outcome_events_from_messages
            ),
            filter_model_messages=self._filter_model_messages,
            has_tool_side_effect_messages=self._has_tool_side_effect_messages,
            published_tool_outcome_ids=published_tool_outcome_ids,
        )

    async def _commit_ready_messages_async(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
        published_tool_outcome_ids: set[str] | None = None,
    ) -> tuple[
        list[ModelRequest | ModelResponse],
        list[ModelRequest | ModelResponse],
        bool,
        bool,
    ]:
        return await self._message_commit_service().commit_ready_messages_async(
            request=request,
            history=history,
            pending_messages=pending_messages,
            last_committable_index=self._last_committable_index,
            has_tool_input_validation_failures=(
                self._has_tool_input_validation_failures
            ),
            normalize_committable_messages=self._normalize_committable_messages,
            workspace_id=self._workspace_id,
            conversation_id=self._conversation_id,
            publish_committed_tool_outcome_events_from_messages=(
                self._publish_committed_tool_outcome_events_from_messages_async
            ),
            filter_model_messages=self._filter_model_messages,
            has_tool_side_effect_messages=self._has_tool_side_effect_messages,
            published_tool_outcome_ids=published_tool_outcome_ids,
        )

    def _commit_all_safe_messages(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
        published_tool_outcome_ids: set[str] | None = None,
    ) -> tuple[
        list[ModelRequest | ModelResponse],
        list[ModelRequest | ModelResponse],
        bool,
        bool,
    ]:
        return self._message_commit_service().commit_all_safe_messages(
            request=request,
            history=history,
            pending_messages=pending_messages,
            commit_ready_messages=self._commit_ready_messages,
            last_committable_index=self._last_committable_index,
            published_tool_outcome_ids=published_tool_outcome_ids,
        )

    async def _commit_all_safe_messages_async(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
        published_tool_outcome_ids: set[str] | None = None,
    ) -> tuple[
        list[ModelRequest | ModelResponse],
        list[ModelRequest | ModelResponse],
        bool,
        bool,
    ]:
        return await self._message_commit_service().commit_all_safe_messages_async(
            request=request,
            history=history,
            pending_messages=pending_messages,
            commit_ready_messages=self._commit_ready_messages_async,
            last_committable_index=self._last_committable_index,
            published_tool_outcome_ids=published_tool_outcome_ids,
        )

    def _has_pending_tool_calls(
        self,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> bool:
        return self._message_commit_service().has_pending_tool_calls(
            messages,
            last_committable_index=self._last_committable_index,
        )

    @staticmethod
    def _has_tool_input_validation_failures(
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> bool:
        return MessageCommitService(
            message_repo=_NullCommitMessageRepo()
        ).has_tool_input_validation_failures(messages)

    def _normalize_committable_messages(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        return self._message_commit_service().normalize_committable_messages(
            request=request,
            messages=messages,
        )

    @staticmethod
    def _normalize_tool_call_args_for_replay(
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> None:
        for message in messages:
            if not isinstance(message, ModelResponse):
                continue
            for part in message.parts:
                if not isinstance(part, ToolCallPart) or not isinstance(part.args, str):
                    continue
                repaired = repair_tool_args(part.args)
                if not repaired.repair_applied and not repaired.fallback_invalid_json:
                    continue
                part.args = repaired.arguments_json

    def _last_committable_index(
        self,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> int:
        return self._message_commit_service().last_committable_index(messages)

    def _maybe_enrich_tool_result_payload(
        self,
        *,
        tool_name: str,
        result_payload: JsonValue,
    ) -> JsonValue:
        descriptor = describe_builtin_tool(tool_name)
        if descriptor is None:
            try:
                server_names = self._mcp_registry.list_names()
            except AttributeError:
                server_names = ()
            for server_name in server_names:
                if not tool_name.startswith(f"{server_name}_"):
                    continue
                descriptor = describe_mcp_tool(
                    effective_tool_name=tool_name,
                    server_name=server_name,
                    source_scope=self._mcp_registry.get_spec(server_name).source,
                )
                break
        if descriptor is None:
            return result_payload
        if isinstance(result_payload, dict):
            payload_map = cast(dict[str, JsonValue], result_payload)
            if isinstance(payload_map.get("computer"), dict):
                return result_payload
            if "ok" in payload_map:
                next_payload = dict(payload_map)
                next_payload["data"] = self._computer_payload_from_raw_result(
                    descriptor=descriptor,
                    raw_result=payload_map.get("data"),
                )
                return cast(JsonValue, next_payload)
        return self._computer_payload_from_raw_result(
            descriptor=descriptor,
            raw_result=result_payload,
        )

    def _computer_payload_from_raw_result(
        self,
        *,
        descriptor: ComputerActionDescriptor,
        raw_result: JsonValue | None,
    ) -> JsonValue:
        if isinstance(raw_result, dict):
            raw_map = cast(dict[str, JsonValue], raw_result)
            if isinstance(raw_map.get("computer"), dict):
                return raw_result
            content = _content_payload(raw_map.get("content"))
            observation = _object_payload(raw_map.get("observation"))
            data = {
                key: value
                for key, value in raw_map.items()
                if key not in {"text", "content", "computer", "observation"}
            }
            return cast(
                JsonValue,
                build_computer_tool_payload(
                    descriptor=descriptor,
                    text=_display_text(raw_result),
                    content=content,
                    observation=observation,
                    data=data or None,
                ),
            )
        return cast(
            JsonValue,
            build_computer_tool_payload(
                descriptor=descriptor,
                text=_display_text(raw_result),
            ),
        )

    def _publish_tool_call_events_from_messages(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
        published_tool_call_ids: set[str] | None = None,
    ) -> bool:
        return self._event_publishing_service().publish_tool_call_events_from_messages(
            request=request,
            messages=messages,
            published_tool_call_ids=published_tool_call_ids,
        )

    async def _publish_tool_call_events_from_messages_async(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
        published_tool_call_ids: set[str] | None = None,
    ) -> bool:
        return await self._event_publishing_service().publish_tool_call_events_from_messages_async(
            request=request,
            messages=messages,
            published_tool_call_ids=published_tool_call_ids,
        )

    def _publish_committed_tool_outcome_events_from_messages(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
        published_tool_outcome_ids: set[str] | None = None,
    ) -> bool:
        return self._event_publishing_service().publish_committed_tool_outcome_events_from_messages(
            request=request,
            messages=messages,
            to_json_compatible=self._to_json_compatible,
            maybe_enrich_tool_result_payload=self._maybe_enrich_tool_result_payload,
            tool_result_already_emitted_from_runtime=(
                self._tool_result_already_emitted_from_runtime
            ),
            published_tool_outcome_ids=published_tool_outcome_ids,
        )

    async def _publish_committed_tool_outcome_events_from_messages_async(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
        published_tool_outcome_ids: set[str] | None = None,
    ) -> bool:
        return await self._event_publishing_service().publish_committed_tool_outcome_events_from_messages_async(
            request=request,
            messages=messages,
            to_json_compatible=self._to_json_compatible,
            maybe_enrich_tool_result_payload=self._maybe_enrich_tool_result_payload,
            tool_result_already_emitted_from_runtime=(
                self._tool_result_already_emitted_from_runtime_async
            ),
            published_tool_outcome_ids=published_tool_outcome_ids,
        )

    def _tool_result_already_emitted_from_runtime(
        self,
        *,
        request: LLMRequest,
        tool_name: str,
        tool_call_id: str,
    ) -> bool:
        return (
            self._tool_result_state_service().tool_result_already_emitted_from_runtime(
                request=request,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                shared_store=self._shared_store,
                load_tool_call_state=load_tool_call_state,
            )
        )

    async def _tool_result_already_emitted_from_runtime_async(
        self,
        *,
        request: LLMRequest,
        tool_name: str,
        tool_call_id: str,
    ) -> bool:
        return await self._tool_result_state_service().tool_result_already_emitted_from_runtime_async(
            request=request,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            shared_store=self._shared_store,
            load_tool_call_state=load_tool_call_state_async,
        )

    def _to_json(self, obj: object) -> str:
        try:
            return json.dumps(obj, ensure_ascii=False, default=str)
        except Exception:
            return json.dumps({"error": "unserializable", "repr": str(obj)})

    def _to_json_compatible(self, value: object) -> JsonValue:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, list):
            entries = cast(list[object], value)
            return [self._to_json_compatible(entry) for entry in entries]
        if isinstance(value, dict):
            entries = cast(dict[object, object], value)
            return {
                str(key): self._to_json_compatible(entry)
                for key, entry in entries.items()
            }
        return str(value)

    def _workspace_id(self, request: LLMRequest) -> str:
        return request.workspace_id

    def _conversation_id(self, request: LLMRequest) -> str:
        return request.conversation_id or build_conversation_id(
            request.session_id,
            request.role_id,
        )

    def _resolve_tool_approval_policy(self, run_id: str) -> ToolApprovalPolicy:
        try:
            yolo = self._run_intent_repo.get(run_id).yolo
        except KeyError:
            yolo = False
        return self._tool_approval_policy.with_yolo(yolo)
