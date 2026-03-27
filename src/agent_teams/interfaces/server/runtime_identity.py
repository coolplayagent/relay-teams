# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import sys

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.builtin import get_builtin_skills_dir
from agent_teams.paths import get_app_config_dir
from agent_teams.skills.skill_models import SkillScope
from agent_teams.skills.skill_registry import SkillRegistry

SERVER_VERSION = "0.1.0"
_DEEPRESEARCH_SKILL_REF = "builtin:deepresearch"


class ServerRuntimeIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    python_executable: str
    package_root: str
    config_dir: str
    builtin_skills_dir: str


class SkillRegistrySanity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    builtin_skill_count: int
    builtin_skill_refs: tuple[str, ...] = Field(default_factory=tuple)
    has_builtin_deepresearch: bool


class ServerHealthPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    version: str = SERVER_VERSION
    python_executable: str | None = None
    package_root: str | None = None
    config_dir: str | None = None
    builtin_skills_dir: str | None = None
    skill_registry_sanity: SkillRegistrySanity | None = None


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
    builtin_skills_dir = get_builtin_skills_dir().expanduser().resolve()
    return ServerRuntimeIdentity(
        python_executable=str(Path(sys.executable).expanduser().resolve()),
        package_root=str(package_root),
        config_dir=str(resolved_config_dir),
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


def build_server_health_payload(
    *,
    config_dir: Path | None = None,
    skill_registry: SkillRegistry | None = None,
) -> ServerHealthPayload:
    runtime_identity = build_server_runtime_identity(config_dir=config_dir)
    return ServerHealthPayload(
        status="ok",
        version=SERVER_VERSION,
        python_executable=runtime_identity.python_executable,
        package_root=runtime_identity.package_root,
        config_dir=runtime_identity.config_dir,
        builtin_skills_dir=runtime_identity.builtin_skills_dir,
        skill_registry_sanity=build_skill_registry_sanity(
            config_dir=config_dir,
            skill_registry=skill_registry,
        ),
    )
