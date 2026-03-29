# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.roles.temporary_role_models import (
    TemporaryRoleRecord,
    TemporaryRoleSource,
    TemporaryRoleSpec,
)
from agent_teams.roles.temporary_role_repository import TemporaryRoleRepository


class RuntimeRoleResolver:
    def __init__(
        self,
        *,
        role_registry: RoleRegistry,
        temporary_role_repository: TemporaryRoleRepository,
    ) -> None:
        self._role_registry = role_registry
        self._temporary_role_repository = temporary_role_repository

    def replace_role_registry(self, role_registry: RoleRegistry) -> None:
        self._role_registry = role_registry

    def get_effective_role(self, *, run_id: str | None, role_id: str) -> RoleDefinition:
        if run_id is not None:
            try:
                temp = self._temporary_role_repository.get(
                    run_id=run_id, role_id=role_id
                )
            except KeyError:
                pass
            else:
                return temp.role.to_role_definition()
        return self._role_registry.get(role_id)

    def list_effective_roles(self, *, run_id: str | None) -> tuple[RoleDefinition, ...]:
        static_roles = list(self._role_registry.list_roles())
        if run_id is None:
            return tuple(static_roles)
        temp_roles = [
            record.role.to_role_definition()
            for record in self._temporary_role_repository.list_by_run(run_id)
        ]
        return tuple(static_roles + temp_roles)

    def create_temporary_role(
        self,
        *,
        run_id: str,
        session_id: str,
        role: TemporaryRoleSpec,
        source: TemporaryRoleSource = TemporaryRoleSource.META_AGENT_GENERATED,
    ) -> RoleDefinition:
        if self._role_registry.is_coordinator_role(role.role_id):
            raise ValueError(
                f"Temporary role id conflicts with coordinator role: {role.role_id}"
            )
        if self._role_registry.is_main_agent_role(role.role_id):
            raise ValueError(
                f"Temporary role id conflicts with main agent role: {role.role_id}"
            )
        if role.template_role_id is not None:
            role = self._merge_with_template(run_id=run_id, role=role)
        record = self._temporary_role_repository.upsert(
            TemporaryRoleRecord(
                run_id=run_id,
                session_id=session_id,
                source=source,
                role=role,
            )
        )
        return record.role.to_role_definition()

    def cleanup_run(self, *, run_id: str) -> None:
        self._temporary_role_repository.delete_by_run(run_id)

    def _merge_with_template(
        self, *, run_id: str, role: TemporaryRoleSpec
    ) -> TemporaryRoleSpec:
        template_role_id = role.template_role_id
        if template_role_id is None:
            return role
        template = self.get_effective_role(run_id=run_id, role_id=template_role_id)
        return TemporaryRoleSpec(
            role_id=role.role_id,
            name=role.name,
            description=role.description,
            version=role.version,
            tools=template.tools if len(role.tools) == 0 else role.tools,
            mcp_servers=(
                template.mcp_servers if len(role.mcp_servers) == 0 else role.mcp_servers
            ),
            skills=template.skills if len(role.skills) == 0 else role.skills,
            model_profile=template.model_profile
            if role.model_profile == "default"
            else role.model_profile,
            bound_agent_id=role.bound_agent_id or template.bound_agent_id,
            execution_surface=role.execution_surface,
            memory_profile=role.memory_profile,
            system_prompt=role.system_prompt,
            template_role_id=role.template_role_id,
        )
