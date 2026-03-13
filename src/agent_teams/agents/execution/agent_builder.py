# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from agent_teams.mcp.registry import McpRegistry
from agent_teams.providers.http_client_factory import build_llm_http_client
from agent_teams.providers.model_config import DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS
from agent_teams.skills.registry import SkillRegistry
from agent_teams.tools.registry import ToolRegistry
from agent_teams.tools.runtime import ToolDeps


def build_coordination_agent(
    *,
    model_name: str,
    base_url: str,
    api_key: str,
    system_prompt: str,
    allowed_tools: tuple[str, ...],
    model_settings: OpenAIChatModelSettings | None = None,
    ssl_verify: bool | None = None,
    connect_timeout_seconds: float = DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
    allowed_mcp_servers: tuple[str, ...] = (),
    allowed_skills: tuple[str, ...] = (),
    tool_registry: ToolRegistry,
    mcp_registry: McpRegistry | None = None,
    skill_registry: SkillRegistry | None = None,
) -> Agent[ToolDeps, str]:
    """Build the lean meta-orchestrator for collaboration management.

    It drives the full task lifecycle, evaluates task complexity, and chooses the
    most suitable execution path.
    """
    toolsets = []
    if mcp_registry and allowed_mcp_servers:
        toolsets.extend(mcp_registry.get_toolsets(allowed_mcp_servers))

    skill_tools = []
    if skill_registry and allowed_skills:
        skill_registry.validate_known(allowed_skills)
        skill_tools = skill_registry.get_toolset_tools(allowed_skills)

    llm_http_client = build_llm_http_client(
        connect_timeout_seconds=connect_timeout_seconds,
        ssl_verify=ssl_verify,
    )
    model = OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(
            base_url=base_url,
            api_key=api_key,
            http_client=llm_http_client,
        ),
    )
    agent: Agent[ToolDeps, str] = Agent(
        model=model,
        deps_type=ToolDeps,
        output_type=str,
        system_prompt=system_prompt,
        model_settings=model_settings,
        toolsets=toolsets,
        tools=skill_tools,
        retries=5,
    )
    tool_registers = tool_registry.require(allowed_tools)
    for register in tool_registers:
        register(agent)

    return agent
