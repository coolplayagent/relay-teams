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
    RoleToolGroupOption,
    RoleValidationResult,
)
from relay_teams.roles.memory_models import (
    MemoryProfile,
    default_memory_profile,
)
from relay_teams.roles.role_contracts import (
    RoleContract,
    RoleContractInvariant,
    RoleContractInvariantType,
    RoleContractPostcondition,
    RoleContractPostconditionType,
    RoleContractPrecondition,
    RoleContractPreconditionType,
)
from relay_teams.roles.role_registry import RoleLoader, RoleRegistry
from relay_teams.roles.runtime_tools import (
    role_with_runtime_tools,
    runtime_denied_tools_for_role,
    runtime_tools_for_role,
    strip_coordinator_only_tools,
    strip_contract_denied_tools,
)
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
    "RoleContract",
    "RoleContractInvariant",
    "RoleContractInvariantType",
    "RoleContractPostcondition",
    "RoleContractPostconditionType",
    "RoleContractPrecondition",
    "RoleContractPreconditionType",
    "RoleConfigOptions",
    "RoleConfigSource",
    "RoleDefinition",
    "RoleDocumentDraft",
    "RoleDocumentRecord",
    "RoleDocumentSummary",
    "RoleMode",
    "RoleLoader",
    "RoleRegistry",
    "SystemRolesUnavailableError",
    "RoleSkillOption",
    "RoleToolGroupOption",
    "RoleValidationResult",
    "RuntimeRoleResolver",
    "role_with_runtime_tools",
    "runtime_denied_tools_for_role",
    "runtime_tools_for_role",
    "strip_coordinator_only_tools",
    "strip_contract_denied_tools",
    "ensure_required_system_roles",
    "TemporaryRoleRecord",
    "TemporaryRoleRepository",
    "TemporaryRoleSource",
    "TemporaryRoleSpec",
]
