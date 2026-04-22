# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from relay_teams.agents.orchestration.settings_service import (
    OrchestrationSettingsService,
)
from relay_teams.gateway.feishu.account_repository import FeishuAccountRepository
from relay_teams.gateway.feishu.errors import FeishuAccountNameConflictError
from relay_teams.gateway.feishu.models import (
    FeishuEnvironment,
    FeishuGatewayAccountCreateInput,
    FeishuGatewayAccountRecord,
    FeishuGatewayAccountStatus,
    FeishuGatewayAccountUpdateInput,
    FeishuTriggerRuntimeConfig,
    FeishuTriggerSecretConfig,
    FeishuTriggerSecretStatus,
    FeishuTriggerSourceConfig,
    FeishuTriggerTargetConfig,
)
from relay_teams.gateway.feishu.secret_store import (
    FeishuTriggerSecretStore,
    get_feishu_trigger_secret_store,
)
from relay_teams.validation import require_force_delete
from relay_teams.logger import get_logger, log_event
from relay_teams.roles import RoleRegistry
from relay_teams.sessions.external_session_binding_repository import (
    ExternalSessionBindingRepository,
)
from relay_teams.sessions.session_models import SessionMode, SessionRecord
from relay_teams.workspace import WorkspaceService

LOGGER = get_logger(__name__)


def _secret_config_payload(
    payload: FeishuTriggerSecretConfig | Mapping[str, str | None] | None,
) -> dict[str, str | None] | None:
    if payload is None:
        return None
    if isinstance(payload, FeishuTriggerSecretConfig):
        return payload.model_dump(mode="json", exclude_unset=True)
    return dict(payload.items())


def _feishu_config_payload(
    payload: (
        Mapping[str, object] | FeishuTriggerSourceConfig | FeishuTriggerTargetConfig
    ),
) -> dict[str, object]:
    if isinstance(payload, (FeishuTriggerSourceConfig, FeishuTriggerTargetConfig)):
        return payload.model_dump(mode="json")
    return dict(payload.items())


