# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from agent_teams.feishu.models import (
    FEISHU_PLATFORM,
    FeishuEnvironment,
    FeishuTriggerRuntimeConfig,
    FeishuTriggerSecretConfig,
    FeishuTriggerSecretStatus,
    FeishuTriggerSourceConfig,
    FeishuTriggerTargetConfig,
)
from agent_teams.feishu.secret_store import (
    FeishuTriggerSecretStore,
    get_feishu_trigger_secret_store,
)
from agent_teams.logger import get_logger, log_event
from agent_teams.roles import RoleRegistry
from agent_teams.sessions.external_session_binding_repository import (
    ExternalSessionBindingRepository,
)
from agent_teams.sessions.session_models import SessionRecord
from agent_teams.sessions.session_models import SessionMode
from agent_teams.triggers import (
    TriggerCreateInput,
    TriggerDefinition,
    TriggerSourceType,
    TriggerUpdateInput,
)
from agent_teams.workspace import WorkspaceService
from agent_teams.agents.orchestration.settings_service import (
    OrchestrationSettingsService,
)

logger = get_logger(__name__)


class FeishuTriggerConfigService:
    def __init__(
        self,
        *,
        config_dir: Path,
        get_trigger: Callable[[str], TriggerDefinition],
        secret_store: FeishuTriggerSecretStore | None = None,
        role_registry: RoleRegistry,
        orchestration_settings_service: OrchestrationSettingsService,
        workspace_service: WorkspaceService,
        external_session_binding_repo: ExternalSessionBindingRepository,
    ) -> None:
        self._config_dir = config_dir
        self._get_trigger = get_trigger
        self._secret_store = (
            get_feishu_trigger_secret_store() if secret_store is None else secret_store
        )
        self._role_registry = role_registry
        self._orchestration_settings_service = orchestration_settings_service
        self._workspace_service = workspace_service
        self._external_session_binding_repo = external_session_binding_repo

    def is_feishu_trigger(self, trigger: TriggerDefinition) -> bool:
        provider = str(trigger.source_config.get("provider", "")).strip().lower()
        return trigger.source_type == TriggerSourceType.IM and provider == FEISHU_PLATFORM

    def attach_secret_status(self, trigger: TriggerDefinition) -> TriggerDefinition:
        if not self.is_feishu_trigger(trigger):
            return trigger
        return trigger.model_copy(
            update={
                "secret_status": self.get_secret_status(trigger.trigger_id).model_dump(
                    mode="json"
                )
            }
        )

    def attach_secret_statuses(
        self,
        triggers: Iterable[TriggerDefinition],
    ) -> tuple[TriggerDefinition, ...]:
        return tuple(self.attach_secret_status(trigger) for trigger in triggers)

    def get_secret_status(self, trigger_id: str) -> FeishuTriggerSecretStatus:
        secret_config = self._secret_store.get_secret_config(self._config_dir, trigger_id)
        return FeishuTriggerSecretStatus(
            app_secret_configured=secret_config.app_secret is not None,
            verification_token_configured=secret_config.verification_token is not None,
            encrypt_key_configured=secret_config.encrypt_key is not None,
        )

    def validate_create_request(self, request: TriggerCreateInput) -> None:
        if not self._is_feishu_request(
            source_type=request.source_type,
            source_config=request.source_config,
        ):
            return
        self._validate_source_and_target(
            source_config=request.source_config,
            target_config=request.target_config,
        )
        _ = self._merge_secret_config(
            trigger_id=None,
            secret_config_payload=request.secret_config,
            require_app_secret=True,
        )

    def validate_update_request(
        self,
        *,
        existing: TriggerDefinition,
        request: TriggerUpdateInput,
    ) -> None:
        if not self.is_feishu_trigger(existing) and not self._is_feishu_request(
            source_type=existing.source_type,
            source_config=request.source_config or {},
        ):
            return
        merged_source = (
            request.source_config
            if request.source_config is not None
            else existing.source_config
        )
        merged_target = (
            request.target_config
            if request.target_config is not None
            else existing.target_config
        )
        self._validate_source_and_target(
            source_config=merged_source,
            target_config=merged_target,
        )
        if request.secret_config is not None:
            _ = self._merge_secret_config(
                trigger_id=existing.trigger_id,
                secret_config_payload=request.secret_config,
                require_app_secret=False,
            )

    def save_secret_config(
        self,
        *,
        trigger_id: str,
        secret_config_payload: Mapping[str, str] | None,
        require_app_secret: bool,
    ) -> None:
        if secret_config_payload is None:
            return
        merged = self._merge_secret_config(
            trigger_id=trigger_id,
            secret_config_payload=secret_config_payload,
            require_app_secret=require_app_secret,
        )
        self._secret_store.set_secret_config(
            self._config_dir,
            trigger_id,
            merged,
        )

    def resolve_runtime_config(
        self,
        trigger: TriggerDefinition,
    ) -> FeishuTriggerRuntimeConfig | None:
        if not self.is_feishu_trigger(trigger):
            return None
        source = FeishuTriggerSourceConfig.model_validate(trigger.source_config)
        target = FeishuTriggerTargetConfig.model_validate(trigger.target_config or {})
        secret_config = self._secret_store.get_secret_config(
            self._config_dir,
            trigger.trigger_id,
        )
        if secret_config.app_secret is None:
            return None
        return FeishuTriggerRuntimeConfig(
            trigger_id=trigger.trigger_id,
            trigger_name=trigger.name,
            source=source,
            target=target,
            environment=FeishuEnvironment(
                app_id=source.app_id,
                app_secret=secret_config.app_secret,
                app_name=source.app_name,
                verification_token=secret_config.verification_token,
                encrypt_key=secret_config.encrypt_key,
            ),
        )

    def get_runtime_config_by_trigger_id(
        self,
        trigger_id: str,
    ) -> FeishuTriggerRuntimeConfig | None:
        try:
            trigger = self._get_trigger(trigger_id)
        except KeyError:
            return None
        return self.resolve_runtime_config(trigger)

    def list_enabled_runtime_configs(
        self,
        triggers: Iterable[TriggerDefinition],
    ) -> tuple[FeishuTriggerRuntimeConfig, ...]:
        resolved: list[FeishuTriggerRuntimeConfig] = []
        for trigger in triggers:
            if not self.is_feishu_trigger(trigger):
                continue
            if str(trigger.status.value) != "enabled":
                continue
            try:
                runtime = self.resolve_runtime_config(trigger)
            except ValueError as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    event="feishu.trigger.invalid_config",
                    message="Skipping Feishu trigger with invalid configuration",
                    payload={"trigger_id": trigger.trigger_id, "error": str(exc)},
                )
                continue
            if runtime is None:
                continue
            resolved.append(runtime)
        return tuple(resolved)

    def runtime_settings_changed(
        self,
        before: TriggerDefinition,
        after: TriggerDefinition,
    ) -> bool:
        if not self.is_feishu_trigger(before) or not self.is_feishu_trigger(after):
            return False
        return self._normalized_target(before) != self._normalized_target(after)

    def clear_bindings(self, trigger_id: str) -> None:
        self._external_session_binding_repo.delete_by_trigger(trigger_id)

    def _validate_source_and_target(
        self,
        *,
        source_config: Mapping[str, object],
        target_config: Mapping[str, object] | None,
    ) -> None:
        source = FeishuTriggerSourceConfig.model_validate(dict(source_config.items()))
        target = FeishuTriggerTargetConfig.model_validate(
            {} if target_config is None else dict(target_config.items())
        )
        self._workspace_service.require_workspace(target.workspace_id)
        normalized_root_role_id = self._resolve_normal_root_role_id(
            target.normal_root_role_id
        )
        if target.session_mode == SessionMode.ORCHESTRATION:
            probe = SessionRecord(
                session_id="validation",
                workspace_id=target.workspace_id,
                metadata={},
                session_mode=SessionMode.ORCHESTRATION,
                normal_root_role_id=normalized_root_role_id,
                orchestration_preset_id=target.orchestration_preset_id,
                created_at=datetime.now(tz=timezone.utc),
                updated_at=datetime.now(tz=timezone.utc),
            )
            _ = self._orchestration_settings_service.resolve_run_topology(probe)
        _ = source

    def _normalized_target(
        self,
        trigger: TriggerDefinition,
    ) -> FeishuTriggerTargetConfig:
        target = FeishuTriggerTargetConfig.model_validate(trigger.target_config or {})
        return target.model_copy(
            update={
                "normal_root_role_id": self._resolve_normal_root_role_id(
                    target.normal_root_role_id
                )
            }
        )

    def _resolve_normal_root_role_id(self, role_id: str | None) -> str:
        return self._role_registry.resolve_normal_mode_role_id(role_id)

    def _merge_secret_config(
        self,
        *,
        trigger_id: str | None,
        secret_config_payload: Mapping[str, str] | None,
        require_app_secret: bool,
    ) -> FeishuTriggerSecretConfig:
        payload = (
            {} if secret_config_payload is None else dict(secret_config_payload.items())
        )
        current = (
            FeishuTriggerSecretConfig()
            if trigger_id is None
            else self._secret_store.get_secret_config(self._config_dir, trigger_id)
        )
        next_secret = FeishuTriggerSecretConfig(
            app_secret=(
                _normalize_secret_value(payload.get("app_secret"))
                if "app_secret" in payload
                else current.app_secret
            ),
            verification_token=(
                _normalize_secret_value(payload.get("verification_token"))
                if "verification_token" in payload
                else current.verification_token
            ),
            encrypt_key=(
                _normalize_secret_value(payload.get("encrypt_key"))
                if "encrypt_key" in payload
                else current.encrypt_key
            ),
        )
        if require_app_secret and next_secret.app_secret is None:
            raise ValueError("Feishu app_secret is required")
        return next_secret

    def _is_feishu_request(
        self,
        *,
        source_type: TriggerSourceType,
        source_config: Mapping[str, object],
    ) -> bool:
        provider = str(source_config.get("provider", "")).strip().lower()
        return source_type == TriggerSourceType.IM and provider == FEISHU_PLATFORM


def _normalize_secret_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
