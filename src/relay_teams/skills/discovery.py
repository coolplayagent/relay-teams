# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import re
from threading import RLock

import yaml

from relay_teams.builtin import get_builtin_skills_dir
from relay_teams.hooks import parse_tolerant_hooks_payload
from relay_teams.hooks.hook_models import HooksConfig
from relay_teams.logger import get_logger
from relay_teams.paths import get_app_config_dir, get_project_root_or_none
from relay_teams.skills.skill_models import (
    Skill,
    SkillMetadata,
    SkillResource,
    SkillScript,
    SkillSource,
)
from relay_teams.trace import trace_span

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


def get_agents_skills_dir(user_home_dir: Path | None = None) -> Path:
    app_config_dir = get_app_config_dir(user_home_dir=user_home_dir)
    return app_config_dir.parent / ".agents" / "skills"


def get_project_skills_dir(project_root: Path | None = None) -> Path:
    resolved_root = _resolve_start_dir(project_root)
    return resolved_root / ".relay-teams" / "skills"


class SkillsDirectory:
    def __init__(
        self,
        *,
        sources: tuple[tuple[SkillSource, Path], ...],
        max_depth: int = 3,
    ) -> None:
        self.max_depth = max_depth
        self.sources = tuple(
            (source, _resolve_dir(base_dir)) for source, base_dir in sources
        )
        self._skills: dict[str, Skill] = {}
        self._lock = RLock()

    @classmethod
    def from_skill_dirs(
        cls,
        *,
        app_skills_dir: Path,
        builtin_skills_dir: Path | None = None,
        max_depth: int = 3,
    ) -> SkillsDirectory:
        sources: list[tuple[SkillSource, Path]] = []
        if builtin_skills_dir is not None:
            sources.append((SkillSource.BUILTIN, _resolve_dir(builtin_skills_dir)))
        sources.append((SkillSource.USER_RELAY_TEAMS, _resolve_dir(app_skills_dir)))
        return cls(sources=tuple(sources), max_depth=max_depth)

    @classmethod
    def from_config_dirs(
        cls,
        *,
        app_config_dir: Path,
        max_depth: int = 3,
        project_start_dir: Path | None = None,
    ) -> SkillsDirectory:
        resolved_app_config_dir = _resolve_dir(app_config_dir)
        return cls(
            sources=_build_default_sources(
                builtin_skills_dir=get_builtin_skills_dir_path(),
                relay_teams_skills_dir=resolved_app_config_dir / "skills",
                agents_skills_dir=resolved_app_config_dir.parent / ".agents" / "skills",
                project_start_dir=project_start_dir,
            ),
            max_depth=max_depth,
        )

    @classmethod
    def from_default_scopes(
        cls,
        *,
        user_home_dir: Path | None = None,
        max_depth: int = 3,
        start_dir: Path | None = None,
    ) -> SkillsDirectory:
        return cls(
            sources=_build_default_sources(
                builtin_skills_dir=get_builtin_skills_dir_path(),
                relay_teams_skills_dir=get_app_skills_dir(user_home_dir=user_home_dir),
                agents_skills_dir=get_agents_skills_dir(user_home_dir=user_home_dir),
                project_start_dir=start_dir,
            ),
            max_depth=max_depth,
        )

    def discover(self) -> None:
        with trace_span(
            logger,
            component="skills.discovery",
            operation="discover",
            attributes={
                "sources": [
                    {"source": source.value, "base_dir": str(path)}
                    for source, path in self.sources
                ],
                "max_depth": self.max_depth,
            },
        ):
            discovered_skills: dict[str, Skill] = {}
            for source, base_dir in self.sources:
                if not base_dir.exists():
                    continue
                for path in sorted(base_dir.rglob("SKILL.md")):
                    try:
                        rel = path.relative_to(base_dir)
                        if len(rel.parts) > self.max_depth + 1:
                            continue
                        skill = self._load_skill(path=path, source=source)
                        if skill is None:
                            continue
                        existing_skill = discovered_skills.get(skill.metadata.name)
                        if existing_skill is not None:
                            logger.warning(
                                "Overriding duplicate skill %s from %s (%s) with %s (%s)",
                                skill.metadata.name,
                                existing_skill.directory,
                                existing_skill.source.value,
                                skill.directory,
                                skill.source.value,
                            )
                        discovered_skills[skill.metadata.name] = skill
                    except Exception as exc:
                        logger.warning("Failed to load skill at %s: %s", path, exc)
            with self._lock:
                self._skills = discovered_skills

    def list_skills(self) -> list[Skill]:
        with self._lock:
            return list(self._skills.values())

    def get_skill(self, name: str) -> Skill | None:
        with self._lock:
            return self._skills.get(name.strip())

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

    def _load_skill(self, *, path: Path, source: SkillSource) -> Skill | None:
        with trace_span(
            logger,
            component="skills.discovery",
            operation="load_skill",
            attributes={"path": str(path), "source": source.value},
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
                hooks=_parse_frontmatter_hooks(data.get("hooks")),
            )
            return Skill(
                ref=name,
                metadata=metadata,
                directory=path.parent,
                source=source,
            )


