from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.agents.orchestration.settings_service import (
    OrchestrationSettingsService,
)
from agent_teams.gateway.feishu.account_repository import FeishuAccountRepository
from agent_teams.gateway.feishu.gateway_service import FeishuGatewayService
from agent_teams.gateway.feishu.models import (
    FeishuGatewayAccountCreateInput,
    FeishuGatewayAccountStatus,
    FeishuTriggerSecretConfig,
)
from agent_teams.gateway.feishu.secret_store import FeishuTriggerSecretStore
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.sessions.external_session_binding_repository import (
    ExternalSessionBindingRepository,
)
from agent_teams.sessions.runs.run_models import RunTopologySnapshot
from agent_teams.sessions.session_models import SessionMode
from agent_teams.workspace import WorkspaceRepository, WorkspaceService


class _FakeSecretStore(FeishuTriggerSecretStore):
    def __init__(self) -> None:
        self._values: dict[str, FeishuTriggerSecretConfig] = {}

    def get_secret_config(
        self,
        config_dir: Path,
        trigger_id: str,
    ) -> FeishuTriggerSecretConfig:
        _ = config_dir
        return self._values.get(trigger_id, FeishuTriggerSecretConfig())

    def set_secret_config(
        self,
        config_dir: Path,
        trigger_id: str,
        secret_config: FeishuTriggerSecretConfig,
    ) -> None:
        _ = config_dir
        self._values[trigger_id] = secret_config

    def delete_secret_config(self, config_dir: Path, trigger_id: str) -> None:
        _ = config_dir
        self._values.pop(trigger_id, None)


class _FakeOrchestrationSettingsService(OrchestrationSettingsService):
    def __init__(self) -> None:
        pass

    def resolve_run_topology(self, session) -> RunTopologySnapshot:
        preset_id = str(getattr(session, "orchestration_preset_id", "") or "").strip()
        if preset_id != "preset-1":
            raise ValueError(f"Unknown orchestration preset: {preset_id or 'none'}")
        return RunTopologySnapshot(
            session_mode=SessionMode.ORCHESTRATION,
            main_agent_role_id="MainAgent",
            normal_root_role_id="MainAgent",
            coordinator_role_id="Coordinator",
            orchestration_preset_id="preset-1",
        )


def _build_role_registry() -> RoleRegistry:
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="Default role.",
            version="1",
            tools=(),
            system_prompt="You are Main Agent.",
        )
    )
    role_registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates tasks.",
            version="1",
            tools=("create_tasks", "update_task", "dispatch_task"),
            system_prompt="Coordinate work.",
        )
    )
    return role_registry


def _build_service(tmp_path: Path) -> FeishuGatewayService:
    db_path = tmp_path / "feishu_gateway.db"
    workspace_service = WorkspaceService(repository=WorkspaceRepository(db_path))
    _ = workspace_service.create_workspace(
        workspace_id="default",
        root_path=tmp_path,
    )
    return FeishuGatewayService(
        config_dir=tmp_path / "config",
        repository=FeishuAccountRepository(db_path),
        secret_store=_FakeSecretStore(),
        role_registry=_build_role_registry(),
        orchestration_settings_service=_FakeOrchestrationSettingsService(),
        workspace_service=workspace_service,
        external_session_binding_repo=ExternalSessionBindingRepository(db_path),
    )


def test_set_account_enabled_rejects_invalid_persisted_workspace(
    tmp_path: Path,
) -> None:
    service = _build_service(tmp_path)
    created = service.create_account(
        FeishuGatewayAccountCreateInput(
            name="feishu-main",
            enabled=False,
            source_config={
                "provider": "feishu",
                "trigger_rule": "mention_only",
                "app_id": "cli_123",
                "app_name": "Feishu Bot",
            },
            target_config={"workspace_id": "default"},
            secret_config={"app_secret": "secret-1"},
        )
    )
    _ = service._repository.update_account(
        created.model_copy(
            update={
                "target_config": {"workspace_id": "missing-workspace"},
                "status": FeishuGatewayAccountStatus.DISABLED,
            }
        )
    )

    with pytest.raises(ValueError, match="Unknown workspace: missing-workspace"):
        service.set_account_enabled(created.account_id, True)


def test_get_account_exposes_last_error_for_invalid_persisted_preset(
    tmp_path: Path,
) -> None:
    service = _build_service(tmp_path)
    created = service.create_account(
        FeishuGatewayAccountCreateInput(
            name="feishu-main",
            enabled=False,
            source_config={
                "provider": "feishu",
                "trigger_rule": "mention_only",
                "app_id": "cli_123",
                "app_name": "Feishu Bot",
            },
            target_config={"workspace_id": "default"},
            secret_config={"app_secret": "secret-1"},
        )
    )
    _ = service._repository.update_account(
        created.model_copy(
            update={
                "target_config": {
                    "workspace_id": "default",
                    "session_mode": "orchestration",
                    "orchestration_preset_id": "preset-missing",
                },
                "status": FeishuGatewayAccountStatus.ENABLED,
            }
        )
    )

    account = service.get_account(created.account_id)

    assert account.last_error == "Unknown orchestration preset: preset-missing"


def test_list_enabled_runtime_configs_skips_invalid_persisted_accounts(
    tmp_path: Path,
) -> None:
    service = _build_service(tmp_path)
    valid = service.create_account(
        FeishuGatewayAccountCreateInput(
            name="feishu-valid",
            enabled=True,
            source_config={
                "provider": "feishu",
                "trigger_rule": "mention_only",
                "app_id": "cli_valid",
                "app_name": "Valid Bot",
            },
            target_config={"workspace_id": "default"},
            secret_config={"app_secret": "secret-valid"},
        )
    )
    invalid = service.create_account(
        FeishuGatewayAccountCreateInput(
            name="feishu-invalid",
            enabled=False,
            source_config={
                "provider": "feishu",
                "trigger_rule": "mention_only",
                "app_id": "cli_invalid",
                "app_name": "Invalid Bot",
            },
            target_config={"workspace_id": "default"},
            secret_config={"app_secret": "secret-invalid"},
        )
    )
    _ = service._repository.update_account(
        invalid.model_copy(
            update={
                "status": FeishuGatewayAccountStatus.ENABLED,
                "target_config": {"workspace_id": "missing-workspace"},
            }
        )
    )

    runtime_configs = service.list_enabled_runtime_configs()

    assert len(runtime_configs) == 1
    assert runtime_configs[0].trigger_id == valid.account_id
