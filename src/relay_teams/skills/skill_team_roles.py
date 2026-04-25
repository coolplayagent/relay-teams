# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.logger import get_logger, log_event
from relay_teams.roles.role_models import RoleDefinition, RoleMode
from relay_teams.roles.role_registry import RoleLoader
from relay_teams.roles.temporary_role_models import TemporaryRoleSpec
from relay_teams.skills.skill_models import Skill

LOGGER = get_logger(__name__)
_ROLE_DIRECTORY_NAMES = ("agents", "roles")
_TEAM_SIGNAL_FILENAMES = (
    "workflow.md",
    "bind.md",
    "dependencies.yaml",
    "dependencies.yml",
)
_MAX_IDENTIFIER_PART_LENGTH = 48


class SkillTeamRoleSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    effective_role_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    model_profile: str = Field(min_length=1)
    source_path: str = Field(min_length=1)


class SkillTeamRoleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: SkillTeamRoleSummary
    role: RoleDefinition


def list_skill_team_roles(skill: Skill) -> tuple[SkillTeamRoleDefinition, ...]:
    roles_by_id: dict[str, SkillTeamRoleDefinition] = {}
    for role_path in _iter_skill_role_files(skill.directory):
        try:
            role = RoleLoader().load_one(role_path)
        except Exception as exc:
            _log_invalid_skill_role(skill=skill, role_path=role_path, error=exc)
            continue
        if role.role_id in roles_by_id:
            log_event(
                LOGGER,
                logging.WARNING,
                event="skills.team_role.duplicate_ignored",
                message="Ignoring duplicate skill-local role id",
                payload={
                    "skill_name": skill.metadata.name,
                    "role_id": role.role_id,
                    "source_path": _relative_skill_path(skill.directory, role_path),
                },
            )
            continue
        roles_by_id[role.role_id] = SkillTeamRoleDefinition(
            summary=summarize_skill_team_role(skill=skill, role=role),
            role=role,
        )
    return tuple(
        sorted(
            roles_by_id.values(),
            key=lambda item: (item.summary.name, item.summary.role_id),
        )
    )


def summarize_skill_team_role(
    *,
    skill: Skill,
    role: RoleDefinition,
) -> SkillTeamRoleSummary:
    source_path = (
        _relative_skill_path(skill.directory, role.source_path)
        if role.source_path is not None
        else "."
    )
    return SkillTeamRoleSummary(
        role_id=role.role_id,
        effective_role_id=build_skill_team_effective_role_id(
            skill_name=skill.metadata.name,
            role_id=role.role_id,
        ),
        name=role.name,
        description=role.description,
        tools=role.tools,
        mcp_servers=role.mcp_servers,
        skills=role.skills,
        model_profile=role.model_profile,
        source_path=source_path,
    )


def build_skill_team_role_spec(
    *,
    skill: Skill,
    role: RoleDefinition,
) -> TemporaryRoleSpec:
    return TemporaryRoleSpec(
        role_id=build_skill_team_effective_role_id(
            skill_name=skill.metadata.name,
            role_id=role.role_id,
        ),
        name=role.name,
        description=role.description,
        version=role.version,
        tools=role.tools,
        mcp_servers=role.mcp_servers,
        skills=role.skills,
        model_profile=role.model_profile,
        bound_agent_id=role.bound_agent_id,
        execution_surface=role.execution_surface,
        mode=RoleMode.SUBAGENT,
        memory_profile=role.memory_profile,
        system_prompt=role.system_prompt,
    )


def build_skill_team_effective_role_id(*, skill_name: str, role_id: str) -> str:
    skill_part = _identifier_part(skill_name, fallback="skill")
    role_part = _identifier_part(role_id, fallback="role")
    digest = hashlib.sha256(
        f"{skill_name.strip()}:{role_id.strip()}".encode("utf-8")
    ).hexdigest()[:8]
    return f"skill_team_{skill_part}_{role_part}_{digest}"


def build_skill_team_routing_signals(skill: Skill) -> tuple[str, ...]:
    lines: list[str] = []
    for filename in _TEAM_SIGNAL_FILENAMES:
        if (skill.directory / filename).is_file():
            lines.append(f"- Team file: {filename}")
    for directory_name in _ROLE_DIRECTORY_NAMES:
        directory = skill.directory / directory_name
        if directory.is_dir():
            lines.append(f"- Team role directory: {directory_name}")
    for entry in list_skill_team_roles(skill):
        summary = entry.summary
        lines.append(
            "- Role "
            f"{summary.role_id}: {summary.name} - {summary.description} "
            f"({summary.source_path})"
        )
    return tuple(lines)


def _iter_skill_role_files(skill_dir: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for directory_name in _ROLE_DIRECTORY_NAMES:
        directory = skill_dir / directory_name
        if not directory.is_dir():
            continue
        files.extend(sorted(directory.glob("*.md")))
    return tuple(files)


def _relative_skill_path(skill_dir: Path, path: Path) -> str:
    try:
        relative_path = path.resolve().relative_to(skill_dir.resolve())
    except ValueError:
        return path.resolve().as_posix()
    return relative_path.as_posix()


def _identifier_part(value: str, *, fallback: str) -> str:
    chars: list[str] = []
    for char in value.strip():
        if char.isascii() and char.isalnum():
            chars.append(char.lower())
        elif char in {"-", "_"}:
            chars.append("_")
    result = "".join(chars).strip("_")
    if not result:
        result = fallback
    return result[:_MAX_IDENTIFIER_PART_LENGTH]


def _log_invalid_skill_role(
    *,
    skill: Skill,
    role_path: Path,
    error: Exception,
) -> None:
    log_event(
        LOGGER,
        logging.WARNING,
        event="skills.team_role.invalid_ignored",
        message="Ignoring invalid skill-local role file",
        payload={
            "skill_name": skill.metadata.name,
            "source_path": _relative_skill_path(skill.directory, role_path),
            "error": str(error),
        },
    )
