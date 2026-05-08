# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from relay_teams.agent_runtimes import (
    ExternalAgentConfig,
    ExternalAgentConfigService,
    ExternalAgentProtocol,
    ExternalAgentSecretBinding,
    ExternalAgentSecretStore,
    StdioTransportConfig,
    StreamableHttpTransportConfig,
)


class _FakeSecretStore(ExternalAgentSecretStore):
    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str, str], str] = {}

    def can_persist_secrets(self) -> bool:
        return True

    def get_secret(
        self,
        *,
        config_dir: Path,
        agent_id: str,
        kind: str,
        name: str,
    ) -> str | None:
        return self.values.get((str(config_dir), agent_id, kind, name))

    def set_secret(
        self,
        *,
        config_dir: Path,
        agent_id: str,
        kind: str,
        name: str,
        value: str,
    ) -> None:
        self.values[(str(config_dir), agent_id, kind, name)] = value

    def delete_secret(
        self,
        *,
        config_dir: Path,
        agent_id: str,
        kind: str,
        name: str,
    ) -> None:
        self.values.pop((str(config_dir), agent_id, kind, name), None)

    def delete_agent(self, *, config_dir: Path, agent_id: str) -> None:
        prefix = (str(config_dir), agent_id)
        next_values = {
            key: value for key, value in self.values.items() if key[:2] != prefix
        }
        self.values = next_values


def test_save_agent_persists_secret_bindings_without_writing_values(
    tmp_path: Path,
) -> None:
    secret_store = _FakeSecretStore()
    service = ExternalAgentConfigService(
        config_dir=tmp_path,
        secret_store=secret_store,
    )

    saved = service.save_agent(
        "codex_local",
        ExternalAgentConfig(
            agent_id="codex_local",
            name="Codex Local",
            description="Runs Codex via stdio",
            transport=StdioTransportConfig(
                command="codex",
                args=("--serve",),
                env=(
                    ExternalAgentSecretBinding(
                        name="CODEX_API_KEY",
                        value="secret-123",
                        secret=True,
                    ),
                ),
            ),
        ),
    )

    assert isinstance(saved.transport, StdioTransportConfig)
    persisted_binding = saved.transport.env[0]
    assert persisted_binding.name == "CODEX_API_KEY"
    assert persisted_binding.value is None
    assert persisted_binding.secret is True
    assert persisted_binding.configured is True
    assert (
        secret_store.get_secret(
            config_dir=tmp_path,
            agent_id="codex_local",
            kind="env",
            name="CODEX_API_KEY",
        )
        == "secret-123"
    )


def test_resolve_runtime_agent_restores_secret_values(tmp_path: Path) -> None:
    secret_store = _FakeSecretStore()
    service = ExternalAgentConfigService(
        config_dir=tmp_path,
        secret_store=secret_store,
    )
    _ = service.save_agent(
        "codex_local",
        ExternalAgentConfig(
            agent_id="codex_local",
            name="Codex Local",
            transport=StdioTransportConfig(
                command="codex",
                env=(
                    ExternalAgentSecretBinding(
                        name="CODEX_API_KEY",
                        value="secret-123",
                        secret=True,
                    ),
                    ExternalAgentSecretBinding(
                        name="MODE",
                        value="cli",
                        secret=False,
                    ),
                ),
            ),
        ),
    )

    resolved = service.resolve_runtime_agent("codex_local")

    assert isinstance(resolved.transport, StdioTransportConfig)
    assert resolved.transport.env[0].value == "secret-123"
    assert resolved.transport.env[0].configured is True
    assert resolved.transport.env[1].value == "cli"


def test_save_agent_deletes_secret_binding_when_marked_unconfigured(
    tmp_path: Path,
) -> None:
    secret_store = _FakeSecretStore()
    secret_store.values[(str(tmp_path), "codex_local", "env", "CODEX_API_KEY")] = (
        "old-secret"
    )
    service = ExternalAgentConfigService(
        config_dir=tmp_path,
        secret_store=secret_store,
    )

    saved = service.save_agent(
        "codex_local",
        ExternalAgentConfig(
            agent_id="codex_local",
            name="Codex Local",
            transport=StdioTransportConfig(
                command="codex",
                env=(
                    ExternalAgentSecretBinding(
                        name="CODEX_API_KEY",
                        secret=True,
                        configured=False,
                    ),
                ),
            ),
        ),
    )

    assert isinstance(saved.transport, StdioTransportConfig)
    assert saved.transport.env[0].configured is False
    assert (
        secret_store.get_secret(
            config_dir=tmp_path,
            agent_id="codex_local",
            kind="env",
            name="CODEX_API_KEY",
        )
        is None
    )


def test_get_agent_strips_legacy_stdio_workdir_from_saved_config(
    tmp_path: Path,
) -> None:
    (tmp_path / "agents.json").write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "agent_id": "codex_local",
                        "name": "Codex Local",
                        "description": "Legacy config",
                        "transport": {
                            "transport": "stdio",
                            "command": "codex",
                            "args": ["--serve"],
                            "cwd": "/tmp/legacy",
                            "env": [],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    service = ExternalAgentConfigService(
        config_dir=tmp_path,
        secret_store=_FakeSecretStore(),
    )

    loaded = service.get_agent("codex_local")

    assert isinstance(loaded.transport, StdioTransportConfig)
    assert loaded.transport.command == "codex"
    assert loaded.model_dump(mode="json") == {
        "agent_id": "codex_local",
        "name": "Codex Local",
        "description": "Legacy config",
        "protocol": "acp",
        "transport": {
            "transport": "stdio",
            "command": "codex",
            "args": ["--serve"],
            "env": [],
        },
        "native_config_enabled": False,
        "native_config_provider": "",
        "skill_bridge_enabled": False,
        "skill_bridge_skills": [],
        "skill_bridge_mode": "inline",
    }


def test_a2a_agent_requires_http_transport(tmp_path: Path) -> None:
    service = ExternalAgentConfigService(
        config_dir=tmp_path,
        secret_store=_FakeSecretStore(),
    )

    try:
        service.save_agent(
            "a2a_local",
            ExternalAgentConfig(
                agent_id="a2a_local",
                name="A2A Local",
                protocol=ExternalAgentProtocol.A2A,
                transport=StdioTransportConfig(command="agent"),
            ),
        )
    except ValueError as exc:
        assert "A2A agent runtimes require streamable_http transport" in str(exc)
    else:
        raise AssertionError("Expected A2A stdio config to be rejected")


def test_list_agent_summaries_include_runtime_protocol(tmp_path: Path) -> None:
    service = ExternalAgentConfigService(
        config_dir=tmp_path,
        secret_store=_FakeSecretStore(),
    )
    _ = service.save_agent(
        "a2a_remote",
        ExternalAgentConfig(
            agent_id="a2a_remote",
            name="A2A Remote",
            protocol=ExternalAgentProtocol.A2A,
            transport=StreamableHttpTransportConfig(
                url="http://agent.test/.well-known/agent.json",
            ),
        ),
    )

    summaries = service.list_agents()

    assert summaries[0].protocol == ExternalAgentProtocol.A2A
