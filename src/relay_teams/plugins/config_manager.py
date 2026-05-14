# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from os import environ, pathsep
from pathlib import Path
import shutil

from pydantic import JsonValue

from relay_teams.logger import get_logger
from relay_teams.plugins.audit import plugin_command_audit_diagnostics
from relay_teams.plugins.capability_validation import validate_plugin_capabilities
from relay_teams.plugins.integrity import (
    compute_plugin_tree_sha256,
    verify_plugin_tree_sha256,
)
from relay_teams.plugins.installers import (
    install_plugin_source,
)
from relay_teams.plugins.manifest_loader import (
    load_plugin_monitor_definitions,
    load_plugin_record,
    reload_plugin_settings_source,
)
from relay_teams.plugins.marketplace_service import PluginMarketplaceService
from relay_teams.plugins.marketplace_models import (
    PluginMarketplaceProviderKind,
    PluginMarketplaceSource,
)
from relay_teams.plugins.marketplace_policy import (
    PluginMarketplaceInstallPolicy,
    load_plugin_marketplace_install_policy,
)
from relay_teams.plugins.plugin_models import (
    PluginComponentCounts,
    PluginComponentSource,
    PluginDependency,
    PluginDiagnostic,
    PluginDiagnosticSeverity,
    PluginInstallSource,
    PluginInstallSourceKind,
    PluginMonitorDefinition,
    PluginRecord,
    PluginRegistry,
    PluginScope,
    PluginSettingsSource,
    PluginStateFile,
    PluginStateRecord,
    PluginUserConfigField,
)
from relay_teams.plugins.state_paths import (
    get_installed_plugin_version_dir,
    get_plugin_managed_state_file,
    get_plugin_data_root,
    get_plugin_installed_root,
    get_plugin_project_local_state_file,
    get_plugin_project_state_file,
    get_plugin_state_file,
    get_plugin_user_state_file,
)
from relay_teams.plugins.user_config_secrets import (
    PluginUserConfigSecretStore,
    get_plugin_user_config_secret_store,
)

LOGGER = get_logger(__name__)
_PLUGIN_DIRS_ENV_VAR = "RELAY_TEAMS_PLUGIN_DIRS"
_MANAGED_SCOPE_ERROR = "Managed plugin state is read-only and not implemented"


