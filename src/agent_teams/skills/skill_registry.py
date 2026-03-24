# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai import Tool

from agent_teams.logger import get_logger

from agent_teams.skills.discovery import SkillsDirectory
from agent_teams.skills.skill_models import Skill, SkillInstructionEntry
from agent_teams.trace import trace_span
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool

LOGGER = get_logger(__name__)


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
    ) -> SkillRegistry:
        return cls(
            directory=SkillsDirectory.from_config_dirs(
                app_config_dir=app_config_dir,
                max_depth=max_depth,
            )
        )

    @classmethod
    def from_default_scopes(
        cls,
        *,
        user_home_dir: Path | None = None,
        max_depth: int = 3,
    ) -> SkillRegistry:
        return cls(
            directory=SkillsDirectory.from_default_scopes(
                user_home_dir=user_home_dir,
                max_depth=max_depth,
            )
        )

    def list_skill_definitions(self) -> tuple[Skill, ...]:
        with trace_span(
            LOGGER,
            component="skills.registry",
            operation="list_skill_definitions",
        ):
            skills = self._get_effective_skill_map().values()
            return tuple(sorted(skills, key=lambda item: item.metadata.name))

    def get_skill_definition(self, name: str) -> Skill | None:
        with trace_span(
            LOGGER,
            component="skills.registry",
            operation="get_skill_definition",
            attributes={"skill_name": name},
        ):
            return self._get_effective_skill_map().get(name)

    def get_toolset_tools(self, skill_names: tuple[str, ...]) -> list[Tool[ToolDeps]]:
        _ = skill_names
        tools: list[Tool[ToolDeps]] = [
            Tool(
                self.load_skill,
                name="load_skill",
                description=(
                    "Load a specific skill by name, including its instructions and "
                    "absolute file paths."
                ),
            ),
        ]
        return tools

    def validate_known(self, skill_names: tuple[str, ...]) -> None:
        with trace_span(
            LOGGER,
            component="skills.registry",
            operation="validate_known",
            attributes={"skill_names": list(skill_names)},
        ):
            known = set(self._get_effective_skill_map().keys())
            missing = [name for name in skill_names if name not in known]
            if missing:
                raise ValueError(f"Unknown skills: {missing}")

    def list_names(self) -> tuple[str, ...]:
        return tuple(skill.metadata.name for skill in self.list_skill_definitions())

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
            self.validate_known(skill_names)
            skill_map = self._get_effective_skill_map()
            entries: list[SkillInstructionEntry] = []
            for name in skill_names:
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
            with trace_span(
                LOGGER,
                component="skills.registry",
                operation="load_skill",
                attributes={"skill_name": name},
                trace_id=ctx.deps.trace_id,
                run_id=ctx.deps.run_id,
                task_id=ctx.deps.task_id,
                session_id=ctx.deps.session_id,
                instance_id=ctx.deps.instance_id,
                role_id=ctx.deps.role_id,
                tool_call_id=ctx.tool_call_id,
            ):
                skill = self.get_skill_definition(name)
                if skill is None:
                    raise KeyError(f"Skill not found: {name}")
                return _skill_to_json(skill)

        return await execute_tool(
            ctx,
            tool_name="load_skill",
            args_summary={"name": name},
            action=_action,
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
    return {
        "name": metadata.name,
        "description": metadata.description,
        "manifest_path": _normalize_skill_path(manifest_path),
        "manifest_content": manifest_path.read_text(encoding="utf-8"),
        "instructions": metadata.instructions,
        "scope": skill.scope.value,
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
        "files": [
            _normalize_skill_path(path)
            for path in _iter_skill_files(skill.directory.rglob("*"))
        ],
    }


def _normalize_skill_path(path: Path) -> str:
    return path.resolve().as_posix()


def _iter_skill_files(paths: Iterable[Path]) -> tuple[Path, ...]:
    return tuple(sorted((path.resolve() for path in paths if path.is_file())))
