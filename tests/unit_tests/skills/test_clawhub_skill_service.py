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
    assert saved.ref == "skill-creator"
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


def test_save_skill_restores_backup_when_reload_callback_fails(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    existing_skill_dir = config_dir / "skills" / "skill-creator-2"
    existing_skill_dir.mkdir(parents=True)
    original_manifest = (
        "---\n"
        "name: skill-creator\n"
        "description: Original skill.\n"
        "---\n"
        "Keep the original skill.\n"
    )
    (existing_skill_dir / "SKILL.md").write_text(original_manifest, encoding="utf-8")

    service = ClawHubSkillService(
        config_dir=config_dir,
        on_skill_mutated=lambda: (_ for _ in ()).throw(RuntimeError("reload failed")),
    )

    with pytest.raises(RuntimeError, match="reload failed"):
        service.save_skill(
            "skill-creator-2",
            ClawHubSkillWriteRequest(
                runtime_name="skill-creator",
                description="Updated skill.",
                instructions="Updated skill.",
                files=(),
            ),
        )

    assert (existing_skill_dir / "SKILL.md").read_text(
        encoding="utf-8"
    ) == original_manifest


def test_delete_skill_rejects_symlink_outside_managed_root(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    external_skill_dir = tmp_path / "external-skill"
    external_skill_dir.mkdir(parents=True)
    (external_skill_dir / "SKILL.md").write_text(
        "---\nname: external-skill\ndescription: external\n---\nexternal\n",
        encoding="utf-8",
    )
    linked_skill_dir = config_dir / "skills" / "linked-skill"
    linked_skill_dir.parent.mkdir(parents=True)
    try:
        linked_skill_dir.symlink_to(external_skill_dir, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Creating directory symlinks requires elevated privileges")
        raise
    service = ClawHubSkillService(config_dir=config_dir)

    with pytest.raises(
        ValueError, match="ClawHub skill path escapes the managed skills root"
    ):
        service.delete_skill("linked-skill")

    assert external_skill_dir.exists()
