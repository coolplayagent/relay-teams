# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import sys

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.builtin import get_builtin_roles_dir, get_builtin_skills_dir
from agent_teams.roles import RoleLoader, RoleRegistry
from agent_teams.roles.role_registry import (
    is_coordinator_role_definition,
    is_main_agent_role_definition,
)
from agent_teams.paths import get_app_config_dir
from agent_teams.skills.skill_models import SkillScope
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.tools.registry import (
    ToolAvailabilityRecord,
    ToolRegistry,
    build_default_registry,
)

SERVER_VERSION = "0.1.0"
_DEEPRESEARCH_SKILL_REF = "builtin:deepresearch"


class ServerRuntimeIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    python_executable: str
    package_root: str
    config_dir: str
    builtin_roles_dir: str
    builtin_skills_dir: str


class SkillRegistrySanity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    builtin_skill_count: int
    builtin_skill_refs: tuple[str, ...] = Field(default_factory=tuple)
    has_builtin_deepresearch: bool


class RoleRegistrySanity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    builtin_role_count: int
    builtin_role_ids: tuple[str, ...] = Field(default_factory=tuple)
    has_builtin_coordinator: bool
    has_builtin_main_agent: bool


class ToolRegistrySanity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available_tool_count: int
    available_tool_names: tuple[str, ...] = Field(default_factory=tuple)
    unavailable_tool_count: int = 0
    unavailable_tools: tuple[ToolAvailabilityRecord, ...] = Field(default_factory=tuple)
    has_write_tmp: bool


class ServerHealthPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    version: str = SERVER_VERSION
    python_executable: str | None = None
    package_root: str | None = None
    config_dir: str | None = None
    builtin_roles_dir: str | None = None
    builtin_skills_dir: str | None = None
    role_registry_sanity: RoleRegistrySanity | None = None
    skill_registry_sanity: SkillRegistrySanity | None = None
    tool_registry_sanity: ToolRegistrySanity | None = None


def build_server_runtime_identity(
    *,
    config_dir: Path | None = None,
) -> ServerRuntimeIdentity:
    resolved_config_dir = (
        get_app_config_dir()
        if config_dir is None
        else config_dir.expanduser().resolve()
    )
    package_root = Path(__file__).resolve().parents[2]
    builtin_roles_dir = get_builtin_roles_dir().expanduser().resolve()
    builtin_skills_dir = get_builtin_skills_dir().expanduser().resolve()
    return ServerRuntimeIdentity(
        python_executable=str(Path(sys.executable).expanduser().resolve()),
        package_root=str(package_root),
        config_dir=str(resolved_config_dir),
        builtin_roles_dir=str(builtin_roles_dir),
        builtin_skills_dir=str(builtin_skills_dir),
    )


def build_skill_registry_sanity(
    *,
    config_dir: Path | None = None,
    skill_registry: SkillRegistry | None = None,
) -> SkillRegistrySanity:
    resolved_config_dir = (
        get_app_config_dir()
        if config_dir is None
        else config_dir.expanduser().resolve()
    )
    registry = (
        skill_registry
        if skill_registry is not None
        else SkillRegistry.from_config_dirs(app_config_dir=resolved_config_dir)
    )
    builtin_skill_refs = tuple(
        skill.ref
        for skill in registry.list_skill_definitions()
        if skill.scope == SkillScope.BUILTIN
    )
    return SkillRegistrySanity(
        builtin_skill_count=len(builtin_skill_refs),
        builtin_skill_refs=builtin_skill_refs,
        has_builtin_deepresearch=_DEEPRESEARCH_SKILL_REF in builtin_skill_refs,
    )


def build_role_registry_sanity() -> RoleRegistrySanity:
    loader = RoleLoader()
    builtin_role_definitions = []
    for md_file in sorted(get_builtin_roles_dir().glob("*.md")):
        try:
            builtin_role_definitions.append(loader.load_one(md_file))
        except ValueError:
            continue
    builtin_role_ids = tuple(sorted(role.role_id for role in builtin_role_definitions))
    return RoleRegistrySanity(
        builtin_role_count=len(builtin_role_definitions),
        builtin_role_ids=builtin_role_ids,
        has_builtin_coordinator=any(
            is_coordinator_role_definition(role) for role in builtin_role_definitions
        ),
        has_builtin_main_agent=any(
            is_main_agent_role_definition(role) for role in builtin_role_definitions
        ),
    )


def build_tool_registry_sanity(
    *,
    tool_registry: ToolRegistry | None = None,
) -> ToolRegistrySanity:
    registry = tool_registry if tool_registry is not None else build_default_registry()
    available_tool_names = registry.list_names()
    unavailable_tools = registry.list_unavailable_tools()
    return ToolRegistrySanity(
        available_tool_count=len(available_tool_names),
        available_tool_names=available_tool_names,
        unavailable_tool_count=len(unavailable_tools),
        unavailable_tools=unavailable_tools,
        has_write_tmp="write_tmp" in available_tool_names,
    )


def build_server_health_payload(
    *,
    config_dir: Path | None = None,
    role_registry: RoleRegistry | None = None,
    skill_registry: SkillRegistry | None = None,
    tool_registry: ToolRegistry | None = None,
) -> ServerHealthPayload:
    runtime_identity = build_server_runtime_identity(config_dir=config_dir)
    _ = role_registry
    return ServerHealthPayload(
        status="ok",
        version=SERVER_VERSION,
        python_executable=runtime_identity.python_executable,
        package_root=runtime_identity.package_root,
        config_dir=runtime_identity.config_dir,
        builtin_roles_dir=runtime_identity.builtin_roles_dir,
        builtin_skills_dir=runtime_identity.builtin_skills_dir,
        role_registry_sanity=build_role_registry_sanity(),
        skill_registry_sanity=build_skill_registry_sanity(
            config_dir=config_dir,
            skill_registry=skill_registry,
        ),
        tool_registry_sanity=build_tool_registry_sanity(tool_registry=tool_registry),
    )


def health_has_runtime_identity(health: ServerHealthPayload) -> bool:
    return (
        isinstance(health.python_executable, str)
        and bool(health.python_executable.strip())
        and isinstance(health.package_root, str)
        and bool(health.package_root.strip())
        and isinstance(health.builtin_roles_dir, str)
        and bool(health.builtin_roles_dir.strip())
    )


def runtime_identity_matches(
    *,
    health: ServerHealthPayload,
    current: ServerRuntimeIdentity,
) -> bool:
    return (
        health.python_executable == current.python_executable
        and health.package_root == current.package_root
        and health.builtin_roles_dir == current.builtin_roles_dir
    )


def raise_if_runtime_mismatch(
    *,
    health: ServerHealthPayload,
    current: ServerRuntimeIdentity,
    display_url: str,
) -> None:
    if not health_has_runtime_identity(health):
        raise RuntimeError(
            f"Agent Teams server is already responding at {display_url}, "
            "but it does not expose runtime identity metadata. "
            "Stop the conflicting server first, then retry."
        )
    if runtime_identity_matches(health=health, current=current):
        return
    raise RuntimeError(
        "Agent Teams server runtime mismatch at "
        f"{display_url}. Current CLI runtime uses "
        f"{current.python_executable} from {current.package_root} "
        f"with builtin roles at {current.builtin_roles_dir}, "
        "but the live server uses "
        f"{health.python_executable} from {health.package_root} "
        f"with builtin roles at {health.builtin_roles_dir}. "
        "Stop the conflicting server first, then retry."
    )
