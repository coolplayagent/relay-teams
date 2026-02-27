from __future__ import annotations

from agent_teams.tools.registry.models import ToolSpec


class ToolRegistry:
    def __init__(self, specs: tuple[ToolSpec, ...]) -> None:
        self._specs = {spec.name: spec for spec in specs}

    def require(self, names: tuple[str, ...]) -> tuple[ToolSpec, ...]:
        missing = [name for name in names if name not in self._specs]
        if missing:
            raise ValueError(f'Unknown tools: {missing}')
        return tuple(self._specs[name] for name in names)

    def validate_known(self, names: tuple[str, ...]) -> None:
        self.require(names)

    def list_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs.keys()))
