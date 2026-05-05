# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.logger import get_logger

if TYPE_CHECKING:
    from relay_teams.skills.skill_registry import SkillRegistry
    from relay_teams.skills.skill_routing_service import SkillRuntimeService

LOGGER = get_logger(__name__)


class BridgedSkill(BaseModel):
    """A single skill exposed through the bridge."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str
    category: str = ""
    instruction_path: str = ""
    tool_names: tuple[str, ...] = ()
    usage_example: str = ""


class SkillBridgeManifest(BaseModel):
    """Manifest of skills bridged to an external agent."""

    model_config = ConfigDict(extra="forbid")

    skills: tuple[BridgedSkill, ...] = ()
    mode: str = "inline"


class SkillBridgeService:
    """Bridge relay-teams skills to external agents."""

    def __init__(
        self,
        *,
        skill_registry: SkillRegistry,
        skill_runtime_service: SkillRuntimeService | None = None,
    ) -> None:
        self._skill_registry = skill_registry
        self._skill_runtime_service = skill_runtime_service

    def build_manifest(
        self,
        *,
        allowed_skills: tuple[str, ...] = (),
        mode: Literal["inline", "directory"] = "inline",
    ) -> SkillBridgeManifest:
        """Build a manifest of bridgeable skills.

        If *allowed_skills* is empty, expose all registered skills.
        Only skills whose ``exposed_to_external`` attribute is truthy or
        whose metadata is non-empty are included.  When *allowed_skills*
        is non-empty, only those named skills (after resolution) are kept.
        """
        all_skills = self._skill_registry.list_skill_definitions()

        if allowed_skills:
            resolved = self._skill_registry.resolve_known(
                allowed_skills,
                strict=False,
                consumer="skill_bridge",
            )
            filtered = [s for s in all_skills if s.metadata.name in resolved]
        else:
            filtered = list(all_skills)

        bridged: list[BridgedSkill] = []
        for skill in filtered:
            meta = skill.metadata
            instructions = meta.instructions.strip()
            if not instructions and not meta.description.strip():
                continue
            bridged.append(
                BridgedSkill(
                    name=meta.name,
                    description=meta.description.strip(),
                    category=skill.source.value,
                    instruction_path="",
                    tool_names=(),
                    usage_example="",
                )
            )

        return SkillBridgeManifest(skills=tuple(bridged), mode=mode)

    def build_inline_reference(self, manifest: SkillBridgeManifest) -> str:
        """Generate a text block of skill references for injection into prompts."""
        if not manifest.skills:
            return ""
        lines = ["## Available Skills (via relay-teams Skill Bridge)", ""]
        for skill in manifest.skills:
            lines.append(f"- **{skill.name}**: {skill.description}")
            if skill.usage_example:
                lines.append(f"  Usage: {skill.usage_example}")
        return "\n".join(lines)

    def populate_config_directory(
        self,
        manifest: SkillBridgeManifest,
        target_dir: Path,
    ) -> None:
        """In directory mode, copy skill instruction files into *target_dir*."""
        target_dir.mkdir(parents=True, exist_ok=True)
        for skill in manifest.skills:
            if not skill.instruction_path:
                continue
            src = Path(skill.instruction_path)
            if not src.exists():
                LOGGER.warning(
                    "Skill instruction path does not exist: %s",
                    skill.instruction_path,
                )
                continue
            dest = target_dir / f"skill_{skill.name}.md"
            shutil.copy2(src, dest)
            LOGGER.info("Copied skill %s to %s", skill.name, dest)
