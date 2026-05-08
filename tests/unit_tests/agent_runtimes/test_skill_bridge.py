# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from relay_teams.agent_runtimes.skill_bridge import (
    BridgedSkill,
    SkillBridgeManifest,
    SkillBridgeService,
)
from relay_teams.skills.skill_models import SkillSource


def _make_registry_stub(
    skills: tuple = (),
    resolve_result: tuple[str, ...] = (),
) -> MagicMock:
    """Create a lightweight stub that satisfies SkillBridgeService."""
    registry = MagicMock()
    registry.list_skill_definitions.return_value = skills
    registry.resolve_known.return_value = resolve_result
    return registry


# ---------------------------------------------------------------------------
# build_manifest
# ---------------------------------------------------------------------------


def test_build_manifest_empty_registry() -> None:
    registry = _make_registry_stub()
    svc = SkillBridgeService(skill_registry=registry)
    manifest = svc.build_manifest()
    assert manifest.skills == ()


def test_build_manifest_all_skills() -> None:
    from relay_teams.skills.skill_models import Skill, SkillMetadata

    skill = Skill(
        ref="web-search",
        metadata=SkillMetadata(
            name="web-search",
            description="Search the web",
            instructions="Use for research",
        ),
        directory=Path("/nonexistent"),
        source=SkillSource.BUILTIN,
    )
    registry = _make_registry_stub(skills=(skill,))
    svc = SkillBridgeService(skill_registry=registry)
    manifest = svc.build_manifest()
    assert len(manifest.skills) == 1
    assert manifest.skills[0].name == "web-search"
    assert manifest.skills[0].description == "Search the web"


def test_build_manifest_filters_by_allowed_skills() -> None:
    from relay_teams.skills.skill_models import Skill, SkillMetadata

    s1 = Skill(
        ref="web-search",
        metadata=SkillMetadata(
            name="web-search", description="Search", instructions="Search the web"
        ),
        directory=Path("/nonexistent"),
        source=SkillSource.BUILTIN,
    )
    s2 = Skill(
        ref="code-review",
        metadata=SkillMetadata(
            name="code-review", description="Review", instructions="Review code"
        ),
        directory=Path("/nonexistent"),
        source=SkillSource.BUILTIN,
    )
    registry = _make_registry_stub(
        skills=(s1, s2),
        resolve_result=("web-search",),
    )
    svc = SkillBridgeService(skill_registry=registry)
    manifest = svc.build_manifest(allowed_skills=("web-search",))
    assert len(manifest.skills) == 1
    assert manifest.skills[0].name == "web-search"


def test_build_manifest_omits_empty_description_and_instructions() -> None:
    from relay_teams.skills.skill_models import Skill, SkillMetadata

    empty_skill = Skill(
        ref="empty",
        metadata=SkillMetadata(name="empty", description="", instructions=""),
        directory=Path("/nonexistent"),
        source=SkillSource.BUILTIN,
    )
    registry = _make_registry_stub(skills=(empty_skill,))
    svc = SkillBridgeService(skill_registry=registry)
    manifest = svc.build_manifest()
    assert manifest.skills == ()


# ---------------------------------------------------------------------------
# build_inline_reference
# ---------------------------------------------------------------------------


def test_build_inline_reference_empty_manifest() -> None:
    registry = _make_registry_stub()
    svc = SkillBridgeService(skill_registry=registry)
    assert svc.build_inline_reference(SkillBridgeManifest()) == ""


def test_build_inline_reference_formats_skills() -> None:
    registry = _make_registry_stub()
    svc = SkillBridgeService(skill_registry=registry)
    manifest = SkillBridgeManifest(
        skills=(
            BridgedSkill(
                name="code-review",
                description="Review code quality",
                usage_example="After generating code",
            ),
        ),
    )
    result = svc.build_inline_reference(manifest)
    assert "## Available Skills (via relay-teams Skill Bridge)" in result
    assert "**code-review**" in result
    assert "Review code quality" in result
    assert "Usage: After generating code" in result


# ---------------------------------------------------------------------------
# populate_config_directory
# ---------------------------------------------------------------------------


def test_populate_config_directory_copies_files(tmp_path: Path) -> None:
    registry = _make_registry_stub()
    # Create a source instruction file
    src_dir = tmp_path / "src_skills" / "web-search"
    src_dir.mkdir(parents=True)
    instruction_file = src_dir / "instructions.md"
    instruction_file.write_text("# Web Search\nUse for research.", encoding="utf-8")

    manifest = SkillBridgeManifest(
        skills=(
            BridgedSkill(
                name="web-search",
                description="Search the web",
                instruction_path=str(instruction_file),
            ),
        ),
    )
    target = tmp_path / "output"
    svc = SkillBridgeService(skill_registry=registry)
    svc.populate_config_directory(manifest, target)
    assert (target / "skill_web-search.md").exists()
    assert "Web Search" in (target / "skill_web-search.md").read_text()


def test_populate_config_directory_skips_missing_path(
    tmp_path: Path,
) -> None:
    registry = _make_registry_stub()
    manifest = SkillBridgeManifest(
        skills=(
            BridgedSkill(
                name="missing",
                description="gone",
                instruction_path="/nonexistent/file.md",
            ),
        ),
    )
    target = tmp_path / "output"
    svc = SkillBridgeService(skill_registry=registry)
    svc.populate_config_directory(manifest, target)
    assert list(target.iterdir()) == []


def test_populate_config_directory_skips_empty_instruction_path(
    tmp_path: Path,
) -> None:
    registry = _make_registry_stub()
    manifest = SkillBridgeManifest(
        skills=(
            BridgedSkill(
                name="no-path",
                description="no path given",
                instruction_path="",
            ),
        ),
    )
    target = tmp_path / "output"
    svc = SkillBridgeService(skill_registry=registry)
    svc.populate_config_directory(manifest, target)
    # Directory was created but no files
    assert target.exists()
    assert list(target.iterdir()) == []
