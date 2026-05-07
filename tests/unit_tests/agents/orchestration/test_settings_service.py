# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.agents.orchestration.policy_models import OrchestrationPolicy
from relay_teams.agents.orchestration.settings_config_manager import (
    OrchestrationSettingsConfigManager,
)
from relay_teams.agents.orchestration.settings_service import (
    OrchestrationSettingsService,
)
from relay_teams.plugins.plugin_models import (
    PluginManifest,
    PluginRecord,
    PluginRegistry,
    PluginScope,
    PluginSettings,
    PluginSettingsSource,
)
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.sessions.session_models import SessionMode
from relay_teams.sessions.session_repository import SessionRepository


def test_resolve_run_topology_uses_preset_policy(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "orchestration.json").write_text(
        (
            "{\n"
            '  "default_orchestration_preset_id": "shipping",\n'
            '  "presets": [\n'
            "    {\n"
            '      "preset_id": "shipping",\n'
            '      "name": "Shipping",\n'
            '      "role_ids": ["writer"],\n'
            '      "orchestration_prompt": "Delegate by capability.",\n'
            '      "policy": {\n'
            '        "max_orchestration_cycles": 12,\n'
            '        "max_parallel_delegated_tasks": 6\n'
            "      }\n"
            "    }\n"
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    session_repo = SessionRepository(tmp_path / "settings_service.db")
    session = session_repo.create(
        session_id="session-1",
        workspace_id="default",
        session_mode=SessionMode.ORCHESTRATION,
        orchestration_preset_id="shipping",
    )
    service = OrchestrationSettingsService(
        config_manager=OrchestrationSettingsConfigManager(config_dir=config_dir),
        session_repo=session_repo,
        get_role_registry=_build_role_registry,
    )

    topology = service.resolve_run_topology(session)

    assert topology.orchestration_preset_id == "shipping"
    assert topology.orchestration_policy.max_orchestration_cycles == 12
    assert topology.orchestration_policy.max_parallel_delegated_tasks == 6


def test_resolve_run_topology_accepts_run_policy_override(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "orchestration.json").write_text(
        (
            "{\n"
            '  "default_orchestration_preset_id": "shipping",\n'
            '  "presets": [\n'
            "    {\n"
            '      "preset_id": "shipping",\n'
            '      "name": "Shipping",\n'
            '      "role_ids": ["writer"],\n'
            '      "orchestration_prompt": "Delegate by capability.",\n'
            '      "policy": {\n'
            '        "max_orchestration_cycles": 12,\n'
            '        "max_parallel_delegated_tasks": 6\n'
            "      }\n"
            "    }\n"
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    session_repo = SessionRepository(tmp_path / "settings_service_override.db")
    session = session_repo.create(
        session_id="session-1",
        workspace_id="default",
        session_mode=SessionMode.ORCHESTRATION,
        orchestration_preset_id="shipping",
    )
    service = OrchestrationSettingsService(
        config_manager=OrchestrationSettingsConfigManager(config_dir=config_dir),
        session_repo=session_repo,
        get_role_registry=_build_role_registry,
    )

    topology = service.resolve_run_topology(
        session,
        policy_override=OrchestrationPolicy(
            max_orchestration_cycles=1,
            max_parallel_delegated_tasks=0,
        ),
    )

    assert topology.orchestration_policy.max_orchestration_cycles == 1
    assert topology.orchestration_policy.max_parallel_delegated_tasks == 0


def test_default_normal_root_role_uses_plugin_agent_setting(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    session_repo = SessionRepository(tmp_path / "plugin_settings_agent.db")
    service = OrchestrationSettingsService(
        config_manager=OrchestrationSettingsConfigManager(config_dir=config_dir),
        session_repo=session_repo,
        get_role_registry=_build_role_registry,
        get_plugin_registry=lambda: _plugin_registry_with_agent(tmp_path, "writer"),
    )

    assert service.default_normal_root_role_id() == "writer"


def test_default_normal_root_role_ignores_invalid_plugin_agent(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    session_repo = SessionRepository(tmp_path / "plugin_settings_invalid_agent.db")
    service = OrchestrationSettingsService(
        config_manager=OrchestrationSettingsConfigManager(config_dir=config_dir),
        session_repo=session_repo,
        get_role_registry=_build_role_registry,
        get_plugin_registry=lambda: _plugin_registry_with_agent(tmp_path, "missing"),
    )

    assert service.default_normal_root_role_id() is None


def _build_role_registry() -> RoleRegistry:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates work.",
            version="1",
            system_prompt="Coordinate.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="Handles normal runs.",
            version="1",
            system_prompt="Handle requests.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="writer",
            name="Writer",
            description="Writes updates.",
            version="1",
            system_prompt="Write.",
        )
    )
    return registry


def _plugin_registry_with_agent(tmp_path: Path, agent: str) -> PluginRegistry:
    root_dir = tmp_path / "quality"
    data_dir = tmp_path / "data" / "quality"
    settings_path = root_dir / "settings.json"
    return PluginRegistry(
        plugins=(
            PluginRecord(
                name="quality",
                version="1",
                scope=PluginScope.USER,
                root_dir=root_dir,
                data_dir=data_dir,
                manifest=PluginManifest(name="quality", version="1"),
                settings_sources=(
                    PluginSettingsSource(
                        plugin_name="quality",
                        scope=PluginScope.USER,
                        root_dir=root_dir,
                        data_dir=data_dir,
                        path=settings_path,
                        settings=PluginSettings(agent=agent),
                    ),
                ),
            ),
        ),
    )
