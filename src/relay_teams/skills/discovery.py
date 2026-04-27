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
_DISCOVERY_WARNING_SAMPLE_LIMIT = 5

_SkillDiscoverySignature = tuple[tuple[str, str, int, int], ...]
_SkillLoadWarningEntry = tuple[Path, str]
_SkillDuplicateWarningEntry = tuple[str, Path, SkillSource, Path, SkillSource]


def get_builtin_skills_dir_path() -> Path:
    return get_builtin_skills_dir()


def get_app_skills_dir(user_home_dir: Path | None = None) -> Path:
    return get_app_config_dir(user_home_dir=user_home_dir) / "skills"


def get_user_skills_dir(user_home_dir: Path | None = None) -> Path:
    return get_app_skills_dir(user_home_dir=user_home_dir)


def get_codex_skills_dir(user_home_dir: Path | None = None) -> Path:
    app_config_dir = get_app_config_dir(user_home_dir=user_home_dir)
    return app_config_dir.parent / ".codex" / "skills"


def get_claude_skills_dir(user_home_dir: Path | None = None) -> Path:
    app_config_dir = get_app_config_dir(user_home_dir=user_home_dir)
    return app_config_dir.parent / ".claude" / "skills"


def get_opencode_skills_dir(user_home_dir: Path | None = None) -> Path:
    app_config_dir = get_app_config_dir(user_home_dir=user_home_dir)
    return app_config_dir.parent / ".config" / "opencode" / "skills"


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
        self._discovery_signature: _SkillDiscoverySignature | None = None
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
                codex_skills_dir=resolved_app_config_dir.parent / ".codex" / "skills",
                claude_skills_dir=resolved_app_config_dir.parent / ".claude" / "skills",
                opencode_skills_dir=resolved_app_config_dir.parent
                / ".config"
                / "opencode"
                / "skills",
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
                codex_skills_dir=get_codex_skills_dir(user_home_dir=user_home_dir),
                claude_skills_dir=get_claude_skills_dir(user_home_dir=user_home_dir),
                opencode_skills_dir=get_opencode_skills_dir(
                    user_home_dir=user_home_dir
                ),
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
            discovery_signature = self._build_discovery_signature()
            with self._lock:
                if discovery_signature == self._discovery_signature:
                    return

            discovered_skills: dict[str, Skill] = {}
            duplicate_warnings: list[_SkillDuplicateWarningEntry] = []
            load_warnings: list[_SkillLoadWarningEntry] = []
            had_transient_load_failure = False
            for source, base_dir in self.sources:
                if not base_dir.exists():
                    continue
                for path in self._iter_skill_manifest_paths(base_dir):
                    try:
                        skill = self._load_skill(
                            path=path,
                            source=source,
                            load_warnings=load_warnings,
                        )
                        if skill is None:
                            continue
                        existing_skill = discovered_skills.get(skill.metadata.name)
                        if existing_skill is not None:
                            duplicate_warnings.append(
                                (
                                    skill.metadata.name,
                                    existing_skill.directory,
                                    existing_skill.source,
                                    skill.directory,
                                    skill.source,
                                )
                            )
                        discovered_skills[skill.metadata.name] = skill
                    except OSError as exc:
                        had_transient_load_failure = True
                        load_warnings.append((path, str(exc)))
                    except Exception as exc:
                        load_warnings.append((path, str(exc)))
            with self._lock:
                self._skills = discovered_skills
                if had_transient_load_failure:
                    self._discovery_signature = None
                else:
                    self._discovery_signature = discovery_signature
            self._log_discovery_warnings(
                duplicate_warnings=duplicate_warnings,
                load_warnings=load_warnings,
            )

    def _build_discovery_signature(self) -> _SkillDiscoverySignature:
        signature: list[tuple[str, str, int, int]] = []
        for source, base_dir in self.sources:
            if not base_dir.exists():
                continue
            for manifest_path in self._iter_skill_manifest_paths(base_dir):
                try:
                    discovery_paths = _iter_skill_discovery_paths(manifest_path)
                except (OSError, RuntimeError):
                    signature.append(
                        (source.value, _safe_signature_path(manifest_path), -1, -1)
                    )
                    continue
                for path in discovery_paths:
                    signature_path = _safe_signature_path(path)
                    try:
                        stat_result = path.stat()
                        signature.append(
                            (
                                source.value,
                                signature_path,
                                stat_result.st_mtime_ns,
                                stat_result.st_size,
                            )
                        )
                    except OSError:
                        signature.append((source.value, signature_path, -1, -1))
        return tuple(signature)

    def _iter_skill_manifest_paths(self, base_dir: Path) -> tuple[Path, ...]:
        return tuple(
            path
            for path in sorted(base_dir.rglob("SKILL.md"))
            if len(path.relative_to(base_dir).parts) <= self.max_depth + 1
        )

    @staticmethod
    def _log_discovery_warnings(
        *,
        duplicate_warnings: list[_SkillDuplicateWarningEntry],
        load_warnings: list[_SkillLoadWarningEntry],
    ) -> None:
        if duplicate_warnings:
            samples = tuple(
                (
                    f"{name}: {previous_path} ({previous_source.value}) -> "
                    f"{selected_path} ({selected_source.value})"
                )
                for (
                    name,
                    previous_path,
                    previous_source,
                    selected_path,
                    selected_source,
                ) in duplicate_warnings[:_DISCOVERY_WARNING_SAMPLE_LIMIT]
            )
            logger.info(
                "Resolved %s duplicate skill definitions by source precedence. %s",
                len(duplicate_warnings),
                _format_discovery_warning_samples(
                    samples=samples,
                    total_count=len(duplicate_warnings),
                ),
            )
        if load_warnings:
            samples = tuple(
                f"{path}: {message}"
                for path, message in load_warnings[:_DISCOVERY_WARNING_SAMPLE_LIMIT]
            )
            logger.warning(
                "Skipped %s invalid skill manifests during discovery. %s",
                len(load_warnings),
                _format_discovery_warning_samples(
                    samples=samples,
                    total_count=len(load_warnings),
                ),
            )

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

    def _load_skill(
        self,
        *,
        path: Path,
        source: SkillSource,
        load_warnings: list[_SkillLoadWarningEntry] | None = None,
    ) -> Skill | None:
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
                if load_warnings is not None:
                    load_warnings.append((path, str(exc)))
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
    codex_skills_dir: Path,
    claude_skills_dir: Path,
    opencode_skills_dir: Path,
    relay_teams_skills_dir: Path,
    agents_skills_dir: Path,
    project_start_dir: Path | None,
) -> tuple[tuple[SkillSource, Path], ...]:
    sources: list[tuple[SkillSource, Path]] = [
        (SkillSource.BUILTIN, _resolve_dir(builtin_skills_dir)),
        (SkillSource.USER_CODEX, _resolve_dir(codex_skills_dir)),
        (SkillSource.USER_CLAUDE, _resolve_dir(claude_skills_dir)),
        (SkillSource.USER_OPENCODE, _resolve_dir(opencode_skills_dir)),
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
    source_specs = (
        (SkillSource.PROJECT_CODEX, ".codex"),
        (SkillSource.PROJECT_CLAUDE, ".claude"),
        (SkillSource.PROJECT_OPENCODE, ".opencode"),
        (SkillSource.PROJECT_RELAY_TEAMS, ".relay-teams"),
        (SkillSource.PROJECT_AGENTS, ".agents"),
    )
    sources = [
        (source, current_dir / directory_name / "skills")
        for source, directory_name in source_specs
        for current_dir in parent_dirs
    ]
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


def _safe_signature_path(path: Path) -> str:
    try:
        return path.resolve().as_posix()
    except (OSError, RuntimeError):
        return path.absolute().as_posix()


def _iter_skill_discovery_paths(manifest_path: Path) -> tuple[Path, ...]:
    skill_dir = manifest_path.parent
    paths: list[Path] = [manifest_path]
    for resource_dir_name in ("resources", "assets"):
        resource_dir = skill_dir / resource_dir_name
        if resource_dir.exists() and resource_dir.is_dir():
            paths.extend(
                path for path in sorted(resource_dir.glob("*")) if path.is_file()
            )
    scripts_dir = skill_dir / "scripts"
    if scripts_dir.exists() and scripts_dir.is_dir():
        paths.extend(sorted(scripts_dir.glob("*.py")))
    return tuple(paths)


def _format_discovery_warning_samples(
    *,
    samples: tuple[str, ...],
    total_count: int,
) -> str:
    if not samples:
        return "No examples available."
    examples = "; ".join(samples)
    omitted_count = total_count - len(samples)
    if omitted_count <= 0:
        return f"Examples: {examples}."
    return f"Examples: {examples}; {omitted_count} more omitted."


def _parse_frontmatter_hooks(value: object) -> HooksConfig:
    try:
        if isinstance(value, dict) and "hooks" in value:
            return parse_tolerant_hooks_payload(value)
        if isinstance(value, dict):
            return parse_tolerant_hooks_payload({"hooks": value})
    except Exception as exc:
        logger.warning("Ignoring invalid skill frontmatter hooks: %s", exc)
    return HooksConfig()
