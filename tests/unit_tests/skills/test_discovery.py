# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.skills import discovery
from agent_teams.skills.discovery import SkillsDirectory


def test_get_user_skills_dir_uses_user_config_dir_when_home_not_provided(
    monkeypatch,
) -> None:
    app_config_dir = Path("D:/home/.config/agent-teams").resolve()
    monkeypatch.setattr(
        discovery, "get_app_config_dir", lambda **kwargs: app_config_dir
    )

    skills_dir = discovery.get_user_skills_dir()

    assert skills_dir == app_config_dir / "skills"


def test_get_user_skills_dir_uses_user_home_override(monkeypatch) -> None:
    user_home_dir = Path("D:/home").resolve()

    def fake_get_app_config_dir(*, user_home_dir: Path | None = None) -> Path:
        assert user_home_dir is not None
        return user_home_dir / ".config" / "agent-teams"

    monkeypatch.setattr(discovery, "get_app_config_dir", fake_get_app_config_dir)

    skills_dir = discovery.get_user_skills_dir(user_home_dir=user_home_dir)

    assert skills_dir == user_home_dir / ".config" / "agent-teams" / "skills"


def test_get_project_skills_dir_uses_app_config_dir_when_root_not_provided(
    monkeypatch,
) -> None:
    app_config_dir = Path("D:/home/.config/agent-teams").resolve()
    monkeypatch.setattr(
        discovery, "get_app_config_dir", lambda **kwargs: app_config_dir
    )

    skills_dir = discovery.get_project_skills_dir()

    assert skills_dir == app_config_dir / "skills"


def test_get_project_skills_dir_ignores_project_root_and_uses_app_dir(
    monkeypatch,
) -> None:
    app_config_dir = Path("D:/home/.config/agent-teams").resolve()
    monkeypatch.setattr(
        discovery, "get_app_config_dir", lambda **kwargs: app_config_dir
    )

    skills_dir = discovery.get_project_skills_dir(project_root=Path("D:/repo-root"))

    assert skills_dir == app_config_dir / "skills"


def test_skills_directory_from_skill_dirs_creates_app_directory(
    tmp_path: Path,
) -> None:
    app_skills_dir = tmp_path / ".config" / "agent-teams" / "skills"
    builtin_skills_dir = tmp_path / "builtin" / "skills"

    directory = SkillsDirectory.from_skill_dirs(
        app_skills_dir=app_skills_dir,
        builtin_skills_dir=builtin_skills_dir,
    )

    assert app_skills_dir.is_dir()
    assert directory.base_dir == app_skills_dir.resolve()
    assert directory.fallback_dirs == (builtin_skills_dir.resolve(),)


def test_skills_directory_from_config_dirs_uses_app_and_builtin_scopes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_config_dir = tmp_path / ".config" / "agent-teams"
    builtin_skills_dir = tmp_path / "builtin" / "skills"
    monkeypatch.setattr(
        discovery, "get_builtin_skills_dir_path", lambda: builtin_skills_dir
    )

    directory = SkillsDirectory.from_config_dirs(app_config_dir=app_config_dir)

    assert directory.base_dir == (app_config_dir / "skills").resolve()
    assert directory.fallback_dirs == (builtin_skills_dir.resolve(),)


def test_skills_directory_from_default_scopes_uses_resolved_scope_dirs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_skills_dir = tmp_path / "home" / ".config" / "agent-teams" / "skills"
    builtin_skills_dir = tmp_path / "agent-teams" / "builtin" / "skills"
    monkeypatch.setattr(
        discovery, "get_app_skills_dir", lambda **kwargs: app_skills_dir
    )
    monkeypatch.setattr(
        discovery, "get_builtin_skills_dir_path", lambda: builtin_skills_dir
    )

    directory = SkillsDirectory.from_default_scopes()

    assert directory.base_dir == app_skills_dir.resolve()
    assert directory.fallback_dirs == (builtin_skills_dir.resolve(),)
