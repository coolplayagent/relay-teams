# -*- coding: utf-8 -*-
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

from agent_teams.logger import get_logger
from agent_teams.mcp.mcp_models import McpServerSpec, McpToolInfo
from agent_teams.trace import trace_span

if TYPE_CHECKING:
    from pydantic_ai.toolsets.fastmcp import FastMCPToolset

LOGGER = get_logger(__name__)


class McpRegistry:
    def __init__(self, specs: tuple[McpServerSpec, ...] = ()) -> None:
        self._specs = {spec.name: spec for spec in specs}
        self._toolsets: dict[str, FastMCPToolset] = {}

    def get_toolsets(self, names: tuple[str, ...]) -> tuple[FastMCPToolset, ...]:
        with trace_span(
            LOGGER,
            component="mcp.registry",
            operation="get_toolsets",
            attributes={"server_names": list(names)},
        ):
            self.validate_known(names)
            toolsets: list[FastMCPToolset] = []
            for name in names:
                toolsets.append(self._get_or_create_toolset(name))
            return tuple(toolsets)

    def validate_known(self, names: tuple[str, ...]) -> None:
        missing = [name for name in names if name not in self._specs]
        if missing:
            raise ValueError(f"Unknown MCP servers: {missing}")

    def list_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs.keys()))

    def list_specs(self) -> tuple[McpServerSpec, ...]:
        return tuple(self._specs[name] for name in self.list_names())

    def get_spec(self, name: str) -> McpServerSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise ValueError(f"Unknown MCP server: {name}")
        return spec

    async def list_tools(self, name: str) -> tuple[McpToolInfo, ...]:
        with trace_span(
            LOGGER,
            component="mcp.registry",
            operation="list_tools",
            attributes={"server_name": name},
        ):
            toolset = self._get_or_create_toolset(name)
            async with toolset:
                mcp_tools = await toolset.client.list_tools()
            return tuple(
                McpToolInfo(
                    name=str(tool.name),
                    description=tool.description
                    if isinstance(tool.description, str)
                    else "",
                )
                for tool in mcp_tools
            )

    def _get_or_create_toolset(self, name: str) -> FastMCPToolset:
        with trace_span(
            LOGGER,
            component="mcp.registry",
            operation="get_or_create_toolset",
            attributes={"server_name": name},
        ):
            existing = self._toolsets.get(name)
            if existing is not None:
                return existing

            spec = self.get_spec(name)
            toolset_type = _load_fastmcp_toolset_type()
            toolset = toolset_type(spec.config)
            self._toolsets[name] = toolset
            return toolset


def _load_fastmcp_toolset_type() -> type[FastMCPToolset]:
    module = import_module("pydantic_ai.toolsets.fastmcp")
    toolset_type = getattr(module, "FastMCPToolset")
    return cast("type[FastMCPToolset]", toolset_type)
