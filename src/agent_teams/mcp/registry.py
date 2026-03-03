from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai.toolsets.fastmcp import FastMCPToolset

@dataclass(frozen=True)
class McpServerSpec:
    name: str
    config: Any

class McpRegistry:
    def __init__(self, specs: tuple[McpServerSpec, ...] = ()) -> None:
        self._specs = {spec.name: spec for spec in specs}
        self._toolsets: dict[str, FastMCPToolset] = {}

    def get_toolsets(self, names: tuple[str, ...]) -> tuple[FastMCPToolset, ...]:
        missing = [name for name in names if name not in self._specs]
        if missing:
            raise ValueError(f'Unknown MCP servers: {missing}')
        
        toolsets = []
        for name in names:
            if name not in self._toolsets:
                spec = self._specs[name]
                # FastMCPToolset is already an AbstractToolset and handles its own async methods
                self._toolsets[name] = FastMCPToolset(spec.config)
            toolsets.append(self._toolsets[name])
        return tuple(toolsets)

    def validate_known(self, names: tuple[str, ...]) -> None:
        missing = [name for name in names if name not in self._specs]
        if missing:
            raise ValueError(f'Unknown MCP servers: {missing}')

    def list_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs.keys()))
