# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections.abc import Mapping

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from relay_teams.logger import get_logger, log_event
from relay_teams.mcp.mcp_discovery_service import McpDiscoveryService
from relay_teams.mcp.mcp_models import McpDiscoveryStatus
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.agents.execution.model_builder import (
    build_runtime_chat_model,
)
from relay_teams.net.llm_client import build_llm_http_client
from relay_teams.providers.model_config import (
    CodeAgentAuthConfig,
    DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
    MaaSAuthConfig,
    ModelEndpointConfig,
    ModelRequestHeader,
    ProviderType,
)
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
    model_settings: ModelSettings | None = None,
    ssl_verify: bool | None = None,
    connect_timeout_seconds: float = DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
    merged_env: Mapping[str, str] | None = None,
    llm_http_client_cache_scope: str | None = None,
    allowed_mcp_servers: tuple[str, ...] = (),
    allowed_skills: tuple[str, ...] = (),
    tool_registry: ToolRegistry,
    role_registry: RoleRegistry | None = None,
    mcp_registry: McpRegistry | None = None,
    mcp_discovery_service: McpDiscoveryService | None = None,
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
            if not _mcp_server_is_ready_for_agent(
                server_name,
                discovery_service=mcp_discovery_service,
            ):
                continue
            is_runtime_failed = getattr(
                mcp_registry,
                "is_server_runtime_failed",
                None,
            )
            if callable(is_runtime_failed) and is_runtime_failed(server_name):
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="llm.mcp_toolset.skip_failed",
                    message=(
                        "Skipping MCP server previously marked as failed while "
                        "building coordination agent"
                    ),
                    payload={"server_name": server_name},
                )
                continue
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
    model = build_runtime_chat_model(
        config=ModelEndpointConfig(
            provider=provider_type,
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            headers=headers,
            maas_auth=maas_auth,
            codeagent_auth=codeagent_auth,
            ssl_verify=ssl_verify,
            connect_timeout_seconds=connect_timeout_seconds,
        ),
        http_client=llm_http_client,
        recoverable_openai=True,
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


def _mcp_server_is_ready_for_agent(
    server_name: str,
    *,
    discovery_service: McpDiscoveryService | None,
) -> bool:
    if discovery_service is None:
        return True
    summary = discovery_service.get_tools_summary(server_name)
    if summary.status == McpDiscoveryStatus.READY:
        return True
    log_event(
        LOGGER,
        logging.INFO,
        event="llm.mcp_toolset.skip_not_ready",
        message=(
            "Skipping MCP server that is not ready while building coordination agent"
        ),
        payload={
            "server_name": server_name,
            "discovery_status": summary.status.value,
        },
    )
    return False
