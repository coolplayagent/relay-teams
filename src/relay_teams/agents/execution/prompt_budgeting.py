# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart

from relay_teams.agents.execution.conversation_compaction import (
    ConversationCompactionBudget,
    ConversationTokenEstimator,
    build_conversation_compaction_budget,
)
from relay_teams.mcp.runtime_schema_loader import (
    cached_runtime_mcp_server_names,
    should_require_ready_mcp_toolsets,
)
from relay_teams.computer import describe_builtin_tool
from relay_teams.mcp.mcp_discovery_service import McpDiscoveryService
from relay_teams.mcp.mcp_models import McpDiscoveryStatus, McpToolInfo, McpToolSchema
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.providers.model_config import ModelEndpointConfig
from relay_teams.providers.provider_contracts import LLMRequest

ESTIMATED_TOKEN_BYTES = 4
ESTIMATED_TOKEN_OVERHEAD = 8
COMPACTION_OUTPUT_RESERVE_TOKENS = 32
MIN_AVAILABLE_OUTPUT_TOKENS = 1
BUILTIN_TOOL_CONTEXT_CHARS = 200
EXTERNAL_TOOL_CONTEXT_CHARS = 600
SKILL_CONTEXT_CHARS = 800
MCP_SERVER_CONTEXT_FALLBACK_CHARS = 8_000
MCP_DISCOVERED_TOOL_SCHEMA_RESERVE_CHARS = 1_200


class PromptBudgetingService:  # pragma: no cover
    def __init__(
        self,
        *,
        config: ModelEndpointConfig,
        mcp_registry: McpRegistry,
        mcp_tool_context_token_cache: dict[str, int],
        mcp_discovery_service: McpDiscoveryService | None = None,
    ) -> None:
        self._config = config
        self._mcp_registry = mcp_registry
        self._mcp_tool_context_token_cache = mcp_tool_context_token_cache
        self._mcp_discovery_service = mcp_discovery_service

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
        estimated_history_tokens = await asyncio.to_thread(
            ConversationTokenEstimator().estimate_history_tokens,
            tuple(history),
        )
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
        if should_require_ready_mcp_toolsets(
            requested_server_names=allowed_mcp_servers,
            resolved_server_count=len(resolved_server_names),
        ):
            resolved_server_names = cached_runtime_mcp_server_names(
                mcp_registry=self._mcp_registry,
                server_names=resolved_server_names,
            )
            if not resolved_server_names:
                return 0
        total_tokens = 0
        for server_name in resolved_server_names:
            cached_tokens = self._mcp_tool_context_token_cache.get(server_name)
            if cached_tokens is not None:
                total_tokens += cached_tokens
                continue
            discovered_tokens = self._estimated_discovered_mcp_context_tokens(
                server_name=server_name
            )
            if discovered_tokens is not None:
                self._mcp_tool_context_token_cache[server_name] = discovered_tokens
                total_tokens += discovered_tokens
                continue
            fallback_tokens = self.estimated_mcp_context_tokens_fallback(
                allowed_mcp_servers=(server_name,),
            )
            total_tokens += fallback_tokens
        return total_tokens

    def _estimated_discovered_mcp_context_tokens(
        self,
        *,
        server_name: str,
    ) -> int | None:
        if self._mcp_discovery_service is None:
            return None
        summary = self._mcp_discovery_service.get_tools_summary(server_name)
        if summary.status != McpDiscoveryStatus.READY:
            return None
        return self.estimate_mcp_tool_info_tokens(
            server_name=server_name,
            tools=summary.tools,
        )

    @staticmethod
    def estimate_mcp_tool_info_tokens(
        *,
        server_name: str,
        tools: tuple[McpToolInfo, ...],
    ) -> int:
        if not tools:
            return 0
        serialized_payload = json.dumps(
            [
                {
                    "server": server_name,
                    "tool": tool.model_dump(mode="json"),
                    "input_schema_reserve_chars": (
                        MCP_DISCOVERED_TOOL_SCHEMA_RESERVE_CHARS
                    ),
                }
                for tool in tools
            ],
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        reserved_chars = len(tools) * MCP_DISCOVERED_TOOL_SCHEMA_RESERVE_CHARS
        return max(
            1,
            ((len(serialized_payload) + reserved_chars) // ESTIMATED_TOKEN_BYTES)
            + ESTIMATED_TOKEN_OVERHEAD,
        )

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
