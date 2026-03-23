# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.metrics.models import MetricDefinition


class MetricRegistry:
    def __init__(self, definitions: tuple[MetricDefinition, ...] = ()) -> None:
        self._definitions: dict[str, MetricDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: MetricDefinition) -> None:
        existing = self._definitions.get(definition.name)
        if existing is not None and existing != definition:
            raise ValueError(f"Metric definition conflict: {definition.name}")
        self._definitions[definition.name] = definition

    def get(self, name: str) -> MetricDefinition:
        definition = self._definitions.get(name)
        if definition is None:
            raise KeyError(f"Unknown metric definition: {name}")
        return definition

    def list_definitions(self) -> tuple[MetricDefinition, ...]:
        return tuple(
            self._definitions[name] for name in sorted(self._definitions.keys())
        )
