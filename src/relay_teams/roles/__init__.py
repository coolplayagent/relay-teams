from __future__ import annotations

from relay_teams.computer import ExecutionSurface
from relay_teams.roles.role_models import (
    NormalModeRoleOption,
    RoleAgentOption,
    RoleConfigOptions,
    RoleConfigSource,
    RoleDefinition,
    RoleDocumentDraft,
    RoleDocumentRecord,
    RoleDocumentSummary,
    RoleMode,
    RoleSkillOption,
    RoleValidationResult,
)
from relay_teams.roles.memory_models import (
    MemoryProfile,
    RoleMemoryRecord,
    default_memory_profile,
)
from relay_teams.roles.memory_repository import RoleMemoryRepository
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.roles.role_registry import RoleLoader, RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.roles.role_registry import (
    SystemRolesUnavailableError,
    ensure_required_system_roles,
)
from relay_teams.roles.temporary_role_models import (
    TemporaryRoleRecord,
    TemporaryRoleSource,
    TemporaryRoleSpec,
)
from relay_teams.roles.temporary_role_repository import TemporaryRoleRepository

__all__ = [
    "default_memory_profile",
    "ExecutionSurface",
    "MemoryProfile",
    "NormalModeRoleOption",
    "RoleAgentOption",
    "RoleConfigOptions",
    "RoleConfigSource",
    "RoleDefinition",
    "RoleDocumentDraft",
    "RoleDocumentRecord",
    "RoleDocumentSummary",
    "RoleMode",
    "RoleLoader",
    "RoleMemoryRecord",
    "RoleMemoryRepository",
    "RoleMemoryService",
    "RoleRegistry",
    "SystemRolesUnavailableError",
    "RoleSkillOption",
    "RoleValidationResult",
    "RuntimeRoleResolver",
    "ensure_required_system_roles",
    "TemporaryRoleRecord",
    "TemporaryRoleRepository",
    "TemporaryRoleSource",
    "TemporaryRoleSpec",
]