class FeishuGatewayService:
    def __init__(
        self,
        *,
        config_dir: Path,
        repository: FeishuAccountRepository,
        secret_store: FeishuTriggerSecretStore | None = None,
        role_registry: RoleRegistry,
        orchestration_settings_service: OrchestrationSettingsService,
        workspace_service: WorkspaceService,
        external_session_binding_repo: ExternalSessionBindingRepository,
    ) -> None:
        self._config_dir = config_dir
        self._repository = repository
        self._secret_store = (
            get_feishu_trigger_secret_store() if secret_store is None else secret_store
        )
        self._role_registry = role_registry
        self._orchestration_settings_service = orchestration_settings_service
        self._workspace_service = workspace_service
        self._external_session_binding_repo = external_session_binding_repo

    def replace_role_registry(self, role_registry: RoleRegistry) -> None:
        self._role_registry = role_registry

    def list_accounts(self) -> tuple[FeishuGatewayAccountRecord, ...]:
        return tuple(
            self.attach_secret_status(account)
            for account in self._repository.list_accounts()
        )

    def get_account(self, account_id: str) -> FeishuGatewayAccountRecord:
        return self.attach_secret_status(self._repository.get_account(account_id))

    def create_account(
        self,
        payload: FeishuGatewayAccountCreateInput,
    ) -> FeishuGatewayAccountRecord:
        self.validate_create_request(payload)
        now = datetime.now(tz=UTC)
        record = FeishuGatewayAccountRecord(
            account_id=f"fsg_{uuid4().hex[:12]}",
            name=payload.name,
            display_name=payload.display_name or payload.name,
            status=(
                FeishuGatewayAccountStatus.ENABLED
                if payload.enabled
                else FeishuGatewayAccountStatus.DISABLED
            ),
            source_config=payload.source_config.model_dump(mode="json"),
            target_config=payload.target_config.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
        )
        created = self._repository.create_account(record)
        self.save_secret_config(
            account_id=created.account_id,
            secret_config_payload=_secret_config_payload(payload.secret_config),
            require_app_secret=True,
        )
        return self.get_account(created.account_id)

    def update_account(
        self,
        account_id: str,
        payload: FeishuGatewayAccountUpdateInput,
    ) -> FeishuGatewayAccountRecord:
        existing = self._repository.get_account(account_id)
        self.validate_update_request(existing=existing, request=payload)
        updated = existing.model_copy(
            update={
                "name": payload.name if payload.name is not None else existing.name,
                "display_name": (
                    payload.display_name
                    if payload.display_name is not None
                    else existing.display_name
                ),
                "source_config": (
                    payload.source_config.model_dump(mode="json")
                    if payload.source_config is not None
                    else existing.source_config
                ),
                "target_config": (
                    payload.target_config.model_dump(mode="json")
                    if payload.target_config is not None
                    else existing.target_config
                ),
                "updated_at": datetime.now(tz=UTC),
            }
        )
        stored = self._repository.update_account(updated)
        self.save_secret_config(
            account_id=stored.account_id,
            secret_config_payload=_secret_config_payload(payload.secret_config),
            require_app_secret=False,
        )
        if self.runtime_settings_changed(existing, stored):
            self.clear_bindings(stored.account_id)
        return self.get_account(stored.account_id)

    def set_account_enabled(
        self,
        account_id: str,
        enabled: bool,
    ) -> FeishuGatewayAccountRecord:
        existing = self._repository.get_account(account_id)
        if enabled:
            self._validate_runtime_account(existing, require_app_secret=True)
        updated = existing.model_copy(
            update={
                "status": (
                    FeishuGatewayAccountStatus.ENABLED
                    if enabled
                    else FeishuGatewayAccountStatus.DISABLED
                ),
                "updated_at": datetime.now(tz=UTC),
            }
        )
        _ = self._repository.update_account(updated)
        return self.get_account(account_id)

    def delete_account(self, account_id: str, *, force: bool = False) -> None:
        _ = self._repository.get_account(account_id)
        if any(
            binding.trigger_id == account_id
            for binding in self._external_session_binding_repo.list_by_platform(
                "feishu"
            )
        ):
            require_force_delete(
                force,
                message="Cannot delete Feishu account while external session bindings exist",
            )
        self.clear_bindings(account_id)
        self.delete_secret_config(account_id)
        self._repository.delete_account(account_id)

    def attach_secret_status(
        self,
        account: FeishuGatewayAccountRecord,
    ) -> FeishuGatewayAccountRecord:
        secret_config = self._secret_store.get_secret_config(
            self._config_dir,
            account.account_id,
        )
        last_error = self._account_runtime_error(account, secret_config=secret_config)
        return account.model_copy(
            update={
                "secret_config": secret_config.model_dump(
                    mode="json",
                    exclude_none=True,
                )
                or None,
                "secret_status": self.get_secret_status(account.account_id).model_dump(
                    mode="json"
                ),
                "last_error": last_error,
            }
        )

    def get_secret_status(self, account_id: str) -> FeishuTriggerSecretStatus:
        secret_config = self._secret_store.get_secret_config(
            self._config_dir,
            account_id,
        )
        return FeishuTriggerSecretStatus(
            app_secret_configured=secret_config.app_secret is not None,
            verification_token_configured=secret_config.verification_token is not None,
            encrypt_key_configured=secret_config.encrypt_key is not None,
        )

    def validate_create_request(
        self,
        request: FeishuGatewayAccountCreateInput,
    ) -> None:
        self._validate_source_and_target(
            source_config=request.source_config,
            target_config=request.target_config,
        )
        _ = self._merge_secret_config(
            account_id=None,
            secret_config_payload=_secret_config_payload(request.secret_config),
            require_app_secret=True,
        )

    def validate_update_request(
        self,
        *,
        existing: FeishuGatewayAccountRecord,
        request: FeishuGatewayAccountUpdateInput,
    ) -> None:
        merged_source = (
            request.source_config.model_dump(mode="json")
            if request.source_config is not None
            else existing.source_config
        )
        merged_target = (
            request.target_config.model_dump(mode="json")
            if request.target_config is not None
            else existing.target_config
        )
        self._validate_source_and_target(
            source_config=merged_source,
            target_config=merged_target,
        )
        if request.secret_config is not None:
            _ = self._merge_secret_config(
                account_id=existing.account_id,
                secret_config_payload=_secret_config_payload(request.secret_config),
                require_app_secret=False,
            )

    def save_secret_config(
        self,
        *,
        account_id: str,
        secret_config_payload: Mapping[str, str | None] | None,
        require_app_secret: bool,
    ) -> None:
        if secret_config_payload is None:
            return
        merged = self._merge_secret_config(
            account_id=account_id,
            secret_config_payload=secret_config_payload,
            require_app_secret=require_app_secret,
        )
        self._secret_store.set_secret_config(
            self._config_dir,
            account_id,
            merged,
        )

    def delete_secret_config(self, account_id: str) -> None:
        self._secret_store.delete_secret_config(self._config_dir, account_id)

    def resolve_runtime_config(
        self,
        account: FeishuGatewayAccountRecord,
    ) -> FeishuTriggerRuntimeConfig | None:
        secret_config = self._secret_store.get_secret_config(
            self._config_dir,
            account.account_id,
        )
        validation_error = self._account_runtime_error(
            account,
            secret_config=secret_config,
        )
        if validation_error is not None:
            return None
        source = FeishuTriggerSourceConfig.model_validate(account.source_config)
        target = FeishuTriggerTargetConfig.model_validate(account.target_config or {})
        app_secret = secret_config.app_secret
        if app_secret is None:
            return None
        return FeishuTriggerRuntimeConfig(
            trigger_id=account.account_id,
            trigger_name=account.display_name,
            source=source,
            target=target,
            environment=FeishuEnvironment(
                app_id=source.app_id,
                app_secret=app_secret,
                app_name=source.app_name,
                verification_token=secret_config.verification_token,
                encrypt_key=secret_config.encrypt_key,
            ),
        )

    def get_runtime_config_by_account_id(
        self,
        account_id: str,
    ) -> FeishuTriggerRuntimeConfig | None:
        try:
            account = self._repository.get_account(account_id)
        except KeyError:
            return None
        return self.resolve_runtime_config(account)

    def get_runtime_config_by_trigger_id(
        self,
        trigger_id: str,
    ) -> FeishuTriggerRuntimeConfig | None:
        return self.get_runtime_config_by_account_id(trigger_id)

    def list_enabled_runtime_configs(self) -> tuple[FeishuTriggerRuntimeConfig, ...]:
        resolved: list[FeishuTriggerRuntimeConfig] = []
        for account in self._repository.list_accounts():
            if account.status != FeishuGatewayAccountStatus.ENABLED:
                continue
            attached = self.attach_secret_status(account)
            if attached.last_error is not None:
                log_event(
                    LOGGER,
                    30,
                    event="gateway.feishu.runtime_config_skipped",
                    message="Skipped invalid persisted Feishu gateway account",
                    payload={
                        "account_id": attached.account_id,
                        "account_name": attached.name,
                        "last_error": attached.last_error,
                    },
                )
                continue
            runtime = self.resolve_runtime_config(account)
            if runtime is None:
                continue
            resolved.append(runtime)
        return tuple(resolved)

    def subscription_runtime_changed_for_update(
        self,
        *,
        existing: FeishuGatewayAccountRecord,
        request: FeishuGatewayAccountUpdateInput,
    ) -> bool:
        before_signature = self._subscription_runtime_signature(
            source_config=existing.source_config,
            secret_config=self._secret_store.get_secret_config(
                self._config_dir,
                existing.account_id,
            ),
        )
        after_source_config = (
            existing.source_config
            if request.source_config is None
            else request.source_config.model_dump(mode="json")
        )
        after_secret_config = self._merge_secret_config(
            account_id=existing.account_id,
            secret_config_payload=_secret_config_payload(request.secret_config),
            require_app_secret=False,
        )
        after_signature = self._subscription_runtime_signature(
            source_config=after_source_config,
            secret_config=after_secret_config,
        )
        return before_signature != after_signature

    def runtime_settings_changed(
        self,
        before: FeishuGatewayAccountRecord,
        after: FeishuGatewayAccountRecord,
    ) -> bool:
        return self._normalized_target(before) != self._normalized_target(after)

    def clear_bindings(self, account_id: str) -> None:
        self._external_session_binding_repo.delete_by_trigger(account_id)

    def _validate_source_and_target(
        self,
        *,
        source_config: Mapping[str, object] | FeishuTriggerSourceConfig,
        target_config: Mapping[str, object] | FeishuTriggerTargetConfig | None,
    ) -> None:
        source = FeishuTriggerSourceConfig.model_validate(
            _feishu_config_payload(source_config)
        )
        target = FeishuTriggerTargetConfig.model_validate(
            {} if target_config is None else _feishu_config_payload(target_config)
        )
        self._require_workspace(target.workspace_id)
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
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )
            _ = self._orchestration_settings_service.resolve_run_topology(probe)
        _ = source

    def _validate_runtime_account(
        self,
        account: FeishuGatewayAccountRecord,
        *,
        require_app_secret: bool,
    ) -> None:
        secret_config = self._secret_store.get_secret_config(
            self._config_dir,
            account.account_id,
        )
        error = self._account_runtime_error(
            account,
            secret_config=secret_config,
            require_app_secret=require_app_secret,
        )
        if error is not None:
            raise ValueError(error)

    def _account_runtime_error(
        self,
        account: FeishuGatewayAccountRecord,
        *,
        secret_config: FeishuTriggerSecretConfig,
        require_app_secret: bool = True,
    ) -> str | None:
        try:
            self._validate_source_and_target(
                source_config=account.source_config,
                target_config=account.target_config,
            )
        except (KeyError, ValueError) as exc:
            return str(exc)
        if require_app_secret and secret_config.app_secret is None:
            return "Feishu app_secret is required"
        return None

    def _require_workspace(self, workspace_id: str) -> None:
        try:
            _ = self._workspace_service.require_workspace(workspace_id)
        except KeyError as exc:
            raise ValueError(f"Unknown workspace: {workspace_id}") from exc

    def _normalized_target(
        self,
        account: FeishuGatewayAccountRecord,
    ) -> FeishuTriggerTargetConfig:
        target = FeishuTriggerTargetConfig.model_validate(account.target_config or {})
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
        account_id: str | None,
        secret_config_payload: Mapping[str, str | None] | None,
        require_app_secret: bool,
    ) -> FeishuTriggerSecretConfig:
        payload = (
            {} if secret_config_payload is None else dict(secret_config_payload.items())
        )
        current = (
            FeishuTriggerSecretConfig()
            if account_id is None
            else self._secret_store.get_secret_config(self._config_dir, account_id)
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

    def _subscription_runtime_signature(
        self,
        *,
        source_config: Mapping[str, object],
        secret_config: FeishuTriggerSecretConfig,
    ) -> tuple[str, str | None, str | None, str | None]:
        source = FeishuTriggerSourceConfig.model_validate(dict(source_config.items()))
        return (
            source.app_id,
            secret_config.app_secret,
            secret_config.verification_token,
            secret_config.encrypt_key,
        )


def _normalize_secret_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


__all__ = [
    "FeishuAccountNameConflictError",
    "FeishuGatewayService",
]