class PluginConfigManager:
    def __init__(
        self,
        *,
        app_config_dir: Path,
        plugin_dirs: tuple[Path, ...] = (),
        project_root: Path | None = None,
        project_start_dir: Path | None = None,
        user_config_secret_store: PluginUserConfigSecretStore | None = None,
    ) -> None:
        self._app_config_dir = app_config_dir.expanduser().resolve()
        self._plugin_dirs = tuple(path.expanduser().resolve() for path in plugin_dirs)
        self._project_root = (
            None if project_root is None else project_root.expanduser().resolve()
        )
        self._project_start_dir = (
            None
            if project_start_dir is None
            else project_start_dir.expanduser().resolve()
        )
        self._user_config_secret_store = (
            get_plugin_user_config_secret_store()
            if user_config_secret_store is None
            else user_config_secret_store
        )

    @classmethod
    def from_environment(
        cls,
        *,
        app_config_dir: Path,
        project_root: Path | None = None,
        project_start_dir: Path | None = None,
    ) -> PluginConfigManager:
        return cls(
            app_config_dir=app_config_dir,
            plugin_dirs=_plugin_dirs_from_env(),
            project_root=project_root,
            project_start_dir=project_start_dir,
        )

    def load_registry(self) -> PluginRegistry:
        diagnostics: list[PluginDiagnostic] = []
        records: list[PluginRecord] = []
        seen_names: set[str] = set()
        data_root = get_plugin_data_root(app_config_dir=self._app_config_dir)
        state_records = self._load_runtime_state_records(diagnostics)
        local_state_records = self._local_plugin_state_records()
        available_records = state_records + local_state_records
        dependency_diagnostics = self._dependency_diagnostics(
            state_records=available_records,
            available_records=available_records,
        )
        for state_record in state_records:
            dependency_diagnostic = dependency_diagnostics.get(state_record.name)
            if dependency_diagnostic is not None:
                diagnostics.append(dependency_diagnostic)
                self._append_state_record(
                    state_record=state_record,
                    data_root=data_root,
                    records=records,
                    diagnostics=diagnostics,
                    seen_names=seen_names,
                    enabled_override=False,
                )
                continue
            self._append_state_record(
                state_record=state_record,
                data_root=data_root,
                records=records,
                diagnostics=diagnostics,
                seen_names=seen_names,
                enabled_override=None,
            )
        local_state_records_by_root = {
            record.root_dir.expanduser().resolve(): record
            for record in local_state_records
        }
        for plugin_dir in self._plugin_dirs:
            local_state_record = local_state_records_by_root.get(
                plugin_dir.expanduser().resolve()
            )
            dependency_diagnostic = (
                None
                if local_state_record is None
                else dependency_diagnostics.get(local_state_record.name)
            )
            if dependency_diagnostic is not None:
                diagnostics.append(dependency_diagnostic)
            self._append_plugin_dir(
                plugin_dir=plugin_dir,
                data_root=data_root,
                records=records,
                diagnostics=diagnostics,
                seen_names=seen_names,
                scope=PluginScope.LOCAL,
                enabled_override=False if dependency_diagnostic is not None else None,
            )
        return PluginRegistry(
            plugins=tuple(records),
            diagnostics=tuple(diagnostics),
        )

    def validate_plugin(
        self,
        *,
        plugin_root: Path,
        scope: PluginScope = PluginScope.LOCAL,
        require_manifest: bool = False,
        strict_explicit_paths: bool = False,
        runtime_plugin_records: tuple[PluginRecord, ...] | None = None,
    ) -> tuple[PluginRecord | None, tuple[PluginDiagnostic, ...]]:
        record, diagnostics = load_plugin_record(
            plugin_root=plugin_root.expanduser().resolve(),
            data_root=get_plugin_data_root(app_config_dir=self._app_config_dir),
            manifest_config_dir_name=self._app_config_dir.name,
            scope=scope,
            require_manifest=require_manifest,
            strict_explicit_paths=strict_explicit_paths,
        )
        if record is None or not strict_explicit_paths:
            return record, diagnostics
        try:
            validate_plugin_capabilities(
                record=record,
                app_config_dir=self._app_config_dir,
                project_start_dir=self._project_start_dir,
                runtime_plugin_records=(
                    self.load_registry().enabled_plugins()
                    if runtime_plugin_records is None
                    else runtime_plugin_records
                ),
            )
        except Exception as exc:
            return record, (
                *diagnostics,
                PluginDiagnostic(
                    plugin_name=record.name,
                    scope=scope,
                    severity=PluginDiagnosticSeverity.ERROR,
                    message=f"Invalid plugin capability reference: {exc}",
                ),
            )
        return record, diagnostics

    def install_plugin(
        self,
        *,
        source: Path,
        scope: PluginScope,
        enabled: bool = True,
    ) -> PluginStateRecord:
        return self.install_from_source(
            source=PluginInstallSource(
                kind=PluginInstallSourceKind.LOCAL,
                value=str(source.expanduser().resolve()),
            ),
            scope=scope,
            enabled=enabled,
        )

    def install_git_plugin(
        self,
        *,
        source: str,
        scope: PluginScope,
        ref: str = "",
        enabled: bool = True,
    ) -> PluginStateRecord:
        return self.install_from_source(
            source=PluginInstallSource(
                kind=PluginInstallSourceKind.GIT,
                value=source.strip(),
                ref=ref.strip(),
            ),
            scope=scope,
            enabled=enabled,
        )

    def install_marketplace_plugin(
        self,
        *,
        name: str,
        marketplace: Path,
        scope: PluginScope,
        version: str | None = None,
        enabled: bool = True,
        marketplace_provider: PluginMarketplaceProviderKind = (
            PluginMarketplaceProviderKind.LOCAL_JSON
        ),
        marketplace_source: str = "",
        marketplace_ref: str = "",
        install_policy: PluginMarketplaceInstallPolicy | None = None,
    ) -> PluginStateRecord:
        marketplace_reference = (
            str(marketplace.expanduser().resolve())
            if marketplace_provider == PluginMarketplaceProviderKind.LOCAL_JSON
            else str(marketplace)
        )
        marketplace_source_reference = self._marketplace_source_reference(
            provider=marketplace_provider,
            marketplace_source=marketplace_source,
        )
        source = self._marketplace_source(
            provider=marketplace_provider,
            marketplace=marketplace_reference,
            marketplace_source=marketplace_source_reference,
            marketplace_ref=marketplace_ref,
        )
        resolved_install_policy = install_policy
        if source.provider == PluginMarketplaceProviderKind.CLAWHUB:
            resolved_install_policy = (
                install_policy
                or load_plugin_marketplace_install_policy(self._app_config_dir)
            )
        entry = PluginMarketplaceService().load_provider_entry(
            source=source,
            name=name,
            app_config_dir=self._app_config_dir,
            install_policy=resolved_install_policy,
        )
        selected = entry.selected_version(version)
        if selected.unsupported_reason:
            raise ValueError(selected.unsupported_reason)
        if resolved_install_policy is not None:
            resolved_install_policy.require_allowed(
                provider=source.provider,
                version=selected,
                entry=entry,
            )
        persisted_source = PluginInstallSource(
            kind=PluginInstallSourceKind.MARKETPLACE,
            value=entry.name,
            marketplace=marketplace_reference,
            marketplace_provider=source.provider.value,
            marketplace_source=source.value,
            marketplace_ref=source.ref,
            requested_version=selected.version,
        )
        return self.install_from_source(
            source=persisted_source,
            scope=scope,
            enabled=enabled,
            resolved_install_source=selected.source,
            expected_sha256=selected.sha256,
            extra_dependencies=selected.dependencies,
        )

    def inspect_marketplace_plugin(
        self,
        *,
        name: str,
        marketplace: Path,
        scope: PluginScope,
        version: str | None = None,
        marketplace_provider: PluginMarketplaceProviderKind = (
            PluginMarketplaceProviderKind.LOCAL_JSON
        ),
        marketplace_source: str = "",
        marketplace_ref: str = "",
        install_policy: PluginMarketplaceInstallPolicy | None = None,
    ) -> PluginRegistry:
        marketplace_reference = (
            str(marketplace.expanduser().resolve())
            if marketplace_provider == PluginMarketplaceProviderKind.LOCAL_JSON
            else str(marketplace)
        )
        marketplace_source_reference = self._marketplace_source_reference(
            provider=marketplace_provider,
            marketplace_source=marketplace_source,
        )
        source = self._marketplace_source(
            provider=marketplace_provider,
            marketplace=marketplace_reference,
            marketplace_source=marketplace_source_reference,
            marketplace_ref=marketplace_ref,
        )
        resolved_install_policy = install_policy
        if source.provider == PluginMarketplaceProviderKind.CLAWHUB:
            resolved_install_policy = (
                install_policy
                or load_plugin_marketplace_install_policy(self._app_config_dir)
            )
        entry = PluginMarketplaceService().load_provider_entry(
            source=source,
            name=name,
            app_config_dir=self._app_config_dir,
            install_policy=resolved_install_policy,
        )
        selected = entry.selected_version(version)
        if selected.unsupported_reason:
            raise ValueError(selected.unsupported_reason)
        if resolved_install_policy is not None:
            resolved_install_policy.require_allowed(
                provider=source.provider,
                version=selected,
                entry=entry,
            )
        source_root = self._materialize_validation_source(selected.source)
        self._verify_expected_checksum(
            plugin_root=source_root,
            expected_sha256=selected.sha256,
        )
        record, diagnostics = self.validate_plugin(
            plugin_root=source_root,
            scope=scope,
            require_manifest=True,
            strict_explicit_paths=True,
        )
        records = () if record is None else (record,)
        return PluginRegistry(plugins=records, diagnostics=diagnostics)

    def install_from_source(
        self,
        *,
        source: PluginInstallSource,
        scope: PluginScope,
        enabled: bool = True,
        resolved_install_source: PluginInstallSource | None = None,
        expected_sha256: str = "",
        extra_dependencies: tuple[PluginDependency, ...] = (),
    ) -> PluginStateRecord:
        self._require_mutable_scope(scope)
        install_source = resolved_install_source or source
        source_root = self._materialize_validation_source(install_source)
        self._verify_expected_checksum(
            plugin_root=source_root,
            expected_sha256=expected_sha256,
        )
        runtime_plugin_records = self.load_registry().enabled_plugins()
        record = self._strict_load_record(
            plugin_root=source_root,
            scope=scope,
            require_manifest=True,
            strict_explicit_paths=True,
            runtime_plugin_records=runtime_plugin_records,
        )
        installed_scope = self._installed_scope_for_name(record.name)
        if installed_scope is not None:
            raise ValueError(
                f"Plugin already installed in {installed_scope.value}: {record.name}"
            )
        state_file = self._require_state_file(scope)
        state = self._read_state_file_strict(state_file)
        self._validate_dependencies(
            record=record,
            state=self._dependency_state_for_mutation(),
            allow_current_name=None,
            extra_dependencies=extra_dependencies,
        )
        target_dir = get_installed_plugin_version_dir(
            plugin_name=record.name,
            version=record.version,
            app_config_dir=self._app_config_dir,
        )
        copied_target = False
        if not target_dir.exists():
            install_plugin_source(
                source=PluginInstallSource(
                    kind=PluginInstallSourceKind.LOCAL,
                    value=str(source_root),
                ),
                app_config_dir=self._app_config_dir,
                target_dir=target_dir,
            )
            copied_target = True
        else:
            self._verify_existing_installed_copy(
                source_root=source_root,
                target_dir=target_dir,
            )
        try:
            self._verify_expected_checksum(
                plugin_root=target_dir,
                expected_sha256=expected_sha256,
            )
            installed_record = self._strict_load_record(
                plugin_root=target_dir,
                scope=scope,
                require_manifest=True,
                strict_explicit_paths=True,
            )
            self._validate_installed_capabilities_if_changed(
                source_root=source_root,
                target_dir=target_dir,
                installed_record=installed_record,
                runtime_plugin_records=runtime_plugin_records,
            )
        except ValueError:
            if copied_target:
                self._remove_directory_under(
                    parent=get_plugin_installed_root(
                        app_config_dir=self._app_config_dir
                    ),
                    target=target_dir,
                )
            raise
        if (
            installed_record.name != record.name
            or installed_record.version != record.version
        ):
            if copied_target:
                self._remove_directory_under(
                    parent=get_plugin_installed_root(
                        app_config_dir=self._app_config_dir
                    ),
                    target=target_dir,
                )
            raise ValueError(
                "Installed plugin target does not match requested plugin "
                f"{record.name}@{record.version}"
            )
        state_record = PluginStateRecord(
            name=installed_record.name,
            version=installed_record.version,
            scope=scope,
            enabled=enabled,
            root_dir=target_dir,
            source=source,
            user_config=self._persist_sensitive_user_config(
                record=installed_record,
                scope=scope,
                user_config=self._validated_user_config(
                    record=installed_record,
                    user_config=installed_record.user_config,
                    require_required=False,
                ),
            ),
            dependencies=extra_dependencies,
        )
        self._write_state_file(
            state_file,
            PluginStateFile(plugins=state.plugins + (state_record,)),
        )
        return state_record

    def update_plugin(
        self,
        *,
        name: str,
        scope: PluginScope,
        version: str | None = None,
        install_policy: PluginMarketplaceInstallPolicy | None = None,
    ) -> PluginStateRecord:
        self._require_mutable_scope(scope)
        state_file = self._require_state_file(scope)
        state = self._read_state_file_strict(state_file)
        current = self._find_state_record(state=state, name=name, scope=scope)
        install_source, expected_sha256, extra_dependencies = (
            self._resolve_update_install_source(
                source=current.source,
                version=version,
                install_policy=install_policy,
            )
        )
        source_root = self._materialize_validation_source(install_source)
        self._verify_expected_checksum(
            plugin_root=source_root,
            expected_sha256=expected_sha256,
        )
        runtime_plugin_records = self.load_registry().enabled_plugins()
        record = self._strict_load_record(
            plugin_root=source_root,
            scope=scope,
            require_manifest=True,
            strict_explicit_paths=True,
            runtime_plugin_records=runtime_plugin_records,
        )
        if record.name != current.name:
            raise ValueError(
                f"Updated plugin source resolved to {record.name}, expected {current.name}"
            )
        if record.version == current.version:
            raise ValueError(f"Plugin is already at version {current.version}: {name}")
        self._validate_dependencies(
            record=record,
            state=self._dependency_state_for_mutation(),
            allow_current_name=current.name,
            extra_dependencies=extra_dependencies,
        )
        target_dir = get_installed_plugin_version_dir(
            plugin_name=record.name,
            version=record.version,
            app_config_dir=self._app_config_dir,
        )
        copied_target = False
        if not target_dir.exists():
            install_plugin_source(
                source=install_source,
                app_config_dir=self._app_config_dir,
                target_dir=target_dir,
            )
            copied_target = True
        else:
            self._verify_existing_installed_copy(
                source_root=source_root,
                target_dir=target_dir,
            )
        try:
            self._verify_expected_checksum(
                plugin_root=target_dir,
                expected_sha256=expected_sha256,
            )
            installed_record = self._strict_load_record(
                plugin_root=target_dir,
                scope=scope,
                require_manifest=True,
                strict_explicit_paths=True,
            )
            self._validate_installed_capabilities_if_changed(
                source_root=source_root,
                target_dir=target_dir,
                installed_record=installed_record,
                runtime_plugin_records=runtime_plugin_records,
            )
        except ValueError:
            if copied_target:
                self._remove_directory_under(
                    parent=get_plugin_installed_root(
                        app_config_dir=self._app_config_dir
                    ),
                    target=target_dir,
                )
            raise
        if (
            installed_record.name != record.name
            or installed_record.version != record.version
        ):
            if copied_target:
                self._remove_directory_under(
                    parent=get_plugin_installed_root(
                        app_config_dir=self._app_config_dir
                    ),
                    target=target_dir,
                )
            raise ValueError(
                "Installed plugin target does not match requested plugin "
                f"{record.name}@{record.version}"
            )
        updated_records: list[PluginStateRecord] = []
        updated_record: PluginStateRecord | None = None
        for item in state.plugins:
            if item.name == name:
                merged_user_config = self._merged_user_config_for_update(
                    current=item,
                    installed_record=installed_record,
                )
                updated_record = item.model_copy(
                    update={
                        "version": record.version,
                        "root_dir": target_dir,
                        "source": self._updated_persisted_source(
                            source=current.source,
                            version=version or record.version,
                        ),
                        "user_config": self._persist_sensitive_user_config(
                            record=installed_record,
                            scope=scope,
                            user_config=merged_user_config,
                            remove_sensitive_fields=(
                                self._removed_sensitive_fields_for_update(
                                    current=item,
                                    installed_record=installed_record,
                                )
                            ),
                        ),
                        "dependencies": extra_dependencies,
                    }
                )
                updated_records.append(updated_record)
                continue
            updated_records.append(item)
        if updated_record is None:
            raise ValueError(f"Plugin is not installed in {scope.value}: {name}")
        self._write_state_file(
            state_file,
            PluginStateFile(plugins=tuple(updated_records)),
        )
        return updated_record

    def set_plugin_user_config(
        self,
        *,
        name: str,
        scope: PluginScope,
        user_config: dict[str, JsonValue],
    ) -> PluginStateRecord:
        self._require_mutable_scope(scope)
        state_file = self._require_state_file(scope)
        state = self._read_state_file_strict(state_file)
        current = self._find_state_record(state=state, name=name, scope=scope)
        installed_record = self._strict_load_record(
            plugin_root=current.root_dir,
            scope=scope,
            require_manifest=True,
            strict_explicit_paths=False,
        )
        merged_user_config = self._merged_current_user_config_for_save(
            installed_record=installed_record,
            current=current,
            user_config=user_config,
        )
        validated_user_config = self._validated_user_config(
            record=installed_record,
            user_config=merged_user_config,
            require_required=True,
        )
        persisted_user_config = self._persist_sensitive_user_config(
            record=installed_record,
            scope=scope,
            user_config=validated_user_config,
        )
        updated_records: list[PluginStateRecord] = []
        updated_record: PluginStateRecord | None = None
        for item in state.plugins:
            if item.name == name:
                updated_record = item.model_copy(
                    update={"user_config": persisted_user_config}
                )
                updated_records.append(updated_record)
                continue
            updated_records.append(item)
        if updated_record is None:
            raise ValueError(f"Plugin is not installed in {scope.value}: {name}")
        self._write_state_file(
            state_file,
            PluginStateFile(plugins=tuple(updated_records)),
        )
        return updated_record

    def set_plugin_enabled(
        self,
        *,
        name: str,
        scope: PluginScope,
        enabled: bool,
    ) -> PluginStateRecord:
        self._require_mutable_scope(scope)
        state_file = self._require_state_file(scope)
        state = self._read_state_file_strict(state_file)
        updated_records: list[PluginStateRecord] = []
        updated_record: PluginStateRecord | None = None
        for record in state.plugins:
            if record.name == name:
                if enabled:
                    installed_record = self._strict_load_record(
                        plugin_root=record.root_dir,
                        scope=scope,
                        require_manifest=True,
                        strict_explicit_paths=False,
                    )
                    self._validated_user_config(
                        record=installed_record,
                        user_config=self._resolved_user_config(
                            record=installed_record,
                            state_record=record,
                        ),
                        require_required=True,
                    )
                updated_record = record.model_copy(update={"enabled": enabled})
                updated_records.append(updated_record)
                continue
            updated_records.append(record)
        if updated_record is None:
            raise ValueError(f"Plugin is not installed in {scope.value}: {name}")
        self._write_state_file(
            state_file,
            PluginStateFile(plugins=tuple(updated_records)),
        )
        return updated_record

    def uninstall_plugin(
        self,
        *,
        name: str,
        scope: PluginScope,
        prune: bool = False,
    ) -> PluginStateRecord:
        self._require_mutable_scope(scope)
        state_file = self._require_state_file(scope)
        state = self._read_state_file_strict(state_file)
        kept_records: list[PluginStateRecord] = []
        removed_record: PluginStateRecord | None = None
        for record in state.plugins:
            if record.name == name:
                removed_record = record
                continue
            kept_records.append(record)
        if removed_record is None:
            raise ValueError(f"Plugin is not installed in {scope.value}: {name}")
        if prune:
            self._list_state_records_strict()
        self._write_state_file(
            state_file,
            PluginStateFile(plugins=tuple(kept_records)),
        )
        if prune:
            try:
                self.prune_installed_plugins()
            except Exception:
                self._write_state_file(state_file, state)
                raise
        self._user_config_secret_store.delete_plugin(
            self._app_config_dir,
            plugin_name=removed_record.name,
            scope=scope,
        )
        return removed_record

    def prune_installed_plugins(self) -> tuple[Path, ...]:
        installed_root = get_plugin_installed_root(app_config_dir=self._app_config_dir)
        if not installed_root.exists():
            return ()
        referenced_roots = {
            record.root_dir.expanduser().resolve()
            for record in self._list_state_records_strict()
        }
        removed: list[Path] = []
        for plugin_dir in sorted(installed_root.iterdir()):
            if not plugin_dir.is_dir():
                continue
            for version_dir in sorted(plugin_dir.iterdir()):
                resolved_version_dir = version_dir.resolve()
                if resolved_version_dir in referenced_roots:
                    continue
                self._remove_directory_under(
                    parent=installed_root,
                    target=resolved_version_dir,
                )
                removed.append(resolved_version_dir)
        return tuple(removed)

    def list_state_records(self) -> tuple[PluginStateRecord, ...]:
        records: list[PluginStateRecord] = []
        for _, state_file in self._iter_existing_state_files():
            try:
                records.extend(self._read_state_file_strict(state_file).plugins)
            except ValueError:
                continue
        return tuple(records)

    def _list_state_records_strict(self) -> tuple[PluginStateRecord, ...]:
        records: list[PluginStateRecord] = []
        for _, state_file in self._iter_existing_state_files():
            records.extend(self._read_state_file_strict(state_file).plugins)
        return tuple(records)

    def _append_state_record(
        self,
        *,
        state_record: PluginStateRecord,
        data_root: Path,
        records: list[PluginRecord],
        diagnostics: list[PluginDiagnostic],
        seen_names: set[str],
        enabled_override: bool | None,
    ) -> None:
        root_dir = state_record.root_dir.expanduser().resolve()
        if not root_dir.exists() or not root_dir.is_dir():
            diagnostics.append(
                PluginDiagnostic(
                    plugin_name=state_record.name,
                    scope=state_record.scope,
                    severity=PluginDiagnosticSeverity.ERROR,
                    path=root_dir,
                    message="Installed plugin directory does not exist",
                )
            )
            return
        record, load_diagnostics = load_plugin_record(
            plugin_root=root_dir,
            data_root=data_root,
            manifest_config_dir_name=self._app_config_dir.name,
            scope=state_record.scope,
        )
        diagnostics.extend(load_diagnostics)
        if record is None:
            return
        if record.name != state_record.name:
            diagnostics.append(
                PluginDiagnostic(
                    plugin_name=state_record.name,
                    scope=state_record.scope,
                    severity=PluginDiagnosticSeverity.ERROR,
                    path=record.manifest_path or root_dir,
                    message=(
                        "Installed plugin manifest name does not match "
                        f"state record: {record.name}"
                    ),
                )
            )
            return
        resolved_user_config = self._resolved_user_config(
            record=record,
            state_record=state_record,
        )
        public_user_config = self._public_user_config(
            record=record,
            user_config=resolved_user_config,
        )
        missing_required = self._missing_required_user_config(
            record=record,
            user_config=resolved_user_config,
        )
        if missing_required:
            diagnostics.append(
                PluginDiagnostic(
                    plugin_name=state_record.name,
                    scope=state_record.scope,
                    severity=PluginDiagnosticSeverity.ERROR,
                    path=record.manifest_path or root_dir,
                    message=(
                        "Missing required plugin user_config field(s): "
                        + ", ".join(missing_required)
                    ),
                )
            )
            enabled_override = False
        runtime_record = self._record_with_user_config(
            record=record,
            public_user_config=public_user_config,
            runtime_user_config=resolved_user_config,
            diagnostics=diagnostics,
        ).model_copy(
            update={
                "enabled": state_record.enabled
                if enabled_override is None
                else enabled_override,
                "source": state_record.source,
            }
        )
        self._append_record(
            record=runtime_record,
            records=records,
            diagnostics=diagnostics,
            seen_names=seen_names,
        )

    def _append_plugin_dir(
        self,
        *,
        plugin_dir: Path,
        data_root: Path,
        records: list[PluginRecord],
        diagnostics: list[PluginDiagnostic],
        seen_names: set[str],
        scope: PluginScope,
        enabled_override: bool | None = None,
    ) -> None:
        if not plugin_dir.exists() or not plugin_dir.is_dir():
            diagnostics.append(
                PluginDiagnostic(
                    scope=scope,
                    severity=PluginDiagnosticSeverity.ERROR,
                    path=plugin_dir,
                    message="Plugin directory does not exist",
                )
            )
            return
        record, load_diagnostics = load_plugin_record(
            plugin_root=plugin_dir,
            data_root=data_root,
            manifest_config_dir_name=self._app_config_dir.name,
            scope=scope,
        )
        diagnostics.extend(load_diagnostics)
        if record is not None:
            diagnostics.extend(plugin_command_audit_diagnostics(record))
            runtime_user_config = dict(record.user_config)
            missing_required = self._missing_required_user_config(
                record=record,
                user_config=runtime_user_config,
            )
            if missing_required:
                diagnostics.append(
                    PluginDiagnostic(
                        plugin_name=record.name,
                        scope=record.scope,
                        severity=PluginDiagnosticSeverity.ERROR,
                        path=record.manifest_path or record.root_dir,
                        message=(
                            "Missing required plugin user_config field(s): "
                            + ", ".join(missing_required)
                        ),
                    )
                )
                enabled_override = False
            if enabled_override is not None:
                record = record.model_copy(update={"enabled": enabled_override})
            self._append_record(
                record=record,
                records=records,
                diagnostics=diagnostics,
                seen_names=seen_names,
            )

    @staticmethod
    def _append_record(
        *,
        record: PluginRecord,
        records: list[PluginRecord],
        diagnostics: list[PluginDiagnostic],
        seen_names: set[str],
    ) -> None:
        if record.name in seen_names:
            diagnostics.append(
                PluginDiagnostic(
                    plugin_name=record.name,
                    scope=record.scope,
                    severity=PluginDiagnosticSeverity.ERROR,
                    path=record.manifest_path or record.root_dir,
                    message=f"Duplicate plugin name skipped: {record.name}",
                )
            )
            return
        seen_names.add(record.name)
        records.append(record)

    def _load_runtime_state_records(
        self,
        diagnostics: list[PluginDiagnostic],
    ) -> tuple[PluginStateRecord, ...]:
        records: list[PluginStateRecord] = []
        for scope, state_file in self._iter_existing_state_files():
            try:
                records.extend(self._read_state_file_strict(state_file).plugins)
            except ValueError as exc:
                diagnostics.append(
                    PluginDiagnostic(
                        scope=scope,
                        severity=PluginDiagnosticSeverity.ERROR,
                        path=state_file,
                        message=f"Invalid plugin state file: {exc}",
                    )
                )
        return tuple(records)

    def _dependency_diagnostics(
        self,
        *,
        state_records: tuple[PluginStateRecord, ...],
        available_records: tuple[PluginStateRecord, ...],
    ) -> dict[str, PluginDiagnostic]:
        diagnostics: dict[str, PluginDiagnostic] = {}
        unavailable_names = {
            record.name for record in available_records if not record.enabled
        }
        changed = True
        while changed:
            changed = False
            for state_record in state_records:
                if state_record.name in diagnostics or not state_record.enabled:
                    continue
                diagnostic = self._dependency_diagnostic(
                    state_record=state_record,
                    available_records=available_records,
                    unavailable_names=unavailable_names,
                )
                if diagnostic is None:
                    continue
                diagnostics[state_record.name] = diagnostic
                unavailable_names.add(state_record.name)
                changed = True
        return diagnostics

    def _dependency_diagnostic(
        self,
        *,
        state_record: PluginStateRecord,
        available_records: tuple[PluginStateRecord, ...],
        unavailable_names: set[str],
    ) -> PluginDiagnostic | None:
        record, _ = load_plugin_record(
            plugin_root=state_record.root_dir,
            data_root=get_plugin_data_root(app_config_dir=self._app_config_dir),
            manifest_config_dir_name=self._app_config_dir.name,
            scope=state_record.scope,
        )
        if record is None:
            return PluginDiagnostic(
                plugin_name=state_record.name,
                scope=state_record.scope,
                severity=PluginDiagnosticSeverity.ERROR,
                path=state_record.root_dir,
                message="Plugin manifest is unavailable",
            )
        installed: dict[str, PluginStateRecord] = {}
        for item in available_records:
            if item.name != state_record.name and item.name not in installed:
                installed[item.name] = item
        for dependency in (*record.manifest.dependencies, *state_record.dependencies):
            installed_dependency = installed.get(dependency.name)
            if installed_dependency is None:
                return PluginDiagnostic(
                    plugin_name=state_record.name,
                    scope=state_record.scope,
                    severity=PluginDiagnosticSeverity.ERROR,
                    path=state_record.root_dir,
                    message=f"Missing plugin dependency: {dependency.name}",
                )
            if not installed_dependency.enabled:
                return PluginDiagnostic(
                    plugin_name=state_record.name,
                    scope=state_record.scope,
                    severity=PluginDiagnosticSeverity.ERROR,
                    path=state_record.root_dir,
                    message=f"Plugin dependency is disabled: {dependency.name}",
                )
            if installed_dependency.name in unavailable_names:
                return PluginDiagnostic(
                    plugin_name=state_record.name,
                    scope=state_record.scope,
                    severity=PluginDiagnosticSeverity.ERROR,
                    path=state_record.root_dir,
                    message=f"Plugin dependency is unavailable: {dependency.name}",
                )
            if (
                dependency.version is not None
                and installed_dependency.version != dependency.version
            ):
                return PluginDiagnostic(
                    plugin_name=state_record.name,
                    scope=state_record.scope,
                    severity=PluginDiagnosticSeverity.ERROR,
                    path=state_record.root_dir,
                    message=(
                        "Plugin dependency version mismatch: "
                        f"{dependency.name} requires {dependency.version}, "
                        f"installed {installed_dependency.version}"
                    ),
                )
        return None

    def _iter_existing_state_files(self) -> tuple[tuple[PluginScope, Path], ...]:
        candidates: list[tuple[PluginScope, Path | None]] = [
            (
                PluginScope.MANAGED,
                get_plugin_managed_state_file(),
            ),
            (
                PluginScope.PROJECT_LOCAL,
                get_plugin_project_local_state_file(
                    app_config_dir=self._app_config_dir,
                    project_root=self._project_root,
                ),
            ),
            (
                PluginScope.PROJECT,
                get_plugin_project_state_file(
                    app_config_dir=self._app_config_dir,
                    project_root=self._project_root,
                ),
            ),
            (
                PluginScope.USER,
                get_plugin_user_state_file(app_config_dir=self._app_config_dir),
            ),
        ]
        return tuple(
            (scope, path)
            for scope, path in candidates
            if path is not None and path.exists()
        )

    def _require_state_file(self, scope: PluginScope) -> Path:
        state_file = get_plugin_state_file(
            scope=scope,
            app_config_dir=self._app_config_dir,
            project_root=self._project_root,
        )
        if state_file is None:
            raise ValueError(f"Unsupported plugin scope for persisted state: {scope}")
        return state_file

    def _strict_load_record(
        self,
        *,
        plugin_root: Path,
        scope: PluginScope,
        require_manifest: bool = False,
        strict_explicit_paths: bool = False,
        runtime_plugin_records: tuple[PluginRecord, ...] | None = None,
    ) -> PluginRecord:
        record, diagnostics = self.validate_plugin(
            plugin_root=plugin_root,
            scope=scope,
            require_manifest=require_manifest,
            strict_explicit_paths=strict_explicit_paths,
            runtime_plugin_records=runtime_plugin_records,
        )
        error_messages = tuple(
            diagnostic.message
            for diagnostic in diagnostics
            if diagnostic.severity == PluginDiagnosticSeverity.ERROR
        )
        if record is None or error_messages:
            joined = "; ".join(error_messages) or "Invalid plugin"
            raise ValueError(joined)
        return record

    @staticmethod
    def _validate_dependencies(
        *,
        record: PluginRecord,
        state: PluginStateFile,
        allow_current_name: str | None,
        extra_dependencies: tuple[PluginDependency, ...] = (),
    ) -> None:
        installed: dict[str, PluginStateRecord] = {}
        for item in state.plugins:
            if item.name != allow_current_name and item.name not in installed:
                installed[item.name] = item
        for dependency in (*record.manifest.dependencies, *extra_dependencies):
            installed_dependency = installed.get(dependency.name)
            if installed_dependency is None:
                raise ValueError(f"Missing plugin dependency: {dependency.name}")
            if not installed_dependency.enabled:
                raise ValueError(f"Plugin dependency is disabled: {dependency.name}")
            if (
                dependency.version is not None
                and installed_dependency.version != dependency.version
            ):
                raise ValueError(
                    "Plugin dependency version mismatch: "
                    f"{dependency.name} requires {dependency.version}, "
                    f"installed {installed_dependency.version}"
                )

    @staticmethod
    def _validated_user_config(
        *,
        record: PluginRecord,
        user_config: dict[str, JsonValue],
        require_required: bool,
    ) -> dict[str, JsonValue]:
        declared_fields = record.manifest.user_config
        unknown_keys = sorted(set(user_config) - set(declared_fields))
        if unknown_keys:
            raise ValueError(
                "Unknown plugin user_config field(s): " + ", ".join(unknown_keys)
            )
        values = dict(record.user_config)
        values.update(user_config)
        if require_required:
            missing_required = sorted(
                key
                for key, field in declared_fields.items()
                if field.required and key not in values
            )
            if missing_required:
                raise ValueError(
                    "Missing required plugin user_config field(s): "
                    + ", ".join(missing_required)
                )
        for key, value in values.items():
            field = declared_fields.get(key)
            if field is None:
                continue
            _validate_user_config_field_type(
                key=key,
                field_type=field.type,
                value=value,
            )
        return values

    def _persist_sensitive_user_config(
        self,
        *,
        record: PluginRecord,
        scope: PluginScope,
        user_config: dict[str, JsonValue],
        remove_sensitive_fields: tuple[str, ...] = (),
    ) -> dict[str, JsonValue]:
        persisted: dict[str, JsonValue] = {}
        sensitive_keys: set[str] = set()
        for key, value in user_config.items():
            field = record.manifest.user_config.get(key)
            if field is None:
                continue
            if not field.sensitive:
                persisted[key] = value
                continue
            sensitive_keys.add(key)
            self._user_config_secret_store.set_field(
                self._app_config_dir,
                plugin_name=record.name,
                scope=scope,
                field_name=key,
                value=_secret_value(value),
            )
        for key in sorted(set(remove_sensitive_fields) - sensitive_keys):
            self._user_config_secret_store.delete_field(
                self._app_config_dir,
                plugin_name=record.name,
                scope=scope,
                field_name=key,
            )
        return persisted

    def _installed_scope_for_name(self, name: str) -> PluginScope | None:
        for record in self._list_state_records_strict():
            if record.name == name:
                return record.scope
        return None

    def _resolved_user_config(
        self,
        *,
        record: PluginRecord,
        state_record: PluginStateRecord,
    ) -> dict[str, JsonValue]:
        values = dict(state_record.user_config)
        for key, field in record.manifest.user_config.items():
            if not field.sensitive:
                continue
            secret_value = self._user_config_secret_store.get_field(
                self._app_config_dir,
                plugin_name=record.name,
                scope=state_record.scope,
                field_name=key,
            )
            if self._user_config_secret_store.has_field(
                self._app_config_dir,
                plugin_name=record.name,
                scope=state_record.scope,
                field_name=key,
            ):
                values[key] = secret_value
        return values

    @staticmethod
    def _public_user_config(
        *,
        record: PluginRecord,
        user_config: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        public: dict[str, JsonValue] = {}
        for key, value in user_config.items():
            field = record.manifest.user_config.get(key)
            if field is not None and field.sensitive:
                public[key] = "<configured>"
                continue
            public[key] = value
        return public

    def _merged_user_config_for_update(
        self,
        *,
        current: PluginStateRecord,
        installed_record: PluginRecord,
    ) -> dict[str, JsonValue]:
        current_values = self._resolved_user_config(
            record=installed_record,
            state_record=current,
        )
        previous_record = self._strict_load_record(
            plugin_root=current.root_dir,
            scope=current.scope,
            require_manifest=True,
            strict_explicit_paths=False,
        )
        previous_values = self._resolved_user_config(
            record=previous_record,
            state_record=current,
        )
        for key, field in installed_record.manifest.user_config.items():
            if field.sensitive or key in current_values or key not in previous_values:
                continue
            current_values[key] = previous_values[key]
        retained = {
            key: value
            for key, value in current_values.items()
            if key in installed_record.manifest.user_config
        }
        return self._validated_user_config(
            record=installed_record,
            user_config=retained,
            require_required=False,
        )

    def _removed_sensitive_fields_for_update(
        self,
        *,
        current: PluginStateRecord,
        installed_record: PluginRecord,
    ) -> tuple[str, ...]:
        previous_record = self._strict_load_record(
            plugin_root=current.root_dir,
            scope=current.scope,
            require_manifest=True,
            strict_explicit_paths=False,
        )
        previous_sensitive = {
            key
            for key, field in previous_record.manifest.user_config.items()
            if field.sensitive
        }
        current_sensitive = {
            key
            for key, field in installed_record.manifest.user_config.items()
            if field.sensitive
        }
        return tuple(sorted(previous_sensitive - current_sensitive))

    def _merged_current_user_config_for_save(
        self,
        *,
        installed_record: PluginRecord,
        current: PluginStateRecord,
        user_config: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        merged = self._resolved_user_config(
            record=installed_record,
            state_record=current,
        )
        for key, value in user_config.items():
            field = installed_record.manifest.user_config.get(key)
            if field is not None and _is_blank_optional_user_config_clear(
                field=field,
                value=value,
            ):
                merged.pop(key, None)
                continue
            merged[key] = value
        return merged

    def _record_with_user_config(
        self,
        *,
        record: PluginRecord,
        public_user_config: dict[str, JsonValue],
        runtime_user_config: dict[str, JsonValue],
        diagnostics: list[PluginDiagnostic],
    ) -> PluginRecord:
        monitor_definitions = self._monitor_definitions_with_user_config(
            sources=record.monitor_sources,
            user_config=runtime_user_config,
            diagnostics=diagnostics,
        )
        settings_sources = self._settings_sources_with_user_config(
            sources=record.settings_sources,
            user_config=runtime_user_config,
            diagnostics=diagnostics,
        )
        updated_record = record.model_copy(
            update={
                "user_config": public_user_config,
                "skill_sources": _sources_with_user_config(
                    sources=record.skill_sources,
                    user_config=runtime_user_config,
                ),
                "role_sources": _sources_with_user_config(
                    sources=record.role_sources,
                    user_config=runtime_user_config,
                ),
                "command_sources": _sources_with_user_config(
                    sources=record.command_sources,
                    user_config=runtime_user_config,
                ),
                "hook_sources": _sources_with_user_config(
                    sources=record.hook_sources,
                    user_config=runtime_user_config,
                ),
                "mcp_sources": _sources_with_user_config(
                    sources=record.mcp_sources,
                    user_config=runtime_user_config,
                ),
                "monitor_sources": _sources_with_user_config(
                    sources=record.monitor_sources,
                    user_config=runtime_user_config,
                ),
                "monitor_definitions": monitor_definitions,
                "settings_sources": settings_sources,
                "component_counts": self._component_counts(
                    record=record,
                    monitor_definitions=monitor_definitions,
                    settings_sources=settings_sources,
                ),
            }
        )
        diagnostics.extend(plugin_command_audit_diagnostics(updated_record))
        return updated_record

    @staticmethod
    def _component_counts(
        *,
        record: PluginRecord,
        monitor_definitions: tuple[PluginMonitorDefinition, ...],
        settings_sources: tuple[PluginSettingsSource, ...],
    ) -> PluginComponentCounts:
        return PluginComponentCounts(
            skills=len(record.skill_sources),
            roles=len(record.role_sources),
            commands=len(record.command_sources),
            hooks=len(record.hook_sources),
            mcp_servers=len(record.mcp_sources),
            monitors=len(monitor_definitions),
            settings=len(settings_sources),
        )

    @staticmethod
    def _monitor_definitions_with_user_config(
        *,
        sources: tuple[PluginComponentSource, ...],
        user_config: dict[str, JsonValue],
        diagnostics: list[PluginDiagnostic],
    ) -> tuple[PluginMonitorDefinition, ...]:
        definitions: list[PluginMonitorDefinition] = []
        for source in sources:
            source_definitions, source_diagnostics = load_plugin_monitor_definitions(
                source.model_copy(update={"user_config": user_config})
            )
            diagnostics.extend(source_diagnostics)
            definitions.extend(source_definitions)
        return tuple(definitions)

    @staticmethod
    def _settings_sources_with_user_config(
        *,
        sources: tuple[PluginSettingsSource, ...],
        user_config: dict[str, JsonValue],
        diagnostics: list[PluginDiagnostic],
    ) -> tuple[PluginSettingsSource, ...]:
        updated_sources: list[PluginSettingsSource] = []
        for source in sources:
            updated_source, source_diagnostics = reload_plugin_settings_source(
                source.model_copy(update={"user_config": user_config})
            )
            diagnostics.extend(source_diagnostics)
            if updated_source is not None:
                updated_sources.append(updated_source)
        return tuple(updated_sources)

    @staticmethod
    def _missing_required_user_config(
        *,
        record: PluginRecord,
        user_config: dict[str, JsonValue],
    ) -> tuple[str, ...]:
        return tuple(
            sorted(
                key
                for key, field in record.manifest.user_config.items()
                if field.required and key not in user_config
            )
        )

    def _resolve_update_install_source(
        self,
        *,
        source: PluginInstallSource,
        version: str | None,
        install_policy: PluginMarketplaceInstallPolicy | None = None,
    ) -> tuple[PluginInstallSource, str, tuple[PluginDependency, ...]]:
        if source.kind == PluginInstallSourceKind.MARKETPLACE:
            if not source.marketplace:
                raise ValueError(
                    "Marketplace plugin source is missing marketplace path"
                )
            provider = _marketplace_provider_from_string(source.marketplace_provider)
            resolved_install_policy = (
                install_policy
                or load_plugin_marketplace_install_policy(self._app_config_dir)
                if provider == PluginMarketplaceProviderKind.CLAWHUB
                else None
            )
            entry = PluginMarketplaceService().load_provider_entry(
                source=self._marketplace_source(
                    provider=provider,
                    marketplace=source.marketplace,
                    marketplace_source=source.marketplace_source,
                    marketplace_ref=source.marketplace_ref,
                    refresh=provider == PluginMarketplaceProviderKind.CLAUDE,
                ),
                name=source.value,
                app_config_dir=self._app_config_dir,
                install_policy=resolved_install_policy,
            )
            selected = entry.selected_version(version)
            if selected.unsupported_reason:
                raise ValueError(selected.unsupported_reason)
            return selected.source, selected.sha256, selected.dependencies
        if version is not None:
            raise ValueError(
                "Versioned plugin updates are only supported for marketplace plugins"
            )
        return source, "", ()

    @staticmethod
    def _verify_expected_checksum(*, plugin_root: Path, expected_sha256: str) -> None:
        if expected_sha256.strip():
            verify_plugin_tree_sha256(
                plugin_root=plugin_root,
                expected_sha256=expected_sha256,
            )

    @staticmethod
    def _verify_existing_installed_copy(*, source_root: Path, target_dir: Path) -> None:
        source_digest = compute_plugin_tree_sha256(source_root)
        target_digest = compute_plugin_tree_sha256(target_dir)
        if source_digest == target_digest:
            return
        raise ValueError(
            "Installed plugin copy already exists with different contents. "
            "Bump the plugin version or prune the unreferenced installed copy before "
            "reinstalling."
        )

    def _validate_installed_capabilities_if_changed(
        self,
        *,
        source_root: Path,
        target_dir: Path,
        installed_record: PluginRecord,
        runtime_plugin_records: tuple[PluginRecord, ...],
    ) -> None:
        if compute_plugin_tree_sha256(source_root) == compute_plugin_tree_sha256(
            target_dir
        ):
            return
        validate_plugin_capabilities(
            record=installed_record,
            app_config_dir=self._app_config_dir,
            project_start_dir=self._project_start_dir,
            runtime_plugin_records=runtime_plugin_records,
        )

    @staticmethod
    def _updated_persisted_source(
        *,
        source: PluginInstallSource,
        version: str,
    ) -> PluginInstallSource:
        if source.kind == PluginInstallSourceKind.MARKETPLACE:
            return source.model_copy(update={"requested_version": version})
        return source

    def _materialize_validation_source(self, source: PluginInstallSource) -> Path:
        if source.kind == PluginInstallSourceKind.LOCAL:
            if not source.adapter.strip():
                return Path(source.value).expanduser().resolve()
            cache_root = self._app_config_dir / "plugins" / "cache" / "validation"
            cache_root.mkdir(parents=True, exist_ok=True)
            target_dir = cache_root / self._cache_dir_name(source.value)
            if target_dir.exists():
                self._remove_directory_under(parent=cache_root, target=target_dir)
            install_plugin_source(
                source=source,
                app_config_dir=self._app_config_dir,
                target_dir=target_dir,
            )
            return target_dir
        if source.kind == PluginInstallSourceKind.GIT:
            cache_root = self._app_config_dir / "plugins" / "cache" / "validation"
            cache_root.mkdir(parents=True, exist_ok=True)
            target_dir = cache_root / self._cache_dir_name(source.value)
            if target_dir.exists():
                self._remove_directory_under(parent=cache_root, target=target_dir)
            install_plugin_source(
                source=source,
                app_config_dir=self._app_config_dir,
                target_dir=target_dir,
            )
            return target_dir
        if source.kind == PluginInstallSourceKind.GIT_SUBDIR:
            cache_root = self._app_config_dir / "plugins" / "cache" / "validation"
            cache_root.mkdir(parents=True, exist_ok=True)
            target_dir = cache_root / self._cache_dir_name(
                f"{source.value}:{source.subdir}"
            )
            if target_dir.exists():
                self._remove_directory_under(parent=cache_root, target=target_dir)
            install_plugin_source(
                source=source,
                app_config_dir=self._app_config_dir,
                target_dir=target_dir,
            )
            return target_dir
        if source.kind == PluginInstallSourceKind.HTTP_ARCHIVE:
            cache_root = self._app_config_dir / "plugins" / "cache" / "validation"
            cache_root.mkdir(parents=True, exist_ok=True)
            target_dir = cache_root / self._cache_dir_name(source.value)
            if target_dir.exists():
                self._remove_directory_under(parent=cache_root, target=target_dir)
            install_plugin_source(
                source=source,
                app_config_dir=self._app_config_dir,
                target_dir=target_dir,
            )
            return target_dir
        if source.kind == PluginInstallSourceKind.UNSUPPORTED:
            raise ValueError(f"Unsupported plugin source kind: {source.value}")
        raise ValueError(
            f"Unsupported plugin source for validation: {source.kind.value}"
        )

    @staticmethod
    def _marketplace_source(
        *,
        provider: PluginMarketplaceProviderKind,
        marketplace: str,
        marketplace_source: str,
        marketplace_ref: str = "",
        refresh: bool = False,
    ) -> PluginMarketplaceSource:
        if provider == PluginMarketplaceProviderKind.LOCAL_JSON:
            return PluginMarketplaceSource(
                provider=provider,
                name=Path(marketplace).stem,
                value=marketplace,
            )
        return PluginMarketplaceSource(
            provider=provider,
            name=marketplace,
            value=PluginConfigManager._marketplace_source_reference(
                provider=provider,
                marketplace_source=marketplace_source,
            ),
            ref=marketplace_ref,
            refresh=refresh,
        )

    @staticmethod
    def _marketplace_source_reference(
        *,
        provider: PluginMarketplaceProviderKind,
        marketplace_source: str,
    ) -> str:
        if provider != PluginMarketplaceProviderKind.CLAUDE:
            return marketplace_source
        normalized = marketplace_source.strip()
        if not normalized:
            return ""
        local_path = Path(normalized).expanduser()
        if local_path.exists():
            return str(local_path.resolve())
        return normalized

    def _dependency_state_for_mutation(self) -> PluginStateFile:
        runtime_records_by_name = {
            record.name: record for record in self.load_registry().plugins
        }
        return PluginStateFile(
            plugins=tuple(
                self._state_record_with_runtime_availability(
                    state_record=state_record,
                    runtime_records_by_name=runtime_records_by_name,
                )
                for state_record in (
                    self._list_state_records_strict()
                    + self._local_plugin_state_records()
                )
            )
        )

    @staticmethod
    def _state_record_with_runtime_availability(
        *,
        state_record: PluginStateRecord,
        runtime_records_by_name: dict[str, PluginRecord],
    ) -> PluginStateRecord:
        runtime_record = runtime_records_by_name.get(state_record.name)
        if runtime_record is None:
            return state_record.model_copy(update={"enabled": False})
        return state_record.model_copy(
            update={"enabled": state_record.enabled and runtime_record.enabled}
        )

    def _local_plugin_state_records(self) -> tuple[PluginStateRecord, ...]:
        records: list[PluginStateRecord] = []
        data_root = get_plugin_data_root(app_config_dir=self._app_config_dir)
        for plugin_root in self._plugin_dirs:
            record, _ = load_plugin_record(
                plugin_root=plugin_root,
                data_root=data_root,
                manifest_config_dir_name=self._app_config_dir.name,
                scope=PluginScope.LOCAL,
            )
            if record is None:
                continue
            missing_required = self._missing_required_user_config(
                record=record,
                user_config=dict(record.user_config),
            )
            records.append(
                PluginStateRecord(
                    name=record.name,
                    version=record.version,
                    scope=PluginScope.LOCAL,
                    enabled=record.enabled and not missing_required,
                    root_dir=record.root_dir,
                    source=PluginInstallSource(
                        kind=PluginInstallSourceKind.LOCAL,
                        value=str(record.root_dir),
                    ),
                )
            )
        return tuple(records)

    @staticmethod
    def _find_state_record(
        *,
        state: PluginStateFile,
        name: str,
        scope: PluginScope,
    ) -> PluginStateRecord:
        for record in state.plugins:
            if record.name == name:
                return record
        raise ValueError(f"Plugin is not installed in {scope.value}: {name}")

    @staticmethod
    def _read_state_file_strict(state_file: Path) -> PluginStateFile:
        if not state_file.exists():
            return PluginStateFile()
        try:
            raw = json.loads(state_file.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ValueError(str(exc)) from exc
        if not isinstance(raw, dict):
            raise ValueError("Plugin state file must be a JSON object")
        try:
            return PluginStateFile.model_validate(raw)
        except Exception as exc:
            raise ValueError(str(exc)) from exc

    @staticmethod
    def _write_state_file(state_file: Path, state: PluginStateFile) -> None:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(
                state.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _require_mutable_scope(scope: PluginScope) -> None:
        if scope == PluginScope.MANAGED:
            raise ValueError(_MANAGED_SCOPE_ERROR)
        if scope == PluginScope.LOCAL:
            raise ValueError("Local development plugins are configured with env vars")

    @staticmethod
    def _remove_directory_under(*, parent: Path, target: Path) -> None:
        resolved_parent = parent.expanduser().resolve()
        resolved_target = target.expanduser().resolve()
        try:
            resolved_target.relative_to(resolved_parent)
        except ValueError as exc:
            raise ValueError(
                "Refusing to remove directory outside plugin storage"
            ) from exc
        shutil.rmtree(resolved_target)

    @staticmethod
    def _cache_dir_name(value: str) -> str:
        readable = "".join(char if char.isalnum() else "_" for char in value).strip("_")
        prefix = readable[:16].strip("_") or "git"
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        return f"{prefix}_{digest}"


def _plugin_dirs_from_env() -> tuple[Path, ...]:
    raw_value = environ.get(_PLUGIN_DIRS_ENV_VAR, "").strip()
    if not raw_value:
        return ()
    paths: list[Path] = []
    for item in raw_value.split(pathsep):
        normalized = item.strip()
        if normalized:
            paths.append(Path(normalized))
    return tuple(paths)


def _marketplace_provider_from_string(value: str) -> PluginMarketplaceProviderKind:
    normalized = value.strip()
    if not normalized:
        return PluginMarketplaceProviderKind.LOCAL_JSON
    try:
        return PluginMarketplaceProviderKind(normalized)
    except ValueError as exc:
        raise ValueError(f"Unsupported plugin marketplace provider: {value}") from exc


def _sources_with_user_config(
    *,
    sources: tuple[PluginComponentSource, ...],
    user_config: dict[str, JsonValue],
) -> tuple[PluginComponentSource, ...]:
    return tuple(
        source.model_copy(update={"user_config": user_config}) for source in sources
    )


def _secret_value(value: JsonValue) -> JsonValue:
    return value


def _is_blank_optional_user_config_clear(
    *,
    field: PluginUserConfigField,
    value: JsonValue,
) -> bool:
    if field.required or value != "":
        return False
    return _normalized_user_config_type(field.type) not in {
        "string",
        "text",
        "password",
    }


def _validate_user_config_field_type(
    *,
    key: str,
    field_type: str,
    value: JsonValue,
) -> None:
    normalized = _normalized_user_config_type(field_type)
    if normalized in {"string", "text", "password"}:
        if isinstance(value, str):
            return
    elif normalized in {"number", "float"}:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return
    elif normalized in {"integer", "int"}:
        if isinstance(value, int) and not isinstance(value, bool):
            return
    elif normalized in {"boolean", "bool"}:
        if isinstance(value, bool):
            return
    elif normalized in {"array", "list"}:
        if isinstance(value, list):
            return
    elif normalized in {"object", "dict"}:
        if isinstance(value, dict):
            return
    elif normalized in {"json", "any"}:
        return
    else:
        raise ValueError(f"Unsupported plugin user_config type for {key}: {field_type}")
    raise ValueError(
        f"Plugin user_config field {key} must be {normalized}, got "
        f"{_json_type_name(value)}"
    )


def _normalized_user_config_type(field_type: str) -> str:
    return field_type.strip().lower() or "string"


def _json_type_name(value: JsonValue) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"
