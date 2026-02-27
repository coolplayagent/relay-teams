from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from agent_teams.tools.registry.registry import ToolRegistry
from agent_teams.tools.runtime import ToolDeps


def build_collaboration_agent(
    *,
    model_name: str,
    base_url: str,
    api_key: str,
    system_prompt: str,
    allowed_tools: tuple[str, ...],
    tool_registry: ToolRegistry,
) -> Agent[ToolDeps, str]:
    model = OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(base_url=base_url, api_key=api_key),
    )
    agent: Agent[ToolDeps, str] = Agent(
        model=model,
        deps_type=ToolDeps,
        output_type=str,
        system_prompt=system_prompt,
    )
    specs = tool_registry.require(allowed_tools)
    for spec in specs:
        spec.mount(agent)
    return agent
