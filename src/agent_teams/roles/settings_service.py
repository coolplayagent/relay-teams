# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.roles.role_models import (
    RoleConfigSource,
    RoleDefinition,
    RoleDocumentDraft,
    RoleDocumentRecord,
    RoleDocumentSummary,
    RoleValidationResult,
)
from agent_teams.roles.role_registry import RoleLoader, RoleRegistry
from agent_teams.roles.role_registry import (
    COORDINATOR_REQUIRED_TOOLS,
    ensure_required_system_roles,
    is_coordinator_role_definition,
    is_reserved_system_role_definition,
)
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.tools.registry import ToolRegistry
from agent_teams.roles.memory_models import default_memory_profile

if TYPE_CHECKING:
    from agent_teams.external_agents import ExternalAgentConfigService


class RoleSettingsService:
    def __init__(
        self,
        *,
        roles_dir: Path,
        builtin_roles_dir: Path,
        get_tool_registry: Callable[[], ToolRegistry],
        get_mcp_registry: Callable[[], McpRegistry],
        get_skill_registry: Callable[[], SkillRegistry],
        get_external_agent_service: Callable[[], ExternalAgentConfigService] | None,
        on_roles_reloaded: Callable[[RoleRegistry], None],
    ) -> None:
        self._roles_dir: Path = roles_dir
        self._builtin_roles_dir: Path = builtin_roles_dir
        self._loader: RoleLoader = RoleLoader()
        self._get_tool_registry: Callable[[], ToolRegistry] = get_tool_registry
        self._get_mcp_registry: Callable[[], McpRegistry] = get_mcp_registry
        self._get_skill_registry: Callable[[], SkillRegistry] = get_skill_registry
        self._get_external_agent_service = get_external_agent_service
        self._on_roles_reloaded: Callable[[RoleRegistry], None] = on_roles_reloaded

    def list_role_documents(self) -> tuple[RoleDocumentSummary, ...]:
        builtin_role_ids = self._load_builtin_role_ids()
        documents: list[RoleDocumentSummary] = []
        for role_id, (role_path, source) in self._loader.build_effective_role_map(
            builtin_roles_dir=self._builtin_roles_dir,
            app_roles_dir=self._roles_dir,
        ).items():
            definition = self._validate_definition(
                self._loader.load_one(role_path),
                strict_capability_validation=False,
                consumer=f"roles.settings_service.list_role_documents.role:{role_id}",
            )
            documents.append(
                self._summary_from_definition(
                    definition,
                    source=source,
                    builtin_role_ids=builtin_role_ids,
                )
            )
        return tuple(documents)

    def get_role_document(self, role_id: str) -> RoleDocumentRecord:
        role_path, source = self._find_role_record(role_id)
        content = role_path.read_text(encoding="utf-8")
        role = self._canonicalize_role_skills_for_record(
            self._loader.load_from_text(content, source_name=role_path.name),
            consumer=f"roles.settings_service.get_role_document.role:{role_id}",
        )
        return self._record_from_definition(
            definition=role,
            file_name=role_path.name,
            content=content,
            source=source,
        )

    def validate_role_document(
        self,
        draft: RoleDocumentDraft,
    ) -> RoleValidationResult:
        normalized = self._normalize_draft(draft)
        content = self._serialize_role_document(normalized)
        role = self._loader.load_from_text(
            content,
            source_name=f"{normalized.role_id}.md",
        )
        validated_role = self._validate_definition(
            role,
            strict_capability_validation=True,
            consumer=f"roles.settings_service.validate_role_document.role:{normalized.role_id}",
        )
        return RoleValidationResult(
            valid=True,
            role=self._record_from_definition(
                definition=validated_role,
                file_name=f"{normalized.role_id}.md",
                content=content,
                source_role_id=normalized.source_role_id,
                source=RoleConfigSource.APP,
            ),
        )

    def save_role_document(
        self,
        role_id: str,
        draft: RoleDocumentDraft,
    ) -> RoleDocumentRecord:
        normalized = self._normalize_draft(draft)
        if normalized.role_id != role_id:
            raise ValueError("Path role_id must match payload role_id")

        source_role_id = normalized.source_role_id or role_id
        source_record = self._find_role_record_optional(source_role_id)
        source_path = None if source_record is None else source_record[0]
        if source_path is not None:
            source_definition = self._loader.load_one(source_path)
            self._validate_reserved_role_mutation(
                source_definition=source_definition,
                draft=normalized,
            )
        validated = self.validate_role_document(normalized).role
        target_path = self._roles_dir / f"{normalized.role_id}.md"
        if source_record is None and normalized.source_role_id:
            raise ValueError(f"Role not found: {source_role_id}")
        if target_path.exists() and (
            source_path is None or target_path.resolve() != source_path.resolve()
        ):
            raise ValueError(f"Role file already exists: {target_path.name}")

        self._roles_dir.mkdir(parents=True, exist_ok=True)
        target_path.write_text(validated.content, encoding="utf-8")
        if (
            source_path is not None
            and source_record is not None
            and source_record[1] == RoleConfigSource.APP
            and target_path != source_path
            and source_path.exists()
        ):
            source_path.unlink()

        registry = self._load_registry(
            strict_capability_validation=False,
            consumer_prefix="roles.settings_service.save_role_document",
        )
        self._on_roles_reloaded(registry)
        return self.get_role_document(normalized.role_id)

    def delete_role_document(self, role_id: str) -> None:
        normalized_role_id = role_id.strip()
        if not normalized_role_id:
            raise ValueError("Role not found: ")
        builtin_role_ids = self._load_builtin_role_ids()
        role_path, source = self._find_role_record(normalized_role_id)
        if not self._is_role_deletable(
            role_id=normalized_role_id,
            source=source,
            builtin_role_ids=builtin_role_ids,
        ):
            raise ValueError(f"Role cannot be deleted: {normalized_role_id}")
        role_path.unlink()
        registry = self._load_registry(
            strict_capability_validation=False,
            consumer_prefix="roles.settings_service.delete_role_document",
        )
        self._on_roles_reloaded(registry)

    def validate_all_roles(self) -> dict[str, int | bool]:
        registry = self._load_registry(
            strict_capability_validation=True,
            consumer_prefix="roles.settings_service.validate_all_roles",
        )
        ensure_required_system_roles(registry)
        return {
            "valid": True,
            "loaded_count": len(registry.list_roles()),
        }

    def _summary_from_definition(
        self,
        definition: RoleDefinition,
        *,
        source: RoleConfigSource,
        builtin_role_ids: frozenset[str],
    ) -> RoleDocumentSummary:
        return RoleDocumentSummary(
            role_id=definition.role_id,
            name=definition.name,
            description=definition.description,
            version=definition.version,
            model_profile=definition.model_profile,
            bound_agent_id=definition.bound_agent_id,
            execution_surface=definition.execution_surface,
            source=source,
            deletable=self._is_role_deletable(
                role_id=definition.role_id,
                source=source,
                builtin_role_ids=builtin_role_ids,
            ),
        )

    def _record_from_definition(
        self,
        *,
        definition: RoleDefinition,
        file_name: str,
        content: str,
        source_role_id: str | None = None,
        source: RoleConfigSource = RoleConfigSource.APP,
    ) -> RoleDocumentRecord:
        return RoleDocumentRecord(
            source_role_id=source_role_id,
            role_id=definition.role_id,
            name=definition.name,
            description=definition.description,
            version=definition.version,
            tools=definition.tools,
            mcp_servers=definition.mcp_servers,
            skills=definition.skills,
            model_profile=definition.model_profile,
            bound_agent_id=definition.bound_agent_id,
            execution_surface=definition.execution_surface,
            memory_profile=definition.memory_profile,
            system_prompt=definition.system_prompt,
            source=source,
            file_name=file_name,
            content=content,
        )

    def _normalize_draft(self, draft: RoleDocumentDraft) -> RoleDocumentDraft:
        return draft.model_copy(
            update={
                "role_id": draft.role_id.strip(),
                "name": draft.name.strip(),
                "description": draft.description.strip(),
                "version": draft.version.strip(),
                "model_profile": draft.model_profile.strip(),
                "bound_agent_id": _normalize_optional_text(draft.bound_agent_id),
                "system_prompt": draft.system_prompt.strip(),
                "tools": tuple(item.strip() for item in draft.tools if item.strip()),
                "mcp_servers": tuple(
                    item.strip() for item in draft.mcp_servers if item.strip()
                ),
                "skills": tuple(item.strip() for item in draft.skills if item.strip()),
            }
        )

    def _serialize_role_document(self, draft: RoleDocumentDraft) -> str:
        front_matter: dict[str, object] = {
            "role_id": draft.role_id,
            "name": draft.name,
            "description": draft.description,
            "model_profile": draft.model_profile,
            "version": draft.version,
            "tools": list(draft.tools),
            "execution_surface": draft.execution_surface.value,
        }
        if draft.bound_agent_id:
            front_matter["bound_agent_id"] = draft.bound_agent_id
        if draft.mcp_servers:
            front_matter["mcp_servers"] = list(draft.mcp_servers)
        if draft.skills:
            front_matter["skills"] = list(draft.skills)
        if draft.memory_profile != default_memory_profile():
            front_matter["memory_profile"] = draft.memory_profile.model_dump(
                mode="json"
            )
        serialized_front_matter = yaml.safe_dump(
            front_matter,
            sort_keys=False,
            allow_unicode=False,
        ).strip()
        return f"---\n{serialized_front_matter}\n---\n\n{draft.system_prompt.strip()}\n"

    def _load_registry(
        self,
        *,
        strict_capability_validation: bool,
        consumer_prefix: str,
    ) -> RoleRegistry:
        registry = self._loader.load_builtin_and_app(
            builtin_roles_dir=self._builtin_roles_dir,
            app_roles_dir=self._roles_dir,
            allow_empty=True,
        )
        sanitized_registry = RoleRegistry()
        for definition in registry.list_roles():
            consumer = f"{consumer_prefix}.role:{definition.role_id}"
            validated_definition = self._validate_definition(
                definition,
                strict_capability_validation=strict_capability_validation,
                consumer=consumer,
            )
            sanitized_registry.register(validated_definition)
        return sanitized_registry

    def _validate_definition(
        self,
        definition: RoleDefinition,
        *,
        strict_capability_validation: bool,
        consumer: str,
    ) -> RoleDefinition:
        if strict_capability_validation:
            tools = definition.tools
            mcp_servers = definition.mcp_servers
            self._get_tool_registry().validate_known(tools)
            self._get_mcp_registry().validate_known(mcp_servers)
            skills = self._get_skill_registry().resolve_known(
                definition.skills,
                strict=True,
                consumer=consumer,
            )
            definition = definition.model_copy(
                update={
                    "tools": tools,
                    "mcp_servers": mcp_servers,
                    "skills": skills,
                }
            )
        else:
            tools = self._get_tool_registry().resolve_known(
                definition.tools,
                strict=False,
                consumer=consumer,
            )
            mcp_servers = self._get_mcp_registry().resolve_server_names(
                definition.mcp_servers,
                strict=False,
                consumer=consumer,
            )
            skills = self._get_skill_registry().resolve_known(
                definition.skills,
                strict=False,
                consumer=consumer,
            )
            definition = definition.model_copy(
                update={
                    "tools": tools,
                    "mcp_servers": mcp_servers,
                    "skills": skills,
                }
            )
        if definition.bound_agent_id:
            if self._get_external_agent_service is None:
                raise ValueError(
                    "External agent bindings are not available in this runtime"
                )
            try:
                self._get_external_agent_service().get_agent(definition.bound_agent_id)
            except KeyError as exc:
                raise ValueError(
                    f"Unknown external agent binding: {definition.bound_agent_id}"
                ) from exc
        if is_reserved_system_role_definition(definition):
            missing_tools = COORDINATOR_REQUIRED_TOOLS.difference(definition.tools)
            if missing_tools and is_coordinator_role_definition(definition):
                missing = ", ".join(sorted(missing_tools))
                raise ValueError(
                    f"Coordinator role must keep required tools: {missing}"
                )
        return definition

    def _canonicalize_role_skills_for_record(
        self,
        definition: RoleDefinition,
        *,
        consumer: str,
    ) -> RoleDefinition:
        normalized_skills: list[str] = []
        for skill_name in definition.skills:
            normalized_skill_name = skill_name.strip()
            if not normalized_skill_name:
                continue
            resolved = self._get_skill_registry().resolve_known(
                (normalized_skill_name,),
                strict=False,
                consumer=consumer,
            )
            if resolved:
                normalized_skills.append(resolved[0])
            else:
                normalized_skills.append(normalized_skill_name)
        return definition.model_copy(update={"skills": tuple(normalized_skills)})

    def _validate_reserved_role_mutation(
        self,
        *,
        source_definition: RoleDefinition,
        draft: RoleDocumentDraft,
    ) -> None:
        if not is_reserved_system_role_definition(source_definition):
            return
        locked_pairs = (
            ("role_id", source_definition.role_id, draft.role_id),
            ("name", source_definition.name, draft.name),
            ("description", source_definition.description, draft.description),
            ("version", source_definition.version, draft.version),
        )
        for field_name, source_value, next_value in locked_pairs:
            if str(source_value) != str(next_value):
                raise ValueError(
                    f"{field_name} is locked for reserved system role {source_definition.role_id}"
                )

    def _find_role_record(self, role_id: str) -> tuple[Path, RoleConfigSource]:
        role_record = self._find_role_record_optional(role_id)
        if role_record is None:
            raise ValueError(f"Role not found: {role_id}")
        return role_record

    def _find_role_record_optional(
        self,
        role_id: str,
    ) -> tuple[Path, RoleConfigSource] | None:
        return self._loader.build_effective_role_map(
            builtin_roles_dir=self._builtin_roles_dir,
            app_roles_dir=self._roles_dir,
        ).get(role_id)

    def _resolve_role_source(self, role_id: str) -> RoleConfigSource:
        return self._load_role_sources().get(role_id, RoleConfigSource.APP)

    def _load_role_sources(self) -> dict[str, RoleConfigSource]:
        return {
            role_id: source
            for role_id, (_, source) in self._loader.build_effective_role_map(
                builtin_roles_dir=self._builtin_roles_dir,
                app_roles_dir=self._roles_dir,
            ).items()
        }

    def _load_builtin_role_ids(self) -> frozenset[str]:
        return frozenset(
            self._loader.load_one(md_file).role_id
            for md_file in sorted(self._builtin_roles_dir.glob("*.md"))
        )

    def _is_role_deletable(
        self,
        *,
        role_id: str,
        source: RoleConfigSource,
        builtin_role_ids: frozenset[str],
    ) -> bool:
        return source == RoleConfigSource.APP and role_id not in builtin_role_ids


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized
