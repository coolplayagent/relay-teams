# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.logger import get_logger

if TYPE_CHECKING:
    from relay_teams.agents.execution.prompt_instructions import (
        LoadedPromptInstructions,
        PromptInstructionResolver,
    )
    from relay_teams.agent_runtimes.models import ExternalAgentConfig
    from relay_teams.agent_runtimes.skill_bridge import SkillBridgeManifest
    from relay_teams.roles.role_models import RoleDefinition

LOGGER = get_logger(__name__)

_PROVIDER_FILE_MAP: dict[str, str] = {
    "anthropic": "CLAUDE.md",
    "google": "GEMINI.md",
    "openai": "AGENTS.md",
}
_DEFAULT_PROVIDER_FILE = "AGENTS.md"
_CONFIG_SUBDIR = ".relay-teams/external"


class NativeConfigSpec(BaseModel):
    """Describes a generated native configuration."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    config_dir: Path
    provider: str
    files: tuple[str, ...] = ()
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
    )


class NativeConfigContent(BaseModel):
    """Content parts for native config assembly."""

    model_config = ConfigDict(extra="forbid")

    project_instructions: str = ""
    role_prompt: str = ""
    task_objective: str = ""
    skill_references: str = ""
    workspace_context: str = ""


def resolve_native_config_filename(provider: str) -> str:
    """Return the config file name for the given provider."""
    return _PROVIDER_FILE_MAP.get(provider.strip().lower(), _DEFAULT_PROVIDER_FILE)


def assemble_native_config_content(content: NativeConfigContent) -> str:
    """Assemble native config content from parts."""
    parts: list[str] = []
    if content.project_instructions.strip():
        parts.append(content.project_instructions.strip())
    if content.role_prompt.strip():
        parts.append(f"## Role Definition\n\n{content.role_prompt.strip()}")
    if content.task_objective.strip():
        parts.append(f"## Task Objective\n\n{content.task_objective.strip()}")
    if content.skill_references.strip():
        parts.append(content.skill_references.strip())
    if content.workspace_context.strip():
        parts.append(f"## Workspace\n\n{content.workspace_context.strip()}")
    return "\n\n".join(parts)


class NativeConfigGenerator:
    """Generate provider-native configuration directories for external agents."""

    def __init__(
        self,
        *,
        instruction_resolver: PromptInstructionResolver,
    ) -> None:
        self._instruction_resolver = instruction_resolver

    async def generate(
        self,
        *,
        agent: ExternalAgentConfig,
        workspace_path: Path,
        role: RoleDefinition,
        task_objective: str,
        skill_bridge_manifest: SkillBridgeManifest | None = None,
    ) -> NativeConfigSpec:
        """Generate a temporary native config directory for an external agent."""
        config_dir = workspace_path / _CONFIG_SUBDIR / agent.agent_id
        config_dir.mkdir(parents=True, exist_ok=True)

        loaded = await self._load_project_instructions(workspace_path)
        filename = resolve_native_config_filename(agent.native_config_provider)
        skill_refs = self._build_skill_reference_text(skill_bridge_manifest)

        content = NativeConfigContent(
            project_instructions="\n\n".join(loaded.sections)
            if loaded.sections
            else "",
            role_prompt=role.system_prompt,
            task_objective=task_objective,
            skill_references=skill_refs,
            workspace_context=str(workspace_path),
        )
        assembled = assemble_native_config_content(content)
        target_file = config_dir / filename
        target_file.write_text(assembled, encoding="utf-8")

        LOGGER.info(
            "Generated native config for agent %s at %s",
            agent.agent_id,
            target_file,
        )

        return NativeConfigSpec(
            config_dir=config_dir,
            provider=agent.native_config_provider,
            files=(filename,),
        )

    async def _load_project_instructions(
        self,
        workspace_path: Path,
    ) -> LoadedPromptInstructions:
        return await self._instruction_resolver.load_initial_instructions(
            working_directory=workspace_path,
            worktree_root=None,
        )

    @staticmethod
    def _build_skill_reference_text(
        manifest: SkillBridgeManifest | None,
    ) -> str:
        if manifest is None or not manifest.skills:
            return ""
        lines = ["## Available Skills (via relay-teams Skill Bridge)", ""]
        for skill in manifest.skills:
            lines.append(f"- **{skill.name}**: {skill.description}")
            if skill.usage_example:
                lines.append(f"  Usage: {skill.usage_example}")
        return "\n".join(lines)

    @staticmethod
    def cleanup(config_dir: Path) -> None:
        """Remove generated config directory."""
        if config_dir.exists():
            for child in config_dir.iterdir():
                child.unlink(missing_ok=True)
            config_dir.rmdir()
            LOGGER.info("Cleaned up native config dir %s", config_dir)
