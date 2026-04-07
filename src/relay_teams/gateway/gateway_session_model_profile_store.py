# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.providers.model_config import ModelEndpointConfig


class GatewaySessionModelProfileStore:
    def __init__(self) -> None:
        self._profiles: dict[str, ModelEndpointConfig] = {}

    def set(self, internal_session_id: str, profile: ModelEndpointConfig) -> None:
        self._profiles[internal_session_id] = profile

    def get(self, internal_session_id: str) -> ModelEndpointConfig | None:
        return self._profiles.get(internal_session_id)

    def delete(self, internal_session_id: str) -> None:
        self._profiles.pop(internal_session_id, None)
