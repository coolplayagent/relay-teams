# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from relay_teams.external_agents.models import (
    CustomTransportConfig,
    ExternalAgentCollection,
    ExternalAgentConfig,
    ExternalAgentOption,
    ExternalAgentSecretBinding,
    ExternalAgentSummary,
    StdioTransportConfig,
    StreamableHttpTransportConfig,
)
from relay_teams.external_agents.secret_store import (
    ExternalAgentSecretStore,
    get_external_agent_secret_store,
)

_CONFIG_FILE_NAME = "agents.json"


class ExternalAgentConfigService:
    def __init__(
        self,
        *,
        config_dir: Path,
        secret_store: ExternalAgentSecretStore | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._config_path = config_dir / _CONFIG_FILE_NAME
        self._secret_store = (
            get_external_agent_secret_store() if secret_store is None else secret_store
        )

    def list_agents(self) -> tuple[ExternalAgentSummary, ...]:
        return tuple(
            ExternalAgentSummary(
                agent_id=agent.agent_id,
                name=agent.name,
                description=agent.description,
                transport=agent.transport.transport,
            )
            for agent in self._load_collection().agents
        )

    def list_agent_options(self) -> tuple[ExternalAgentOption, ...]:
        return tuple(
            ExternalAgentOption(
                agent_id=agent.agent_id,
                name=agent.name,
                transport=agent.transport.transport,
            )
            for agent in self._load_collection().agents
        )

    def get_agent(self, agent_id: str) -> ExternalAgentConfig:
        normalized_agent_id = _normalize_required_text(agent_id, "agent_id")
        for agent in self._load_collection().agents:
            if agent.agent_id == normalized_agent_id:
                return self._attach_secret_status(self._normalize_agent(agent))
        raise KeyError(f"Unknown external agent: {normalized_agent_id}")

    def save_agent(
        self,
        agent_id: str,
        config: ExternalAgentConfig,
    ) -> ExternalAgentConfig:
        normalized = self._normalize_agent(config)
        if normalized.agent_id != _normalize_required_text(agent_id, "agent_id"):
            raise ValueError("Path agent_id must match payload agent_id")

        current = None
        collection = self._load_collection()
        next_agents: list[ExternalAgentConfig] = []
        for existing in collection.agents:
            if existing.agent_id == normalized.agent_id:
                current = existing
                continue
            next_agents.append(existing)

        persisted = self._prepare_for_persistence(
            incoming=normalized,
            current=current,
        )
        next_agents.append(persisted)
        next_agents.sort(key=lambda item: (item.name.casefold(), item.agent_id))
        self._write_collection(ExternalAgentCollection(agents=tuple(next_agents)))
        return self.get_agent(normalized.agent_id)

    def delete_agent(self, agent_id: str) -> None:
        normalized_agent_id = _normalize_required_text(agent_id, "agent_id")
        collection = self._load_collection()
        next_agents = tuple(
            agent
            for agent in collection.agents
            if agent.agent_id != normalized_agent_id
        )
        if len(next_agents) == len(collection.agents):
            raise KeyError(f"Unknown external agent: {normalized_agent_id}")
        self._write_collection(ExternalAgentCollection(agents=next_agents))
        self._secret_store.delete_agent(
            config_dir=self._config_dir,
            agent_id=normalized_agent_id,
        )

    def resolve_runtime_agent(self, agent_id: str) -> ExternalAgentConfig:
        config = self.get_agent(agent_id)
        if isinstance(config.transport, StdioTransportConfig):
            return config.model_copy(
                update={
                    "transport": config.transport.model_copy(
                        update={
                            "env": self._resolve_runtime_bindings(
                                agent_id=config.agent_id,
                                bindings=config.transport.env,
                                kind="env",
                            )
                        }
                    )
                }
            )
        if isinstance(config.transport, StreamableHttpTransportConfig):
            return config.model_copy(
                update={
                    "transport": config.transport.model_copy(
                        update={
                            "headers": self._resolve_runtime_bindings(
                                agent_id=config.agent_id,
                                bindings=config.transport.headers,
                                kind="header",
                            )
                        }
                    )
                }
            )
        return config

    def _load_collection(self) -> ExternalAgentCollection:
        if not self._config_path.exists():
            return ExternalAgentCollection()
        raw = self._config_path.read_text(encoding="utf-8").strip()
        if not raw:
            return ExternalAgentCollection()
        payload = json.loads(raw)
        return ExternalAgentCollection.model_validate(
            _strip_legacy_stdio_workdir(payload)
        )

    def _write_collection(self, collection: ExternalAgentCollection) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            collection.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def _prepare_for_persistence(
        self,
        *,
        incoming: ExternalAgentConfig,
        current: ExternalAgentConfig | None,
    ) -> ExternalAgentConfig:
        if isinstance(incoming.transport, StdioTransportConfig):
            return incoming.model_copy(
                update={
                    "transport": incoming.transport.model_copy(
                        update={
                            "env": self._persist_secret_bindings(
                                agent_id=incoming.agent_id,
                                bindings=incoming.transport.env,
                                current_bindings=(
                                    current.transport.env
                                    if isinstance(current, ExternalAgentConfig)
                                    and isinstance(
                                        current.transport, StdioTransportConfig
                                    )
                                    else ()
                                ),
                                kind="env",
                            )
                        }
                    )
                }
            )
        if isinstance(incoming.transport, StreamableHttpTransportConfig):
            return incoming.model_copy(
                update={
                    "transport": incoming.transport.model_copy(
                        update={
                            "headers": self._persist_secret_bindings(
                                agent_id=incoming.agent_id,
                                bindings=incoming.transport.headers,
                                current_bindings=(
                                    current.transport.headers
                                    if isinstance(current, ExternalAgentConfig)
                                    and isinstance(
                                        current.transport,
                                        StreamableHttpTransportConfig,
                                    )
                                    else ()
                                ),
                                kind="header",
                            )
                        }
                    )
                }
            )
        if isinstance(incoming.transport, CustomTransportConfig):
            return incoming
        raise ValueError(
            f"Unsupported external agent transport: {incoming.transport.transport.value}"
        )

    def _persist_secret_bindings(
        self,
        *,
        agent_id: str,
        bindings: tuple[ExternalAgentSecretBinding, ...],
        current_bindings: tuple[ExternalAgentSecretBinding, ...],
        kind: str,
    ) -> tuple[ExternalAgentSecretBinding, ...]:
        existing_names = {binding.name: binding for binding in current_bindings}
        next_bindings: list[ExternalAgentSecretBinding] = []
        seen_names: set[str] = set()
        for binding in bindings:
            normalized = _normalize_secret_binding(binding)
            if normalized.name in seen_names:
                raise ValueError(
                    f"Duplicate {kind} binding name for external agent {agent_id}: {normalized.name}"
                )
            seen_names.add(normalized.name)
            if normalized.secret:
                value = _normalize_optional_text(normalized.value)
                if value is not None:
                    self._secret_store.set_secret(
                        config_dir=self._config_dir,
                        agent_id=agent_id,
                        kind=kind,
                        name=normalized.name,
                        value=value,
                    )
                elif normalized.configured is False:
                    self._secret_store.delete_secret(
                        config_dir=self._config_dir,
                        agent_id=agent_id,
                        kind=kind,
                        name=normalized.name,
                    )
                elif (
                    normalized.name not in existing_names
                    and self._secret_store.get_secret(
                        config_dir=self._config_dir,
                        agent_id=agent_id,
                        kind=kind,
                        name=normalized.name,
                    )
                    is None
                ):
                    raise ValueError(
                        f"Secret value is required for {kind} binding {normalized.name}"
                    )
                next_bindings.append(
                    normalized.model_copy(update={"value": None, "configured": False})
                )
                continue
            plain_value = _normalize_optional_text(normalized.value)
            if plain_value is None:
                raise ValueError(
                    f"Non-secret {kind} binding {normalized.name} requires a value"
                )
            next_bindings.append(
                normalized.model_copy(
                    update={
                        "value": plain_value,
                        "configured": True,
                    }
                )
            )
        return tuple(next_bindings)

    def _attach_secret_status(self, config: ExternalAgentConfig) -> ExternalAgentConfig:
        if isinstance(config.transport, StdioTransportConfig):
            return config.model_copy(
                update={
                    "transport": config.transport.model_copy(
                        update={
                            "env": self._attach_bindings(
                                agent_id=config.agent_id,
                                bindings=config.transport.env,
                                kind="env",
                            )
                        }
                    )
                }
            )
        if isinstance(config.transport, StreamableHttpTransportConfig):
            return config.model_copy(
                update={
                    "transport": config.transport.model_copy(
                        update={
                            "headers": self._attach_bindings(
                                agent_id=config.agent_id,
                                bindings=config.transport.headers,
                                kind="header",
                            )
                        }
                    )
                }
            )
        return config

    def _attach_bindings(
        self,
        *,
        agent_id: str,
        bindings: tuple[ExternalAgentSecretBinding, ...],
        kind: str,
    ) -> tuple[ExternalAgentSecretBinding, ...]:
        next_bindings: list[ExternalAgentSecretBinding] = []
        for binding in bindings:
            if binding.secret:
                configured = (
                    self._secret_store.get_secret(
                        config_dir=self._config_dir,
                        agent_id=agent_id,
                        kind=kind,
                        name=binding.name,
                    )
                    is not None
                )
                next_bindings.append(
                    binding.model_copy(update={"value": None, "configured": configured})
                )
                continue
            next_bindings.append(
                binding.model_copy(
                    update={
                        "configured": _normalize_optional_text(binding.value)
                        is not None
                    }
                )
            )
        return tuple(next_bindings)

    def _resolve_runtime_bindings(
        self,
        *,
        agent_id: str,
        bindings: tuple[ExternalAgentSecretBinding, ...],
        kind: str,
    ) -> tuple[ExternalAgentSecretBinding, ...]:
        next_bindings: list[ExternalAgentSecretBinding] = []
        for binding in bindings:
            if not binding.secret:
                next_bindings.append(binding)
                continue
            secret_value = self._secret_store.get_secret(
                config_dir=self._config_dir,
                agent_id=agent_id,
                kind=kind,
                name=binding.name,
            )
            next_bindings.append(
                binding.model_copy(
                    update={
                        "value": secret_value,
                        "configured": secret_value is not None,
                    }
                )
            )
        return tuple(next_bindings)

    def _normalize_agent(self, config: ExternalAgentConfig) -> ExternalAgentConfig:
        agent_id = _normalize_required_text(config.agent_id, "agent_id")
        name = _normalize_required_text(config.name, "name")
        description = str(config.description or "").strip()
        if isinstance(config.transport, StdioTransportConfig):
            transport = config.transport.model_copy(
                update={
                    "command": _normalize_required_text(
                        config.transport.command,
                        "command",
                    ),
                    "args": tuple(
                        _normalize_required_text(item, "arg")
                        for item in config.transport.args
                    ),
                    "env": tuple(
                        _normalize_secret_binding(binding)
                        for binding in config.transport.env
                    ),
                }
            )
        elif isinstance(config.transport, StreamableHttpTransportConfig):
            transport = config.transport.model_copy(
                update={
                    "url": _normalize_required_text(config.transport.url, "url"),
                    "headers": tuple(
                        _normalize_secret_binding(binding)
                        for binding in config.transport.headers
                    ),
                }
            )
        elif isinstance(config.transport, CustomTransportConfig):
            transport = config.transport.model_copy(
                update={
                    "adapter_id": _normalize_required_text(
                        config.transport.adapter_id,
                        "adapter_id",
                    )
                }
            )
        else:  # pragma: no cover - defensive guard for future transports
            raise ValueError(
                f"Unsupported external agent transport: {config.transport.transport.value}"
            )
        return config.model_copy(
            update={
                "agent_id": agent_id,
                "name": name,
                "description": description,
                "transport": transport,
            }
        )


def _normalize_secret_binding(
    binding: ExternalAgentSecretBinding,
) -> ExternalAgentSecretBinding:
    return binding.model_copy(
        update={
            "name": _normalize_required_text(binding.name, "binding_name"),
            "value": _normalize_optional_text(binding.value),
            "configured": binding.configured is True,
        }
    )


def _normalize_required_text(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _strip_legacy_stdio_workdir(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload
    raw_agents = payload.get("agents")
    if not isinstance(raw_agents, list):
        return payload
    agents: list[object] = []
    for raw_agent in raw_agents:
        if not isinstance(raw_agent, dict):
            agents.append(raw_agent)
            continue
        raw_transport = raw_agent.get("transport")
        if not isinstance(raw_transport, dict):
            agents.append(raw_agent)
            continue
        if raw_transport.get("transport") != "stdio":
            agents.append(raw_agent)
            continue
        agents.append(
            {
                **raw_agent,
                "transport": {
                    key: value for key, value in raw_transport.items() if key != "cwd"
                },
            }
        )
    return {**payload, "agents": agents}