def _build_default_sources(
    *,
    builtin_skills_dir: Path,
    relay_teams_skills_dir: Path,
    agents_skills_dir: Path,
    project_start_dir: Path | None,
) -> tuple[tuple[SkillSource, Path], ...]:
    sources: list[tuple[SkillSource, Path]] = [
        (SkillSource.BUILTIN, _resolve_dir(builtin_skills_dir)),
        (SkillSource.USER_RELAY_TEAMS, _resolve_dir(relay_teams_skills_dir)),
        (SkillSource.USER_AGENTS, _resolve_dir(agents_skills_dir)),
    ]
    if project_start_dir is not None:
        sources.extend(_project_skill_sources(start_dir=project_start_dir))
    return tuple(sources)


def _project_skill_sources(*, start_dir: Path) -> tuple[tuple[SkillSource, Path], ...]:
    resolved_start_dir = _resolve_start_dir(start_dir)
    project_root = get_project_root_or_none(start_dir=resolved_start_dir)
    stop_dir = resolved_start_dir if project_root is None else project_root
    parent_dirs = _iter_parent_dirs(resolved_start_dir, stop_dir)
    relay_teams_sources = [
        (
            SkillSource.PROJECT_RELAY_TEAMS,
            current_dir / ".relay-teams" / "skills",
        )
        for current_dir in parent_dirs
    ]
    agents_sources = [
        (
            SkillSource.PROJECT_AGENTS,
            current_dir / ".agents" / "skills",
        )
        for current_dir in parent_dirs
    ]
    sources = relay_teams_sources + agents_sources
    return tuple((_source, _resolve_dir(path)) for _source, path in sources)


def _iter_parent_dirs(start_dir: Path, stop_dir: Path) -> tuple[Path, ...]:
    current_dir = _resolve_start_dir(start_dir)
    resolved_stop_dir = _resolve_start_dir(stop_dir)
    directories: list[Path] = []
    while True:
        directories.append(current_dir)
        if current_dir == resolved_stop_dir or current_dir.parent == current_dir:
            break
        current_dir = current_dir.parent
    return tuple(directories)


def _resolve_start_dir(path: Path | None) -> Path:
    if path is None:
        return Path.cwd().resolve()
    resolved = path.expanduser().resolve()
    if resolved.is_file():
        return resolved.parent
    return resolved


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


def _parse_frontmatter_hooks(value: object) -> HooksConfig:
    try:
        if isinstance(value, dict) and "hooks" in value:
            return parse_tolerant_hooks_payload(value)
        if isinstance(value, dict):
            return parse_tolerant_hooks_payload({"hooks": value})
    except Exception as exc:
        logger.warning("Ignoring invalid skill frontmatter hooks: %s", exc)
    return HooksConfig()
