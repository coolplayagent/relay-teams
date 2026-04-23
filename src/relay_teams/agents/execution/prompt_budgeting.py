# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart

from relay_teams.agents.execution.conversation_compaction import (
    ConversationCompactionBudget,
    ConversationTokenEstimator,
    build_conversation_compaction_budget,
)
from relay_teams.computer import describe_builtin_tool
from relay_teams.logger import get_logger, log_event
from relay_teams.mcp.mcp_models import McpToolSchema
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.providers.model_config import ModelEndpointConfig
from relay_teams.providers.provider_contracts import LLMRequest

LOGGER = get_logger(__name__)

ESTIMATED_TOKEN_BYTES = 4
ESTIMATED_TOKEN_OVERHEAD = 8
COMPACTION_OUTPUT_RESERVE_TOKENS = 32
MIN_AVAILABLE_OUTPUT_TOKENS = 1
BUILTIN_TOOL_CONTEXT_CHARS = 200
EXTERNAL_TOOL_CONTEXT_CHARS = 600
SKILL_CONTEXT_CHARS = 800
MCP_SERVER_CONTEXT_FALLBACK_CHARS = 1_200


class PromptBudgetingService:
    def __init__(
        self,
        *,
        config: ModelEndpointConfig,
        mcp_registry: McpRegistry,
        mcp_tool_context_token_cache: dict[str, int],
    ) -> None:
        self._config = config
        self._mcp_registry = mcp_registry
        self._mcp_tool_context_token_cache = mcp_tool_context_token_cache

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
        del history
        estimated_mcp_context_tokens: int | None = None
        if self._config.context_window is not None and self._config.context_window > 0:
            estimated_mcp_context_tokens = await self.estimated_mcp_context_tokens(
                allowed_mcp_servers=allowed_mcp_servers
            )
        estimator = ConversationTokenEstimator()
        estimated_system_prompt_tokens = max(
            1,
            (len(system_prompt.encode("utf-8")) // ESTIMATED_TOKEN_BYTES)
            + ESTIMATED_TOKEN_OVERHEAD,
        )
        user_prompt = request.prompt_text.strip()
        estimated_user_prompt_tokens = (
            estimator.estimate_message_tokens(
                ModelRequest(parts=[UserPromptPart(content=user_prompt)])
            )
            if reserve_user_prompt_tokens and user_prompt
            else 0
        )
        estimated_tool_context_tokens = self.estimated_tool_context_tokens(
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
            estimated_mcp_context_tokens=estimated_mcp_context_tokens,
        )
        return build_conversation_compaction_budget(
            context_window=self._config.context_window,
            estimated_system_prompt_tokens=estimated_system_prompt_tokens,
            estimated_user_prompt_tokens=estimated_user_prompt_tokens,
            estimated_tool_context_tokens=estimated_tool_context_tokens,
            estimated_output_reserve_tokens=COMPACTION_OUTPUT_RESERVE_TOKENS,
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
        configured_max_tokens = self._config.sampling.max_tokens
        if configured_max_tokens is None:
            return None
        context_window = self._config.context_window
        if context_window is None or context_window <= 0:
            return configured_max_tokens
        estimator = ConversationTokenEstimator()
        estimated_history_tokens = estimator.estimate_history_tokens(history)
        budget = await self.estimate_compaction_budget(
            request=request,
            history=history,
            system_prompt=system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )
        reserved_tokens = estimated_history_tokens + budget.estimated_non_history_tokens
        available_output_tokens = context_window - reserved_tokens
        if available_output_tokens <= 0:
            return MIN_AVAILABLE_OUTPUT_TOKENS
        return max(
            MIN_AVAILABLE_OUTPUT_TOKENS,
            min(configured_max_tokens, available_output_tokens),
        )

    def estimated_tool_context_tokens(
        self,
        *,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
        estimated_mcp_context_tokens: int | None = None,
    ) -> int:
        if not allowed_tools and not allowed_mcp_servers and not allowed_skills:
            return 0
        reserved_chars = 0
        for tool_name in allowed_tools:
            descriptor = describe_builtin_tool(tool_name)
            if descriptor is not None:
                reserved_chars += BUILTIN_TOOL_CONTEXT_CHARS
                continue
            reserved_chars += EXTERNAL_TOOL_CONTEXT_CHARS
        reserved_chars += len(allowed_skills) * SKILL_CONTEXT_CHARS
        builtin_and_skill_tokens = (
            max(
                0,
                (reserved_chars // ESTIMATED_TOKEN_BYTES) + ESTIMATED_TOKEN_OVERHEAD,
            )
            if reserved_chars > 0
            else 0
        )
        mcp_tokens = (
            estimated_mcp_context_tokens
            if estimated_mcp_context_tokens is not None
            else self.estimated_mcp_context_tokens_fallback(
                allowed_mcp_servers=allowed_mcp_servers
            )
        )
        return builtin_and_skill_tokens + mcp_tokens

    async def estimated_mcp_context_tokens(
        self,
        *,
        allowed_mcp_servers: tuple[str, ...],
    ) -> int:
        if not allowed_mcp_servers:
            return 0
        resolved_server_names = self._mcp_registry.resolve_server_names(
            allowed_mcp_servers,
            strict=False,
            consumer="agents.execution.prompt_history",
        )
        total_tokens = 0
        for server_name in resolved_server_names:
            cached_tokens = self._mcp_tool_context_token_cache.get(server_name)
            if cached_tokens is not None:
                total_tokens += cached_tokens
                continue
            try:
                tool_schemas = await self._mcp_registry.list_tool_schemas(server_name)
            except Exception as exc:
                fallback_tokens = self.estimated_mcp_context_tokens_fallback(
                    allowed_mcp_servers=(server_name,),
                )
                total_tokens += fallback_tokens
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="llm.mcp_context_budget.estimate_failed",
                    message=(
                        "Failed to inspect MCP tool schemas for token budgeting; "
                        "falling back to heuristic reserve"
                    ),
                    payload={
                        "server_name": server_name,
                        "fallback_tokens": fallback_tokens,
                    },
                    exc_info=exc,
                )
                continue
            estimated_tokens = self.estimate_mcp_tool_schema_tokens(
                server_name=server_name,
                tool_schemas=tool_schemas,
            )
            self._mcp_tool_context_token_cache[server_name] = estimated_tokens
            total_tokens += estimated_tokens
        return total_tokens

    def estimate_mcp_tool_schema_tokens(
        self,
        *,
        server_name: str,
        tool_schemas: tuple[McpToolSchema, ...],
    ) -> int:
        if not tool_schemas:
            return 0
        serialized_payload = json.dumps(
            [
                {
                    "server": server_name,
                    "tool": schema.model_dump(mode="json"),
                }
                for schema in tool_schemas
            ],
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        return max(
            1,
            (len(serialized_payload) // ESTIMATED_TOKEN_BYTES)
            + ESTIMATED_TOKEN_OVERHEAD,
        )

    def estimated_mcp_context_tokens_fallback(
        self,
        *,
        allowed_mcp_servers: tuple[str, ...],
    ) -> int:
        if not allowed_mcp_servers:
            return 0
        reserved_chars = len(allowed_mcp_servers) * MCP_SERVER_CONTEXT_FALLBACK_CHARS
        return max(
            0,
            (reserved_chars // ESTIMATED_TOKEN_BYTES) + ESTIMATED_TOKEN_OVERHEAD,
        )
