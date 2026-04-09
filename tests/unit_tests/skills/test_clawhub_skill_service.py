# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.skills.clawhub_models import ClawHubSkillWriteRequest
from relay_teams.skills.clawhub_skill_service import ClawHubSkillService


def test_save_get_and_delete_skill(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    mutation_count = 0

    def on_skill_mutated() -> None:
        nonlocal mutation_count
        mutation_count += 1

    service = ClawHubSkillService(
        config_dir=config_dir,
        on_skill_mutated=on_skill_mutated,
    )

    saved = service.save_skill(
        "skill-creator-2",
        ClawHubSkillWriteRequest(
            runtime_name="skill-creator",
            description="Create skills.",
            instructions="Create skills safely.",
            files=(),
        ),
    )

    assert saved.skill_id == "skill-creator-2"
    assert saved.runtime_name == "skill-creator"
    assert saved.ref == "app:skill-creator"
    assert saved.valid is True
    assert mutation_count == 1

    loaded = service.get_skill("skill-creator-2")

    assert loaded.skill_id == "skill-creator-2"
    assert loaded.runtime_name == "skill-creator"
    assert loaded.instructions == "Create skills safely."

    service.delete_skill("skill-creator-2")

    assert mutation_count == 2
    with pytest.raises(KeyError, match="Unknown ClawHub skill"):
        service.get_skill("skill-creator-2")


def test_save_skill_rejects_duplicate_runtime_name(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    service = ClawHubSkillService(config_dir=config_dir)

    _ = service.save_skill(
        "skill-creator-2",
        ClawHubSkillWriteRequest(
            runtime_name="skill-creator",
            description="Create skills.",
            instructions="Create skills safely.",
            files=(),
        ),
    )

    with pytest.raises(ValueError, match="Duplicate app skill runtime name"):
        service.save_skill(
            "skill-creator-copy",
            ClawHubSkillWriteRequest(
                runtime_name="skill-creator",
                description="Create skills.",
                instructions="Create skills safely.",
                files=(),
            ),
        )


def test_get_skill_returns_binary_files_as_base64(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    service = ClawHubSkillService(config_dir=config_dir)
    skill_dir = config_dir / "skills" / "binary-demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: binary-demo\ndescription: demo\n---\nUse carefully.\n",
        encoding="utf-8",
    )
    (skill_dir / "icon.bin").write_bytes(b"\x89PNG\x00\x01")

    detail = service.get_skill("binary-demo")

    assert len(detail.files) == 1
    assert detail.files[0].path == "icon.bin"
    assert detail.files[0].encoding == "base64"


def test_list_skills_surfaces_invalid_skill_manifest(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    skill_dir = config_dir / "skills" / "broken"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("name: broken\n", encoding="utf-8")
    service = ClawHubSkillService(config_dir=config_dir)

    listed = service.list_skills()

    assert len(listed) == 1
    assert listed[0].skill_id == "broken"
    assert listed[0].valid is False
    assert listed[0].error == "SKILL.md must start with YAML front matter"
