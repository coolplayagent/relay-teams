# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.workspace.ssh_profile_models import (
    SshProfileConfig,
    SshProfilePasswordRevealView,
    SshProfileRecord,
    SshProfileStoredConfig,
)
from relay_teams.workspace.ssh_profile_repository import SshProfileRepository
from relay_teams.workspace.ssh_profile_secret_store import (
    SshProfileSecretStore,
    get_ssh_profile_secret_store,
)


class SshProfileService:
    def __init__(
        self,
        *,
        repository: SshProfileRepository,
        config_dir: Path,
        secret_store: SshProfileSecretStore | None = None,
    ) -> None:
        self._repository = repository
        self._config_dir = Path(config_dir)
        self._secret_store = (
            get_ssh_profile_secret_store() if secret_store is None else secret_store
        )

    def list_profiles(self) -> tuple[SshProfileRecord, ...]:
        return tuple(
            self._enrich_record(record) for record in self._repository.list_all()
        )

    def get_profile(self, ssh_profile_id: str) -> SshProfileRecord:
        return self._enrich_record(self._repository.get(ssh_profile_id))

    def reveal_password(self, ssh_profile_id: str) -> SshProfilePasswordRevealView:
        _ = self._repository.get(ssh_profile_id)
        return SshProfilePasswordRevealView(
            password=self._secret_store.get_password(
                self._config_dir,
                ssh_profile_id,
            )
        )

    def save_profile(
        self,
        *,
        ssh_profile_id: str,
        config: SshProfileConfig,
    ) -> SshProfileRecord:
        existing = self._get_existing_record(ssh_profile_id)
        has_existing_private_key = (
            False
            if existing is None
            else self._secret_store.get_secret_flags(
                self._config_dir,
                ssh_profile_id,
            )[1]
        )
        record = self._repository.save(
            ssh_profile_id=ssh_profile_id,
            config=SshProfileStoredConfig(
                host=config.host,
                username=config.username,
                port=config.port,
                remote_shell=config.remote_shell,
                connect_timeout_seconds=config.connect_timeout_seconds,
                private_key_name=(
                    config.private_key_name
                    if config.private_key is not None
                    else (
                        existing.private_key_name
                        if existing is not None and has_existing_private_key
                        else None
                    )
                ),
            ),
        )
        if config.password is not None:
            self._secret_store.set_password(
                self._config_dir,
                ssh_profile_id,
                config.password,
            )
        if config.private_key is not None:
            self._secret_store.set_private_key(
                self._config_dir,
                ssh_profile_id,
                config.private_key,
            )
        return self._enrich_record(record)

    def delete_profile(self, ssh_profile_id: str) -> None:
        if not self._repository.exists(ssh_profile_id):
            raise KeyError(f"Unknown ssh_profile_id: {ssh_profile_id}")
        self._repository.delete(ssh_profile_id)
        self._secret_store.delete_profile_secrets(self._config_dir, ssh_profile_id)

    def require_profile(self, ssh_profile_id: str) -> SshProfileRecord:
        return self.get_profile(ssh_profile_id)

    def _enrich_record(self, record: SshProfileRecord) -> SshProfileRecord:
        has_password, has_private_key = self._secret_store.get_secret_flags(
            self._config_dir,
            record.ssh_profile_id,
        )
        return record.model_copy(
            update={
                "has_password": has_password,
                "has_private_key": has_private_key,
            }
        )

    def _get_existing_record(self, ssh_profile_id: str) -> SshProfileRecord | None:
        try:
            return self._repository.get(ssh_profile_id)
        except KeyError:
            return None
