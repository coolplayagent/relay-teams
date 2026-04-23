# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai import Tool

from relay_teams.logger import get_logger, log_event
from relay_teams.skills.discovery import SkillsDirectory
from relay_teams.skills.skill_models import (
    Skill,
    SkillInstructionEntry,
    SkillOptionEntry,
    SkillSummaryEntry,
)
from relay_teams.trace import trace_span
from relay_teams.tools.runtime.context import (
    ToolContext,
    ToolDeps,
)
from relay_teams.tools.runtime.execution import execute_tool

LOGGER = get_logger(__name__)
_SKILL_LOAD_MAX_PAYLOAD_CHARS = 120_000
_SKILL_LOAD_MAX_FILE_COUNT = 200
_SKILL_LOAD_EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "out",
        "target",
        "venv",
    }
)
_SKILL_LOAD_EXCLUDED_FILE_SUFFIXES = frozenset({".pyc", ".pyo"})
_LEGACY_SCOPED_SKILL_PREFIXES = frozenset({"app", "builtin"})


class _SkillFileSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[str] = Field(default_factory=list)
    omitted_count: int = 0


class SkillRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    directory: SkillsDirectory

    @classmethod
    def from_skill_dirs(
        cls,
        *,
        app_skills_dir: Path,
        builtin_skills_dir: Path | None = None,
        max_depth: int = 3,
    ) -> SkillRegistry:
        return cls(
            directory=SkillsDirectory.from_skill_dirs(
                app_skills_dir=app_skills_dir,
                builtin_skills_dir=builtin_skills_dir,
                max_depth=max_depth,
            )
        )

    @classmethod
    def from_config_dirs(
        cls,
        *,
        app_config_dir: Path,
        max_depth: int = 3,
        project_start_dir: Path | None = None,
    ) -> SkillRegistry:
        return cls(
            directory=SkillsDirectory.from_config_dirs(
                app_config_dir=app_config_dir,
                max_depth=max_depth,
                project_start_dir=project_start_dir,
            )
        )

    @classmethod
    def from_default_scopes(
        cls,
        *,
        user_home_dir: Path | None = None,
        max_depth: int = 3,
        start_dir: Path | None = None,
    ) -> SkillRegistry:
        return cls(
            directory=SkillsDirectory.from_default_scopes(
                user_home_dir=user_home_dir,
                max_depth=max_depth,
                start_dir=start_dir,
            )
        )

    def list_skill_definitions(self) -> tuple[Skill, ...]:
        with trace_span(
            LOGGER,
            component="skills.registry",
            operation="list_skill_definitions",
        ):
            skills = self._get_effective_skill_map().values()
            return tuple(sorted(skills, key=_skill_sort_key))

    def get_skill_definition(self, name: str) -> Skill | None:
        raw_name = name.strip()
        with trace_span(
            LOGGER,
            component="skills.registry",
            operation="get_skill_definition",
            attributes={"skill_name": raw_name},
        ):
            skill_map = self._get_effective_skill_map()
            normalized_name = _normalize_legacy_scoped_skill_name(
                name=raw_name,
                skill_map=skill_map,
            )
            return skill_map.get(normalized_name)

    def get_toolset_tools(self, skill_names: tuple[str, ...]) -> list[Tool[ToolDeps]]:
        _ = skill_names
        tools: list[Tool[ToolDeps]] = [
            Tool(
                self.load_skill,
                name="load_skill",
                description=(
                    "Load a specific skill by name, including its instructions, "
                    "resources, scripts, and selected absolute file paths."
                ),
            ),
        ]
        return tools

    def resolve_known(
        self,
        skill_names: tuple[str, ...],
        *,
        strict: bool = True,
        consumer: str | None = None,
    ) -> tuple[str, ...]:
        attributes: dict[str, JsonValue] = {
            "skill_names": list(skill_names),
            "strict": strict,
        }
        if consumer is not None:
            attributes["consumer"] = consumer
        with trace_span(
            LOGGER,
            component="skills.registry",
            operation="resolve_known",
            attributes=attributes,
        ):
            resolved, missing = self._resolve_skill_names(
                skill_names,
                strict=strict,
            )
            if strict and missing:
                raise ValueError(f"Unknown skills: {list(missing)}")
            if missing:
                self._log_ignored_unknown_skills(
                    skill_names=skill_names,
                    resolved_names=resolved,
                    missing_names=missing,
                    consumer=consumer,
                )
            return resolved

    def validate_known(self, skill_names: tuple[str, ...]) -> None:
        with trace_span(
            LOGGER,
            component="skills.registry",
            operation="validate_known",
            attributes={"skill_names": list(skill_names)},
        ):
            _, missing = self._resolve_skill_names(skill_names, strict=True)
            if missing:
                raise ValueError(f"Unknown skills: {list(missing)}")

    def list_names(self) -> tuple[str, ...]:
        return tuple(skill.ref for skill in self.list_skill_definitions())

    def list_skill_options(self) -> tuple[SkillOptionEntry, ...]:
        with trace_span(
            LOGGER,
            component="skills.registry",
            operation="list_skill_options",
        ):
            return tuple(
                SkillOptionEntry(
                    ref=skill.ref,
                    name=skill.metadata.name,
                    description=skill.metadata.description.strip(),
                    source=skill.source,
                )
                for skill in self.list_skill_definitions()
            )

    def list_skill_summaries(self) -> tuple[SkillSummaryEntry, ...]:
        with trace_span(
            LOGGER,
            component="skills.registry",
            operation="list_skill_summaries",
        ):
            return tuple(
                SkillSummaryEntry(
                    ref=skill.ref,
                    name=skill.metadata.name,
                    description=skill.metadata.description.strip(),
                    source=skill.source,
                )
                for skill in self.list_skill_definitions()
            )

    def get_instructions(self, skill_names: tuple[str, ...]) -> str:
        entries = self.get_instruction_entries(skill_names)
        return "\n\n".join(entry.description for entry in entries)

    def get_instruction_entries(
        self, skill_names: tuple[str, ...]
    ) -> tuple[SkillInstructionEntry, ...]:
        with trace_span(
            LOGGER,
            component="skills.registry",
            operation="get_instruction_entries",
            attributes={"skill_names": list(skill_names)},
        ):
            resolved_names, missing = self._resolve_skill_names(skill_names)
            if missing:
                raise ValueError(f"Unknown skills: {list(missing)}")
            skill_map = self._get_effective_skill_map()
            entries: list[SkillInstructionEntry] = []
            for name in resolved_names:
                skill = skill_map.get(name)
                if skill is None:
                    continue
                description = skill.metadata.description.strip()
                if description:
                    entries.append(
                        SkillInstructionEntry(
                            name=skill.metadata.name,
                            description=description,
                        )
                    )
            return tuple(entries)

    async def list_skills(self, ctx: ToolContext) -> dict[str, JsonValue]:
        return await execute_tool(
            ctx,
            tool_name="list_skills",
            args_summary={},
            action=lambda: [
                _skill_to_json(skill) for skill in self.list_skill_definitions()
            ],
        )

    async def load_skill(self, ctx: ToolContext, name: str) -> dict[str, JsonValue]:
        async def _action() -> JsonValue:
            requested_name = name.strip()
            with trace_span(
                LOGGER,
                component="skills.registry",
                operation="load_skill",
                attributes={"skill_name": requested_name},
                trace_id=ctx.deps.trace_id,
                run_id=ctx.deps.run_id,
                task_id=ctx.deps.task_id,
                session_id=ctx.deps.session_id,
                instance_id=ctx.deps.instance_id,
                role_id=ctx.deps.role_id,
                tool_call_id=ctx.tool_call_id,
            ):
                role = _get_effective_role_for_skill_load(
                    skill_registry=self,
                    ctx=ctx,
                )
                resolved_name = _resolve_skill_name_for_role(
                    skill_registry=self,
                    role=role,
                    requested_name=requested_name,
                )
                if resolved_name is None:
                    raise PermissionError(
                        f"Role {role.role_id} is not authorized to load skill: {requested_name}"
                    )
                skill = self.get_skill_definition(resolved_name)
                if skill is None:
                    raise KeyError(f"Skill not found: {requested_name}")
                result = _skill_to_json(skill)
                omitted_count_value = result.get("files_omitted_count")
                omitted_count = (
                    omitted_count_value if isinstance(omitted_count_value, int) else 0
                )
                if omitted_count > 0:
                    files_value = result.get("files")
                    returned_count = (
                        len(files_value) if isinstance(files_value, list) else 0
                    )
                    log_event(
                        LOGGER,
                        logging.WARNING,
                        event="skill.load.truncated",
                        message="Skill payload truncated for load_skill",
                        payload={
                            "skill_ref": skill.ref,
                            "skill_name": skill.metadata.name,
                            "directory": _normalize_skill_path(skill.directory),
                            "returned_file_count": returned_count,
                            "omitted_file_count": omitted_count,
                        },
                    )
                return result

        return await execute_tool(
            ctx,
            tool_name="load_skill",
            args_summary={"name": name},
            action=_action,
        )

    def _resolve_skill_names(
        self,
        skill_names: tuple[str, ...],
        *,
        strict: bool = False,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        skill_map = self._get_effective_skill_map()
        resolved_names: list[str] = []
        missing_names: list[str] = []
        for raw_name in skill_names:
            name = raw_name.strip()
            if not name:
                if strict:
                    missing_names.append("")
                continue
            normalized_name = _normalize_legacy_scoped_skill_name(
                name=name,
                skill_map=skill_map,
            )
            if normalized_name in skill_map:
                resolved_names.append(normalized_name)
            else:
                missing_names.append(name)
        return (tuple(resolved_names), tuple(missing_names))

    def _log_ignored_unknown_skills(
        self,
        *,
        skill_names: tuple[str, ...],
        resolved_names: tuple[str, ...],
        missing_names: tuple[str, ...],
        consumer: str | None,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "requested_skill_names": list(skill_names),
            "resolved_skill_names": list(resolved_names),
            "ignored_skill_names": list(missing_names),
        }
        if consumer is not None:
            payload["consumer"] = consumer
        log_event(
            LOGGER,
            logging.WARNING,
            event="skills.registry.unknown_ignored",
            message="Ignoring unknown skills from existing configuration",
            payload=payload,
        )

    def _get_effective_skill_map(self) -> dict[str, Skill]:
        with trace_span(
            LOGGER,
            component="skills.registry",
            operation="build_effective_skill_map",
        ):
            self.directory.discover()
            return {
                skill.metadata.name: skill for skill in self.directory.list_skills()
            }


def _skill_to_json(skill: Skill) -> dict[str, JsonValue]:
    metadata = skill.metadata
    manifest_path = (skill.directory / "SKILL.md").resolve()
    payload: dict[str, JsonValue] = {
        "ref": skill.ref,
        "name": metadata.name,
        "description": metadata.description,
        "manifest_path": _normalize_skill_path(manifest_path),
        "manifest_content": manifest_path.read_text(encoding="utf-8"),
        "instructions": metadata.instructions,
        "source": skill.source.value,
        "directory": _normalize_skill_path(skill.directory),
        "resources": {
            name: {
                "name": resource.name,
                "description": resource.description,
                "path": (
                    _normalize_skill_path(resource.path)
                    if resource.path is not None
                    else None
                ),
                "content": resource.content,
            }
            for name, resource in metadata.resources.items()
        },
        "scripts": {
            name: {
                "name": script.name,
                "description": script.description,
                "path": _normalize_skill_path(script.path),
            }
            for name, script in metadata.scripts.items()
        },
    }
    file_selection = _select_skill_files(skill=skill, base_payload=payload)
    payload["files"] = cast(JsonValue, file_selection.files)
    payload["files_truncated"] = file_selection.omitted_count > 0
    payload["files_omitted_count"] = file_selection.omitted_count
    return payload


def _normalize_skill_path(path: Path) -> str:
    return path.resolve().as_posix()


def _normalize_legacy_scoped_skill_name(
    *,
    name: str,
    skill_map: dict[str, Skill],
) -> str:
    prefix, separator, suffix = name.partition(":")
    if separator != ":":
        return name
    if prefix.strip().lower() not in _LEGACY_SCOPED_SKILL_PREFIXES:
        return name
    normalized_name = suffix.strip()
    if not normalized_name:
        return name
    if normalized_name not in skill_map:
        return name
    return normalized_name


def _iter_skill_files(skill_dir: Path) -> tuple[Path, ...]:
    resolved_skill_dir = skill_dir.resolve()
    return tuple(
        sorted(
            (
                path.resolve()
                for path in skill_dir.rglob("*")
                if path.is_file()
                and not _should_exclude_skill_file(
                    skill_dir=resolved_skill_dir, path=path
                )
            ),
            key=lambda path: _skill_file_sort_key(
                skill_dir=resolved_skill_dir, path=path
            ),
        )
    )


def _select_skill_files(
    *, skill: Skill, base_payload: dict[str, JsonValue]
) -> _SkillFileSelection:
    candidate_files = [
        _normalize_skill_path(path) for path in _iter_skill_files(skill.directory)
    ]
    if not candidate_files:
        return _SkillFileSelection()

    max_candidates = min(len(candidate_files), _SKILL_LOAD_MAX_FILE_COUNT)
    selected_files: list[str] = []
    for index, file_path in enumerate(candidate_files[:max_candidates], start=1):
        remaining_count = len(candidate_files) - index
        candidate_selection = [*selected_files, file_path]
        candidate_payload = {
            **base_payload,
            "files": candidate_selection,
            "files_truncated": remaining_count > 0,
            "files_omitted_count": remaining_count,
        }
        if _skill_payload_char_count(candidate_payload) > _SKILL_LOAD_MAX_PAYLOAD_CHARS:
            break
        selected_files = candidate_selection
    return _SkillFileSelection(
        files=selected_files,
        omitted_count=len(candidate_files) - len(selected_files),
    )


def _skill_payload_char_count(payload: dict[str, JsonValue]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _should_exclude_skill_file(*, skill_dir: Path, path: Path) -> bool:
    relative_path = path.resolve().relative_to(skill_dir.resolve())
    if any(part in _SKILL_LOAD_EXCLUDED_DIR_NAMES for part in relative_path.parts[:-1]):
        return True
    return relative_path.suffix in _SKILL_LOAD_EXCLUDED_FILE_SUFFIXES


def _skill_file_sort_key(*, skill_dir: Path, path: Path) -> tuple[int, int, str]:
    relative_path = path.resolve().relative_to(skill_dir.resolve())
    priority = 2
    if relative_path.name == "SKILL.md":
        priority = 0
    elif relative_path.parts and relative_path.parts[0] in {"resources", "scripts"}:
        priority = 1
    return (priority, len(relative_path.parts), relative_path.as_posix())


def _raise_if_skill_unauthorized(
    *,
    skill_registry: SkillRegistry,
    ctx: ToolContext,
    skill_name: str,
) -> None:
    role = _get_effective_role_for_skill_load(
        skill_registry=skill_registry,
        ctx=ctx,
    )
    resolved_name = _resolve_skill_name_for_role(
        skill_registry=skill_registry,
        role=role,
        requested_name=skill_name,
    )
    if resolved_name is not None:
        return
    raise PermissionError(
        f"Role {role.role_id} is not authorized to load skill: {skill_name}"
    )


def _get_effective_role_for_skill_load(
    *,
    skill_registry: SkillRegistry,
    ctx: ToolContext,
):
    _ = skill_registry
    runtime_role_resolver = getattr(ctx.deps, "runtime_role_resolver", None)
    if runtime_role_resolver is not None:
        try:
            return runtime_role_resolver.get_effective_role(
                run_id=ctx.deps.run_id,
                role_id=ctx.deps.role_id,
            )
        except KeyError:
            pass
    return ctx.deps.role_registry.get(ctx.deps.role_id)


def _resolve_skill_name_for_role(
    *,
    skill_registry: SkillRegistry,
    role: object,
    requested_name: str,
) -> str | None:
    requested = requested_name.strip()
    if not requested:
        return None
    authorized_set = {
        _normalize_legacy_scoped_skill_ref(str(item).strip())
        for item in getattr(role, "skills", ())
        if str(item).strip()
    }
    normalized_requested = _normalize_legacy_scoped_skill_ref(requested)
    if normalized_requested not in authorized_set:
        return None
    return normalized_requested


def _normalize_legacy_scoped_skill_ref(name: str) -> str:
    prefix, separator, suffix = name.partition(":")
    if separator != ":":
        return name
    if prefix.strip().lower() not in _LEGACY_SCOPED_SKILL_PREFIXES:
        return name
    normalized_name = suffix.strip()
    return normalized_name if normalized_name else name


def _skill_sort_key(skill: Skill) -> tuple[str]:
    return (skill.metadata.name,)
