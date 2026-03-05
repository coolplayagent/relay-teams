from __future__ import annotations

import io
import inspect
import importlib.util
from contextlib import redirect_stdout
from pydantic_ai import Tool
from pydantic import BaseModel, ConfigDict

from agent_teams.core.types import JsonObject, JsonValue
from agent_teams.skills.discovery import SkillsDirectory
from agent_teams.tools.runtime import ToolContext, ToolDeps
from agent_teams.tools.tool_helpers import execute_tool

from agent_teams.skills.models import SkillMetadata


class SkillRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    directory: SkillsDirectory

    def get_toolset_tools(self, skill_names: tuple[str, ...]) -> list[Tool[ToolDeps]]:
        # This returns the core tools for managing skills, not the skills themselves
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
        """Call discover() before validate_known to ensure internal registry is populated."""
        self.directory.discover()
        all_skills = self.directory.list_skills()
        known = {s.metadata.name for s in all_skills}
        missing = [name for name in skill_names if name not in known]
        if missing:
            raise ValueError(f"Unknown skills: {missing}")

    def list_names(self) -> tuple[str, ...]:
        self.directory.discover()
        all_skills = self.directory.list_skills()
        return tuple(sorted(s.metadata.name for s in all_skills))

    def get_instructions(self, skill_names: tuple[str, ...]) -> str:
        all_skills = self.directory.list_skills()
        results = []
        for name in skill_names:
            skill = next((s for s in all_skills if s.metadata.name == name), None)
            if skill and skill.metadata.instructions:
                results.append(f"## Skill: {name}\n{skill.metadata.instructions}")
        return "\n\n".join(results)

    async def list_skills(self, ctx: ToolContext) -> JsonObject:
        return await execute_tool(
            ctx,
            tool_name="list_skills",
            args_summary={},
            action=lambda: [
                _skill_metadata_to_json(s.metadata)
                for s in self.directory.list_skills()
            ],
        )

    async def load_skill(self, ctx: ToolContext, name: str) -> JsonObject:
        async def _action() -> JsonValue:
            skill = self.directory.get_skill(name)
            if not skill:
                raise KeyError(f"Skill not found: {name}")
            return _skill_metadata_to_json(skill.metadata)

        return await execute_tool(
            ctx, tool_name="load_skill", args_summary={"name": name}, action=_action
        )

    async def read_skill_resource(
        self, ctx: ToolContext, skill_name: str, resource_path: str
    ) -> JsonObject:
        async def _action() -> JsonValue:
            skill = self.directory.get_skill(skill_name)
            if not skill:
                raise KeyError(f"Skill not found: {skill_name}")
            # Resources dictionary contains SkillResource objects
            resource = skill.metadata.resources.get(resource_path)
            if not resource or not resource.path:
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
            skill = self.directory.get_skill(skill_name)
            if not skill:
                raise KeyError(f"Skill not found: {skill_name}")

            script = skill.metadata.scripts.get(script_name)
            if not script:
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

            # Use 'run' if available, otherwise 'main'
            if hasattr(module, "run"):
                run_fn = getattr(module, "run")
            elif hasattr(module, "main"):
                run_fn = getattr(module, "main")
            else:
                raise AttributeError(
                    f"Script {script_name} in skill {skill_name} has no 'run' or 'main' function"
                )

            f = io.StringIO()
            with redirect_stdout(f):
                # Handle both async and sync
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

            output = f.getvalue().strip()
            if output:
                return output
            return _normalize_script_result(ret)

        return await execute_tool(
            ctx,
            tool_name=f"skill:{skill_name}:{script_name}",
            args_summary=args or {},
            action=_action,
        )


def _skill_metadata_to_json(metadata: SkillMetadata) -> JsonObject:
    return {
        "name": metadata.name,
        "description": metadata.description,
        "instructions": metadata.instructions,
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
