# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections.abc import Mapping

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModelSettings
from pydantic_ai.profiles.openai import OpenAIModelProfile

from relay_teams.logger import get_logger, log_event
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.agents.execution.recoverable_openai_chat_model import (
    RecoverableOpenAIChatModel as OpenAIChatModel,
)
from relay_teams.net.llm_client import build_llm_http_client
from relay_teams.providers.model_config import (
    CodeAgentAuthConfig,
    DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
    MaaSAuthConfig,
    ModelRequestHeader,
    ProviderType,
)
from relay_teams.providers.openai_support import build_openai_provider_for_endpoint
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.tools.registry import ToolRegistry
from relay_teams.tools.runtime.context import ToolDeps

LOGGER = get_logger(__name__)


def build_coordination_agent(
    *,
    model_name: str,
    base_url: str,
    api_key: str | None,
    headers: tuple[ModelRequestHeader, ...] = (),
    provider_type: ProviderType = ProviderType.OPENAI_COMPATIBLE,
    maas_auth: MaaSAuthConfig | None = None,
    codeagent_auth: CodeAgentAuthConfig | None = None,
    system_prompt: str,
    allowed_tools: tuple[str, ...],
    model_settings: OpenAIChatModelSettings | None = None,
    model_profile: OpenAIModelProfile | None = None,
    ssl_verify: bool | None = None,
    connect_timeout_seconds: float = DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
    merged_env: Mapping[str, str] | None = None,
    llm_http_client_cache_scope: str | None = None,
    allowed_mcp_servers: tuple[str, ...] = (),
    allowed_skills: tuple[str, ...] = (),
    tool_registry: ToolRegistry,
    role_registry: RoleRegistry | None = None,
    mcp_registry: McpRegistry | None = None,
    skill_registry: SkillRegistry | None = None,
) -> Agent[ToolDeps, str]:
    """Build the lean meta-orchestrator for collaboration management.

    It drives the full task lifecycle, evaluates task complexity, and chooses the
    most suitable execution path.
    """
    toolsets = []
    if mcp_registry and allowed_mcp_servers:
        resolved_mcp_servers = mcp_registry.resolve_server_names(
            allowed_mcp_servers,
            strict=False,
            consumer="agents.execution.coordination_agent_builder",
        )
        for server_name in resolved_mcp_servers:
            try:
                toolsets.extend(mcp_registry.get_toolsets((server_name,)))
            except Exception as exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="llm.mcp_toolset.load_failed",
                    message=(
                        "Failed to initialize MCP toolset for coordination agent; "
                        "continuing without this MCP server"
                    ),
                    payload={"server_name": server_name},
                    exc_info=exc,
                )

    skill_tools = []
    if skill_registry and allowed_skills:
        resolved_skills = skill_registry.resolve_known(
            allowed_skills,
            strict=False,
            consumer="agents.execution.coordination_agent_builder",
        )
        skill_tools = skill_registry.get_toolset_tools(resolved_skills)

    llm_http_client = build_llm_http_client(
        merged_env=merged_env,
        connect_timeout_seconds=connect_timeout_seconds,
        cache_scope=llm_http_client_cache_scope,
        ssl_verify=ssl_verify,
    )
    model = OpenAIChatModel(
        model_name,
        provider=build_openai_provider_for_endpoint(
            base_url=base_url,
            api_key=api_key,
            headers=headers,
            provider_type=provider_type,
            maas_auth=maas_auth,
            codeagent_auth=codeagent_auth,
            ssl_verify=ssl_verify,
            connect_timeout_seconds=connect_timeout_seconds,
            http_client=llm_http_client,
        ),
        profile=model_profile,
    )
    agent: Agent[ToolDeps, str] = Agent(
        model=model,
        deps_type=ToolDeps,
        output_type=str,
        instructions=system_prompt,
        model_settings=model_settings,
        toolsets=toolsets,
        tools=skill_tools,
        retries=5,
    )
    if role_registry is not None:
        setattr(agent, "_agent_teams_role_registry", role_registry)
    tool_registers = tool_registry.require(
        tool_registry.resolve_known(
            allowed_tools,
            strict=False,
            consumer="agents.execution.coordination_agent_builder",
        )
    )
    for register in tool_registers:
        register(agent)

    return agent
