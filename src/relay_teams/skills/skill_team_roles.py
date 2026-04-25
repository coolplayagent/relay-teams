# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from relay_teams.logger import get_logger, log_event
from relay_teams.roles.role_models import RoleDefinition, RoleMode
from relay_teams.roles.role_registry import RoleLoader
from relay_teams.roles.temporary_role_models import TemporaryRoleSpec
from relay_teams.skills.skill_models import Skill

LOGGER = get_logger(__name__)
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
        except Exception as exc:
            _log_invalid_skill_role(skill=skill, role_path=role_path, error=exc)
            continue
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
    role_spec = build_skill_team_role_spec(skill=skill, role=role)
    effective_role = role_spec.to_role_definition()
    source_path = (
        _relative_skill_path(skill.directory, role.source_path)
        if role.source_path is not None
        else "."
    )
    return SkillTeamRoleSummary(
        role_id=role.role_id,
        effective_role_id=effective_role.role_id,
        name=effective_role.name,
        description=effective_role.description,
        tools=effective_role.tools,
        mcp_servers=effective_role.mcp_servers,
        skills=effective_role.skills,
        model_profile=effective_role.model_profile,
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
    role_entries = list_skill_team_roles(skill)
    if role_entries:
        lines.append(f"- Inferred skill team roles: {len(role_entries)}")
    for entry in role_entries:
        summary = entry.summary
        lines.append(
            "- Role "
            f"{summary.role_id}: {summary.name} - {summary.description} "
            f"({summary.source_path})"
        )
    return tuple(lines)


def _iter_skill_role_files(skill_dir: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(skill_dir.rglob("*.md"))
        if path.name.casefold() != "skill.md" and _looks_like_role_file(path)
    )


def _looks_like_role_file(path: Path) -> bool:
    try:
        front_matter = _read_markdown_front_matter(path)
        parsed = yaml.safe_load(front_matter)
    except (OSError, ValueError, yaml.YAMLError):
        return False
    return isinstance(parsed, dict) and "role_id" in parsed


def _read_markdown_front_matter(path: Path) -> str:
    content = path.read_text(encoding="utf-8").lstrip("\ufeff")
    if not content.startswith("---"):
        raise ValueError("Markdown front matter is missing")
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("Markdown front matter is missing")
    end_index: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_index = idx
            break
    if end_index is None:
        raise ValueError("Markdown front matter is incomplete")
    return "".join(lines[1:end_index])


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
