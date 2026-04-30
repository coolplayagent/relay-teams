# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from relay_teams.agents.orchestration.graph_models import OrchestrationGraph


class OrchestrationPreset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    role_ids: tuple[str, ...] = Field(default_factory=tuple)
    orchestration_prompt: str = Field(min_length=1)
    graph: OrchestrationGraph | None = None

    @model_validator(mode="after")
    def validate_role_ids(self) -> OrchestrationPreset:
        if not self.role_ids:
            raise ValueError("orchestration preset must include at least one role")
        if self.graph is None:
            return self
        allowed_role_ids = set(self.role_ids)
        for node in self.graph.nodes:
            if node.role_id not in allowed_role_ids:
                raise ValueError(
                    f"orchestration graph node role_id must be listed in role_ids: {node.role_id}"
                )
        return self


class OrchestrationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_orchestration_preset_id: str = ""
    presets: tuple[OrchestrationPreset, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_defaults(self) -> OrchestrationSettings:
        preset_ids = [preset.preset_id for preset in self.presets]
        if len(preset_ids) != len(set(preset_ids)):
            raise ValueError("orchestration preset ids must be unique")
        if self.presets and not self.default_orchestration_preset_id:
            raise ValueError("default_orchestration_preset_id is required")
        if (
            self.default_orchestration_preset_id
            and self.default_orchestration_preset_id not in preset_ids
        ):
            raise ValueError("default_orchestration_preset_id must reference a preset")
        return self
