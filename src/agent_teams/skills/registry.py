# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
import importlib.util
import inspect
import io
from contextlib import redirect_stdout
from types import ModuleType

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Tool

from agent_teams.shared_types.json_types import JsonObject, JsonValue
from agent_teams.skills.discovery import SkillsDirectory
from agent_teams.skills.models import Skill, SkillInstructionEntry
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool

type SkillEntrypoint = Callable[..., object]


class SkillRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    directory: SkillsDirectory

    def list_skill_definitions(self) -> tuple[Skill, ...]:
        self.directory.discover()
        skills = self.directory.list_skills()
        return tuple(sorted(skills, key=lambda item: item.metadata.name))

    def get_skill_definition(self, name: str) -> Skill | None:
        self.directory.discover()
        return self.directory.get_skill(name)

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
        known = {skill.metadata.name for skill in self.list_skill_definitions()}
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
        self.validate_known(skill_names)
        all_skills = self.directory.list_skills()
        entries: list[SkillInstructionEntry] = []
        for name in skill_names:
            skill = next(
                (item for item in all_skills if item.metadata.name == name), None
            )
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

    async def list_skills(self, ctx: ToolContext) -> JsonObject:
        return await execute_tool(
            ctx,
            tool_name="list_skills",
            args_summary={},
            action=lambda: [
                _skill_to_json(skill) for skill in self.list_skill_definitions()
            ],
        )

    async def load_skill(self, ctx: ToolContext, name: str) -> JsonObject:
        async def _action() -> JsonValue:
            skill = self.get_skill_definition(name)
            if skill is None:
                raise KeyError(f"Skill not found: {name}")
            return _skill_to_json(skill)

        return await execute_tool(
            ctx, tool_name="load_skill", args_summary={"name": name}, action=_action
        )

    async def read_skill_resource(
        self, ctx: ToolContext, skill_name: str, resource_path: str
    ) -> JsonObject:
        async def _action() -> JsonValue:
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
        args: JsonObject | None = None,
    ) -> JsonObject:
        async def _action() -> JsonValue:
            skill = self.get_skill_definition(skill_name)
            if skill is None:
                raise KeyError(f"Skill not found: {skill_name}")

            script = skill.metadata.scripts.get(script_name)
            if script is None:
                raise KeyError(f"Script {script_name} not found in skill {skill_name}")

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


def _skill_to_json(skill: Skill) -> JsonObject:
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
        normalized: JsonObject = {}
        for key, item in value.items():
            normalized[str(key)] = _normalize_script_result(item)
        return normalized
    return str(value)
