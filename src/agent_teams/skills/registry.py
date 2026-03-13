# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
import importlib.util
import inspect
import io
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType

from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai import Tool

from agent_teams.logger import get_logger

from agent_teams.skills.discovery import SkillsDirectory
from agent_teams.skills.models import Skill, SkillInstructionEntry
from agent_teams.trace import trace_span
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool

type SkillEntrypoint = Callable[..., object]

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
                self.list_skills,
                name="list_skills",
                description="List all discovered skills.",
            ),
            Tool(
                self.load_skill,
                name="load_skill",
                description="Load a specific skill by name.",
            ),
            Tool(
                self.read_skill_resource,
                name="read_skill_resource",
                description="Read a resource file from a skill.",
            ),
            Tool(
                self.run_skill_script,
                name="run_skill_script",
                description="Run a script associated with a skill.",
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
        return "\n\n".join(entry.instructions for entry in entries)

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
                instructions = skill.metadata.instructions.strip()
                if instructions:
                    entries.append(
                        SkillInstructionEntry(
                            name=skill.metadata.name,
                            instructions=instructions,
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

    async def read_skill_resource(
        self, ctx: ToolContext, skill_name: str, resource_path: str
    ) -> dict[str, JsonValue]:
        async def _action() -> JsonValue:
            with trace_span(
                LOGGER,
                component="skills.registry",
                operation="read_skill_resource",
                attributes={
                    "skill_name": skill_name,
                    "resource_path": resource_path,
                },
                trace_id=ctx.deps.trace_id,
                run_id=ctx.deps.run_id,
                task_id=ctx.deps.task_id,
                session_id=ctx.deps.session_id,
                instance_id=ctx.deps.instance_id,
                role_id=ctx.deps.role_id,
                tool_call_id=ctx.tool_call_id,
            ):
                skill = self.get_skill_definition(skill_name)
                if skill is None:
                    raise KeyError(f"Skill not found: {skill_name}")
                resource = skill.metadata.resources.get(resource_path)
                if resource is None or resource.path is None:
                    raise FileNotFoundError(
                        f"Resource {resource_path} not found in skill {skill_name}"
                    )
                return resource.path.read_text("utf-8")

        return await execute_tool(
            ctx,
            tool_name="read_skill_resource",
            args_summary={"skill_name": skill_name, "resource_path": resource_path},
            action=_action,
        )

    async def run_skill_script(
        self,
        ctx: ToolContext,
        skill_name: str,
        script_name: str,
        args: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        async def _action() -> JsonValue:
            with trace_span(
                LOGGER,
                component="skills.registry",
                operation="run_skill_script",
                attributes={
                    "skill_name": skill_name,
                    "script_name": script_name,
                    "has_args": bool(args),
                },
                trace_id=ctx.deps.trace_id,
                run_id=ctx.deps.run_id,
                task_id=ctx.deps.task_id,
                session_id=ctx.deps.session_id,
                instance_id=ctx.deps.instance_id,
                role_id=ctx.deps.role_id,
                tool_call_id=ctx.tool_call_id,
            ):
                skill = self.get_skill_definition(skill_name)
                if skill is None:
                    raise KeyError(f"Skill not found: {skill_name}")

                script = skill.metadata.scripts.get(script_name)
                if script is None:
                    raise KeyError(
                        f"Script {script_name} not found in skill {skill_name}"
                    )

                spec = importlib.util.spec_from_file_location(
                    f"skill_script_{skill_name}_{script_name}", script.path
                )
                if spec is None or spec.loader is None:
                    raise ImportError(
                        f"Could not load script {script_name} from {script.path}"
                    )

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                run_fn = _resolve_script_entrypoint(module, skill_name, script_name)

                stdout_buffer = io.StringIO()
                with redirect_stdout(stdout_buffer):
                    if inspect.iscoroutinefunction(run_fn):
                        try:
                            ret = await run_fn(ctx, **(args or {}))
                        except TypeError:
                            ret = await run_fn()
                    else:
                        try:
                            ret = run_fn(ctx, **(args or {}))
                        except TypeError:
                            ret = run_fn()

                output = stdout_buffer.getvalue().strip()
                if output:
                    return output
                return _normalize_script_result(ret)

        return await execute_tool(
            ctx,
            tool_name=f"skill:{skill_name}:{script_name}",
            args_summary=args or {},
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


def _resolve_script_entrypoint(
    module: ModuleType, skill_name: str, script_name: str
) -> SkillEntrypoint:
    run_fn = getattr(module, "run", None)
    if callable(run_fn):
        return run_fn

    main_fn = getattr(module, "main", None)
    if callable(main_fn):
        return main_fn

    raise AttributeError(
        f"Script {script_name} in skill {skill_name} has no 'run' or 'main' function"
    )


def _skill_to_json(skill: Skill) -> dict[str, JsonValue]:
    metadata = skill.metadata
    return {
        "name": metadata.name,
        "description": metadata.description,
        "instructions": metadata.instructions,
        "scope": skill.scope.value,
        "directory": str(skill.directory),
        "resources": {
            name: {
                "name": resource.name,
                "description": resource.description,
                "path": str(resource.path) if resource.path is not None else None,
                "content": resource.content,
            }
            for name, resource in metadata.resources.items()
        },
        "scripts": {
            name: {
                "name": script.name,
                "description": script.description,
                "path": str(script.path),
            }
            for name, script in metadata.scripts.items()
        },
    }


def _normalize_script_result(value: object) -> JsonValue:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_normalize_script_result(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            normalized[str(key)] = _normalize_script_result(item)
        return normalized
    return str(value)
