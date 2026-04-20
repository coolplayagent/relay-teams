# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.workspace.ssh_profile_models import SshProfileConfig, SshProfileRecord
from relay_teams.workspace.ssh_profile_repository import SshProfileRepository


class SshProfileService:
    def __init__(self, *, repository: SshProfileRepository) -> None:
        self._repository = repository

    def list_profiles(self) -> tuple[SshProfileRecord, ...]:
        return self._repository.list_all()

    def get_profile(self, ssh_profile_id: str) -> SshProfileRecord:
        return self._repository.get(ssh_profile_id)

    def save_profile(
        self,
        *,
        ssh_profile_id: str,
        config: SshProfileConfig,
    ) -> SshProfileRecord:
        return self._repository.save(
            ssh_profile_id=ssh_profile_id,
            config=config,
        )

    def delete_profile(self, ssh_profile_id: str) -> None:
        if not self._repository.exists(ssh_profile_id):
            raise KeyError(f"Unknown ssh_profile_id: {ssh_profile_id}")
        self._repository.delete(ssh_profile_id)

    def require_profile(self, ssh_profile_id: str) -> SshProfileRecord:
        return self.get_profile(ssh_profile_id)
