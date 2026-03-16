# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import re

import yaml

from agent_teams.builtin import get_builtin_skills_dir
from agent_teams.logger import get_logger
from agent_teams.paths import get_app_config_dir
from agent_teams.skills.skill_models import (
    Skill,
    SkillMetadata,
    SkillResource,
    SkillScope,
    SkillScript,
)
from agent_teams.trace import trace_span

logger = get_logger(__name__)
_SCRIPT_DESCRIPTION_PATTERN = re.compile(
    r"^- ([\w-]+):\s*(.*?)(?:\s*\((.*?)\))?$",
    re.MULTILINE,
)


def get_builtin_skills_dir_path() -> Path:
    return get_builtin_skills_dir()


def get_app_skills_dir(user_home_dir: Path | None = None) -> Path:
    return get_app_config_dir(user_home_dir=user_home_dir) / "skills"


def get_user_skills_dir(user_home_dir: Path | None = None) -> Path:
    return get_app_skills_dir(user_home_dir=user_home_dir)


def get_project_skills_dir(project_root: Path | None = None) -> Path:
    _ = project_root
    return get_app_skills_dir()


class SkillsDirectory:
    def __init__(
        self,
        base_dir: Path,
        max_depth: int = 3,
        fallback_dirs: tuple[Path, ...] = (),
    ) -> None:
        self.base_dir = _resolve_dir(base_dir)
        self.max_depth = max_depth
        self.fallback_dirs = tuple(_resolve_dir(item) for item in fallback_dirs)
        self._skills: dict[str, Skill] = {}

    @classmethod
    def from_skill_dirs(
        cls,
        *,
        app_skills_dir: Path,
        builtin_skills_dir: Path | None = None,
        max_depth: int = 3,
    ) -> SkillsDirectory:
        resolved_app_skills_dir = _resolve_dir(app_skills_dir)
        resolved_app_skills_dir.mkdir(parents=True, exist_ok=True)
        fallback_dirs = (
            (_resolve_dir(builtin_skills_dir),)
            if builtin_skills_dir is not None
            else ()
        )
        return cls(
            base_dir=resolved_app_skills_dir,
            max_depth=max_depth,
            fallback_dirs=fallback_dirs,
        )

    @classmethod
    def from_config_dirs(
        cls,
        *,
        app_config_dir: Path,
        max_depth: int = 3,
    ) -> SkillsDirectory:
        return cls.from_skill_dirs(
            app_skills_dir=_resolve_dir(app_config_dir) / "skills",
            builtin_skills_dir=get_builtin_skills_dir_path(),
            max_depth=max_depth,
        )

    @classmethod
    def from_default_scopes(
        cls,
        *,
        user_home_dir: Path | None = None,
        max_depth: int = 3,
    ) -> SkillsDirectory:
        return cls.from_skill_dirs(
            app_skills_dir=get_app_skills_dir(user_home_dir=user_home_dir),
            builtin_skills_dir=get_builtin_skills_dir_path(),
            max_depth=max_depth,
        )

    def discover(self) -> None:
        with trace_span(
            logger,
            component="skills.discovery",
            operation="discover",
            attributes={
                "base_dir": str(self.base_dir),
                "fallback_dirs": [str(path) for path in self.fallback_dirs],
                "max_depth": self.max_depth,
            },
        ):
            self._skills.clear()
            for scope, base_dir in self._iter_sources():
                if not base_dir.exists():
                    continue
                for path in sorted(base_dir.rglob("SKILL.md")):
                    try:
                        rel = path.relative_to(base_dir)
                        if len(rel.parts) > self.max_depth + 1:
                            continue
                        skill = self._load_skill(path=path, scope=scope)
                        if skill is not None:
                            self._skills[skill.metadata.name] = skill
                    except Exception as exc:
                        logger.warning("Failed to load skill at %s: %s", path, exc)

    def list_skills(self) -> list[Skill]:
        return list(self._skills.values())

    def get_skill(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def _iter_sources(self) -> tuple[tuple[SkillScope, Path], ...]:
        fallback_sources = tuple(
            (SkillScope.BUILTIN, base_dir) for base_dir in self.fallback_dirs
        )
        return (*fallback_sources, (SkillScope.APP, self.base_dir))

    def _split_front_matter(self, content: str) -> tuple[str, str]:
        if not content.startswith("---"):
            raise ValueError("SKILL.md must start with YAML front matter")

        lines = content.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            raise ValueError("SKILL.md must start with YAML front matter")

        end_index = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                end_index = idx
                break

        if end_index is None:
            raise ValueError("Invalid YAML front matter delimiters")

        front_matter = "".join(lines[1:end_index])
        body = "".join(lines[end_index + 1 :])
        return front_matter, body

    def _load_skill(self, *, path: Path, scope: SkillScope) -> Skill | None:
        with trace_span(
            logger,
            component="skills.discovery",
            operation="load_skill",
            attributes={"path": str(path), "scope": scope.value},
        ):
            raw = path.read_text(encoding="utf-8")
            try:
                front_matter, body = self._split_front_matter(raw)
                data = _as_object_mapping(yaml.safe_load(front_matter))
            except Exception as exc:
                logger.warning("Skipping %s due to parsing error: %s", path, exc)
                return None

            if data is None:
                return None

            raw_name = data.get("name")
            name = raw_name if isinstance(raw_name, str) else ""
            raw_description = data.get("description", "")
            description = raw_description if isinstance(raw_description, str) else ""
            if not name:
                return None

            resources: dict[str, SkillResource] = {}
            resource_entries = _as_object_mapping(data.get("resources"))
            if resource_entries is not None:
                for resource_name, raw_resource in resource_entries.items():
                    resource_data = _as_object_mapping(raw_resource)
                    if resource_data is None:
                        continue
                    resources[resource_name] = SkillResource(
                        name=resource_name,
                        description=_coerce_string(resource_data.get("description")),
                        path=_resolve_optional_path(
                            path.parent, resource_data.get("path")
                        ),
                    )

            for resource_dir_name in ["resources", "assets"]:
                resource_dir = path.parent / resource_dir_name
                if not resource_dir.exists() or not resource_dir.is_dir():
                    continue
                for resource_path in sorted(resource_dir.glob("*")):
                    if resource_path.is_file() and resource_path.name not in resources:
                        resources[resource_path.name] = SkillResource(
                            name=resource_path.name,
                            description=(
                                f"Auto-discovered resource: {resource_path.name}"
                            ),
                            path=resource_path,
                        )

            scripts: dict[str, SkillScript] = {}
            scripts_dir = path.parent / "scripts"
            script_meta: dict[str, tuple[str, str | None]] = {}
            for match in _SCRIPT_DESCRIPTION_PATTERN.finditer(body):
                script_name, script_description, script_path = match.groups()
                script_meta[script_name] = (script_description.strip(), script_path)

            if scripts_dir.exists() and scripts_dir.is_dir():
                for script_path in sorted(scripts_dir.glob("*.py")):
                    script_name = script_path.stem
                    description_text, _ = script_meta.get(
                        script_name, (f"Execute {script_name} script.", None)
                    )
                    scripts[script_name] = SkillScript(
                        name=script_name,
                        description=description_text,
                        path=script_path,
                    )
                    resource_name = f"scripts/{script_path.name}"
                    resources[resource_name] = SkillResource(
                        name=resource_name,
                        description=f"Script source: {script_name}",
                        path=script_path,
                    )

            metadata = SkillMetadata(
                name=name,
                description=description,
                instructions=body.strip(),
                resources=resources,
                scripts=scripts,
            )
            return Skill(metadata=metadata, directory=path.parent, scope=scope)


def _resolve_dir(path: Path) -> Path:
    return path.expanduser().resolve()


def _as_object_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in value.items()}


def _coerce_string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _resolve_optional_path(base_dir: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return base_dir / value
