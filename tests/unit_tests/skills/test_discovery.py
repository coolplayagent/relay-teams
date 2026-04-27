# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.hooks.hook_models import HookEventName
from relay_teams.hooks.hook_models import HooksConfig
from relay_teams.skills import discovery
from relay_teams.skills.discovery import SkillsDirectory, _parse_frontmatter_hooks
from relay_teams.skills.skill_models import SkillSource


def test_get_user_skills_dir_uses_user_config_dir_when_home_not_provided(
    monkeypatch,
) -> None:
    app_config_dir = Path("D:/home/.agent-teams").resolve()
    monkeypatch.setattr(
        discovery, "get_app_config_dir", lambda **kwargs: app_config_dir
    )

    skills_dir = discovery.get_user_skills_dir()

    assert skills_dir == app_config_dir / "skills"


def test_get_user_skills_dir_uses_user_home_override(monkeypatch) -> None:
    user_home_dir = Path("D:/home").resolve()

    def fake_get_app_config_dir(*, user_home_dir: Path | None = None) -> Path:
        assert user_home_dir is not None
        return user_home_dir / ".agent-teams"

    monkeypatch.setattr(discovery, "get_app_config_dir", fake_get_app_config_dir)

    skills_dir = discovery.get_user_skills_dir(user_home_dir=user_home_dir)

    assert skills_dir == user_home_dir / ".agent-teams" / "skills"


def test_get_agents_skills_dir_uses_sibling_agents_directory(monkeypatch) -> None:
    app_config_dir = Path("D:/home/.relay-teams").resolve()
    monkeypatch.setattr(
        discovery, "get_app_config_dir", lambda **kwargs: app_config_dir
    )

    skills_dir = discovery.get_agents_skills_dir()

    assert skills_dir == app_config_dir.parent / ".agents" / "skills"


def test_get_compatible_user_skills_dirs_use_user_config_siblings(monkeypatch) -> None:
    app_config_dir = Path("D:/home/.relay-teams").resolve()
    monkeypatch.setattr(
        discovery, "get_app_config_dir", lambda **kwargs: app_config_dir
    )

    assert (
        discovery.get_codex_skills_dir() == app_config_dir.parent / ".codex" / "skills"
    )
    assert (
        discovery.get_claude_skills_dir()
        == app_config_dir.parent / ".claude" / "skills"
    )
    assert (
        discovery.get_opencode_skills_dir()
        == app_config_dir.parent / ".config" / "opencode" / "skills"
    )


def test_get_project_skills_dir_uses_project_root_when_provided() -> None:
    project_root = Path("D:/repo-root").resolve()

    skills_dir = discovery.get_project_skills_dir(project_root=project_root)

    assert skills_dir == project_root / ".relay-teams" / "skills"


def test_get_project_skills_dir_defaults_to_current_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    skills_dir = discovery.get_project_skills_dir()

    assert skills_dir == (project_root / ".relay-teams" / "skills").resolve()


def test_skills_directory_from_skill_dirs_uses_builtin_then_user_sources(
    tmp_path: Path,
) -> None:
    user_skills_dir = tmp_path / ".agent-teams" / "skills"
    builtin_skills_dir = tmp_path / "builtin" / "skills"

    directory = SkillsDirectory.from_skill_dirs(
        app_skills_dir=user_skills_dir,
        builtin_skills_dir=builtin_skills_dir,
    )

    assert directory.sources == (
        (SkillSource.BUILTIN, builtin_skills_dir.resolve()),
        (SkillSource.USER_RELAY_TEAMS, user_skills_dir.resolve()),
    )


def test_skills_directory_from_config_dirs_uses_builtin_user_and_agents_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_config_dir = tmp_path / ".relay-teams"
    builtin_skills_dir = tmp_path / "builtin" / "skills"
    monkeypatch.setattr(
        discovery, "get_builtin_skills_dir_path", lambda: builtin_skills_dir
    )

    directory = SkillsDirectory.from_config_dirs(app_config_dir=app_config_dir)

    assert directory.sources == (
        (SkillSource.BUILTIN, builtin_skills_dir.resolve()),
        (
            SkillSource.USER_CODEX,
            (app_config_dir.parent / ".codex" / "skills").resolve(),
        ),
        (
            SkillSource.USER_CLAUDE,
            (app_config_dir.parent / ".claude" / "skills").resolve(),
        ),
        (
            SkillSource.USER_OPENCODE,
            (app_config_dir.parent / ".config" / "opencode" / "skills").resolve(),
        ),
        (SkillSource.USER_RELAY_TEAMS, (app_config_dir / "skills").resolve()),
        (
            SkillSource.USER_AGENTS,
            (app_config_dir.parent / ".agents" / "skills").resolve(),
        ),
    )


def test_skills_directory_from_default_scopes_builds_ordered_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    builtin_skills_dir = tmp_path / "builtin" / "skills"
    user_codex_skills_dir = tmp_path / "home" / ".codex" / "skills"
    user_claude_skills_dir = tmp_path / "home" / ".claude" / "skills"
    user_opencode_skills_dir = tmp_path / "home" / ".config" / "opencode" / "skills"
    user_relay_teams_skills_dir = tmp_path / "home" / ".relay-teams" / "skills"
    user_agents_skills_dir = tmp_path / "home" / ".agents" / "skills"
    project_root = tmp_path / "repo"
    start_dir = project_root / "nested" / "deeper"
    start_dir.mkdir(parents=True)
    monkeypatch.setattr(
        discovery, "get_builtin_skills_dir_path", lambda: builtin_skills_dir
    )
    monkeypatch.setattr(
        discovery, "get_codex_skills_dir", lambda **kwargs: user_codex_skills_dir
    )
    monkeypatch.setattr(
        discovery, "get_claude_skills_dir", lambda **kwargs: user_claude_skills_dir
    )
    monkeypatch.setattr(
        discovery,
        "get_opencode_skills_dir",
        lambda **kwargs: user_opencode_skills_dir,
    )
    monkeypatch.setattr(
        discovery, "get_app_skills_dir", lambda **kwargs: user_relay_teams_skills_dir
    )
    monkeypatch.setattr(
        discovery, "get_agents_skills_dir", lambda **kwargs: user_agents_skills_dir
    )
    monkeypatch.setattr(
        discovery,
        "get_project_root_or_none",
        lambda start_dir=None: project_root.resolve(),
    )

    directory = SkillsDirectory.from_default_scopes(start_dir=start_dir)

    assert directory.sources == (
        (SkillSource.BUILTIN, builtin_skills_dir.resolve()),
        (SkillSource.USER_CODEX, user_codex_skills_dir.resolve()),
        (SkillSource.USER_CLAUDE, user_claude_skills_dir.resolve()),
        (SkillSource.USER_OPENCODE, user_opencode_skills_dir.resolve()),
        (SkillSource.USER_RELAY_TEAMS, user_relay_teams_skills_dir.resolve()),
        (SkillSource.USER_AGENTS, user_agents_skills_dir.resolve()),
        (
            SkillSource.PROJECT_CODEX,
            (start_dir / ".codex" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_CODEX,
            (start_dir.parent / ".codex" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_CODEX,
            (project_root / ".codex" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_CLAUDE,
            (start_dir / ".claude" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_CLAUDE,
            (start_dir.parent / ".claude" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_CLAUDE,
            (project_root / ".claude" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_OPENCODE,
            (start_dir / ".opencode" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_OPENCODE,
            (start_dir.parent / ".opencode" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_OPENCODE,
            (project_root / ".opencode" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_RELAY_TEAMS,
            (start_dir / ".relay-teams" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_RELAY_TEAMS,
            (start_dir.parent / ".relay-teams" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_RELAY_TEAMS,
            (project_root / ".relay-teams" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_AGENTS,
            (start_dir / ".agents" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_AGENTS,
            (start_dir.parent / ".agents" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_AGENTS,
            (project_root / ".agents" / "skills").resolve(),
        ),
    )


def test_skills_directory_from_default_scopes_omits_project_sources_without_start_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    builtin_skills_dir = tmp_path / "builtin" / "skills"
    user_codex_skills_dir = tmp_path / "home" / ".codex" / "skills"
    user_claude_skills_dir = tmp_path / "home" / ".claude" / "skills"
    user_opencode_skills_dir = tmp_path / "home" / ".config" / "opencode" / "skills"
    user_relay_teams_skills_dir = tmp_path / "home" / ".relay-teams" / "skills"
    user_agents_skills_dir = tmp_path / "home" / ".agents" / "skills"
    monkeypatch.setattr(
        discovery, "get_builtin_skills_dir_path", lambda: builtin_skills_dir
    )
    monkeypatch.setattr(
        discovery, "get_codex_skills_dir", lambda **kwargs: user_codex_skills_dir
    )
    monkeypatch.setattr(
        discovery, "get_claude_skills_dir", lambda **kwargs: user_claude_skills_dir
    )
    monkeypatch.setattr(
        discovery,
        "get_opencode_skills_dir",
        lambda **kwargs: user_opencode_skills_dir,
    )
    monkeypatch.setattr(
        discovery, "get_app_skills_dir", lambda **kwargs: user_relay_teams_skills_dir
    )
    monkeypatch.setattr(
        discovery, "get_agents_skills_dir", lambda **kwargs: user_agents_skills_dir
    )

    directory = SkillsDirectory.from_default_scopes()

    assert directory.sources == (
        (SkillSource.BUILTIN, builtin_skills_dir.resolve()),
        (SkillSource.USER_CODEX, user_codex_skills_dir.resolve()),
        (SkillSource.USER_CLAUDE, user_claude_skills_dir.resolve()),
        (SkillSource.USER_OPENCODE, user_opencode_skills_dir.resolve()),
        (SkillSource.USER_RELAY_TEAMS, user_relay_teams_skills_dir.resolve()),
        (SkillSource.USER_AGENTS, user_agents_skills_dir.resolve()),
    )


def test_project_skill_sources_stop_at_start_dir_when_project_root_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    start_dir = tmp_path / "repo" / "nested"
    start_dir.mkdir(parents=True)
    monkeypatch.setattr(
        discovery,
        "get_project_root_or_none",
        lambda start_dir=None: None,
    )

    sources = discovery._project_skill_sources(start_dir=start_dir)

    assert sources == (
        (
            SkillSource.PROJECT_CODEX,
            (start_dir / ".codex" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_CLAUDE,
            (start_dir / ".claude" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_OPENCODE,
            (start_dir / ".opencode" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_RELAY_TEAMS,
            (start_dir / ".relay-teams" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_AGENTS,
            (start_dir / ".agents" / "skills").resolve(),
        ),
    )


def test_project_skill_sources_use_file_parent_and_include_project_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "repo"
    manifest_path = project_root / "nested" / "task.txt"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("task\n", encoding="utf-8")
    monkeypatch.setattr(
        discovery,
        "get_project_root_or_none",
        lambda start_dir=None: project_root.resolve(),
    )

    sources = discovery._project_skill_sources(start_dir=manifest_path)

    assert sources == (
        (
            SkillSource.PROJECT_CODEX,
            (manifest_path.parent / ".codex" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_CODEX,
            (project_root / ".codex" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_CLAUDE,
            (manifest_path.parent / ".claude" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_CLAUDE,
            (project_root / ".claude" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_OPENCODE,
            (manifest_path.parent / ".opencode" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_OPENCODE,
            (project_root / ".opencode" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_RELAY_TEAMS,
            (manifest_path.parent / ".relay-teams" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_RELAY_TEAMS,
            (project_root / ".relay-teams" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_AGENTS,
            (manifest_path.parent / ".agents" / "skills").resolve(),
        ),
        (
            SkillSource.PROJECT_AGENTS,
            (project_root / ".agents" / "skills").resolve(),
        ),
    )


def test_discover_uses_later_sources_to_override_duplicate_names(
    tmp_path: Path,
) -> None:
    builtin_dir = tmp_path / "builtin" / "skills"
    user_dir = tmp_path / "home" / ".relay-teams" / "skills"
    project_agents_dir = tmp_path / "repo" / ".agents" / "skills"
    _write_skill(
        builtin_dir / "shared",
        name="shared",
        description="builtin shared skill",
        instructions="Builtin instructions.",
    )
    _write_skill(
        user_dir / "shared",
        name="shared",
        description="user shared skill",
        instructions="User instructions.",
    )
    _write_skill(
        project_agents_dir / "shared",
        name="shared",
        description="project agents shared skill",
        instructions="Project agents instructions.",
    )
    _write_skill(
        builtin_dir / "builtin_only",
        name="builtin_only",
        description="builtin only skill",
        instructions="Builtin only.",
    )

    directory = SkillsDirectory(
        sources=(
            (SkillSource.BUILTIN, builtin_dir),
            (SkillSource.USER_RELAY_TEAMS, user_dir),
            (SkillSource.PROJECT_AGENTS, project_agents_dir),
        )
    )

    directory.discover()

    shared = directory.get_skill("shared")
    builtin_only = directory.get_skill("builtin_only")

    assert shared is not None
    assert shared.source == SkillSource.PROJECT_AGENTS
    assert shared.metadata.description == "project agents shared skill"
    assert shared.ref == "shared"
    assert builtin_only is not None
    assert builtin_only.source == SkillSource.BUILTIN
    assert {skill.ref for skill in directory.list_skills()} == {
        "shared",
        "builtin_only",
    }


def test_default_scope_discovery_prefers_project_and_native_skill_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    builtin_skills_dir = tmp_path / "builtin" / "skills"
    user_codex_skills_dir = tmp_path / "home" / ".codex" / "skills"
    user_claude_skills_dir = tmp_path / "home" / ".claude" / "skills"
    user_opencode_skills_dir = tmp_path / "home" / ".config" / "opencode" / "skills"
    user_relay_teams_skills_dir = tmp_path / "home" / ".relay-teams" / "skills"
    user_agents_skills_dir = tmp_path / "home" / ".agents" / "skills"
    project_root = tmp_path / "repo"
    project_root.mkdir()
    monkeypatch.setattr(
        discovery, "get_builtin_skills_dir_path", lambda: builtin_skills_dir
    )
    monkeypatch.setattr(
        discovery, "get_codex_skills_dir", lambda **kwargs: user_codex_skills_dir
    )
    monkeypatch.setattr(
        discovery, "get_claude_skills_dir", lambda **kwargs: user_claude_skills_dir
    )
    monkeypatch.setattr(
        discovery,
        "get_opencode_skills_dir",
        lambda **kwargs: user_opencode_skills_dir,
    )
    monkeypatch.setattr(
        discovery, "get_app_skills_dir", lambda **kwargs: user_relay_teams_skills_dir
    )
    monkeypatch.setattr(
        discovery, "get_agents_skills_dir", lambda **kwargs: user_agents_skills_dir
    )
    monkeypatch.setattr(
        discovery,
        "get_project_root_or_none",
        lambda start_dir=None: project_root.resolve(),
    )
    _write_skill(
        builtin_skills_dir / "shared",
        name="shared",
        description="builtin shared skill",
        instructions="Use the builtin shared skill.",
    )
    _write_skill(
        user_codex_skills_dir / "shared",
        name="shared",
        description="user codex shared skill",
        instructions="Use the user codex shared skill.",
    )
    _write_skill(
        user_claude_skills_dir / "shared",
        name="shared",
        description="user claude shared skill",
        instructions="Use the user claude shared skill.",
    )
    _write_skill(
        user_opencode_skills_dir / "shared",
        name="shared",
        description="user opencode shared skill",
        instructions="Use the user opencode shared skill.",
    )
    _write_skill(
        user_relay_teams_skills_dir / "shared",
        name="shared",
        description="user relay-teams shared skill",
        instructions="Use the user relay-teams shared skill.",
    )
    _write_skill(
        user_agents_skills_dir / "shared",
        name="shared",
        description="user agents shared skill",
        instructions="Use the user agents shared skill.",
    )
    _write_skill(
        project_root / ".codex" / "skills" / "shared",
        name="shared",
        description="project codex shared skill",
        instructions="Use the project codex shared skill.",
    )
    _write_skill(
        project_root / ".claude" / "skills" / "shared",
        name="shared",
        description="project claude shared skill",
        instructions="Use the project claude shared skill.",
    )
    _write_skill(
        project_root / ".opencode" / "skills" / "shared",
        name="shared",
        description="project opencode shared skill",
        instructions="Use the project opencode shared skill.",
    )
    _write_skill(
        project_root / ".relay-teams" / "skills" / "shared",
        name="shared",
        description="project relay-teams shared skill",
        instructions="Use the project relay-teams shared skill.",
    )
    _write_skill(
        project_root / ".agents" / "skills" / "shared",
        name="shared",
        description="project agents shared skill",
        instructions="Use the project agents shared skill.",
    )

    directory = SkillsDirectory.from_default_scopes(start_dir=project_root)
    directory.discover()

    shared = directory.get_skill("shared")

    assert shared is not None
    assert shared.source == SkillSource.PROJECT_AGENTS
    assert shared.metadata.description == "project agents shared skill"


def test_default_scope_discovery_preserves_source_precedence_across_project_levels(
    tmp_path: Path,
    monkeypatch,
) -> None:
    builtin_skills_dir = tmp_path / "builtin" / "skills"
    user_codex_skills_dir = tmp_path / "home" / ".codex" / "skills"
    user_claude_skills_dir = tmp_path / "home" / ".claude" / "skills"
    user_opencode_skills_dir = tmp_path / "home" / ".config" / "opencode" / "skills"
    user_relay_teams_skills_dir = tmp_path / "home" / ".relay-teams" / "skills"
    user_agents_skills_dir = tmp_path / "home" / ".agents" / "skills"
    project_root = tmp_path / "repo"
    start_dir = project_root / "nested" / "deeper"
    start_dir.mkdir(parents=True)
    monkeypatch.setattr(
        discovery, "get_builtin_skills_dir_path", lambda: builtin_skills_dir
    )
    monkeypatch.setattr(
        discovery, "get_codex_skills_dir", lambda **kwargs: user_codex_skills_dir
    )
    monkeypatch.setattr(
        discovery, "get_claude_skills_dir", lambda **kwargs: user_claude_skills_dir
    )
    monkeypatch.setattr(
        discovery,
        "get_opencode_skills_dir",
        lambda **kwargs: user_opencode_skills_dir,
    )
    monkeypatch.setattr(
        discovery, "get_app_skills_dir", lambda **kwargs: user_relay_teams_skills_dir
    )
    monkeypatch.setattr(
        discovery, "get_agents_skills_dir", lambda **kwargs: user_agents_skills_dir
    )
    monkeypatch.setattr(
        discovery,
        "get_project_root_or_none",
        lambda start_dir=None: project_root.resolve(),
    )
    _write_skill(
        project_root / ".relay-teams" / "skills" / "shared",
        name="shared",
        description="root relay-teams shared skill",
        instructions="Use the root relay-teams version.",
    )
    _write_skill(
        start_dir / ".agents" / "skills" / "shared",
        name="shared",
        description="nested agents shared skill",
        instructions="Use the nested agents version.",
    )

    directory = SkillsDirectory.from_default_scopes(start_dir=start_dir)
    directory.discover()

    shared = directory.get_skill("shared")

    assert shared is not None
    assert shared.source == SkillSource.PROJECT_AGENTS
    assert shared.metadata.description == "nested agents shared skill"


def test_default_scope_discovery_loads_project_tool_compatible_skills(
    tmp_path: Path,
    monkeypatch,
) -> None:
    builtin_skills_dir = tmp_path / "builtin" / "skills"
    user_codex_skills_dir = tmp_path / "home" / ".codex" / "skills"
    user_claude_skills_dir = tmp_path / "home" / ".claude" / "skills"
    user_opencode_skills_dir = tmp_path / "home" / ".config" / "opencode" / "skills"
    user_relay_teams_skills_dir = tmp_path / "home" / ".relay-teams" / "skills"
    user_agents_skills_dir = tmp_path / "home" / ".agents" / "skills"
    project_root = tmp_path / "repo"
    project_root.mkdir()
    monkeypatch.setattr(
        discovery, "get_builtin_skills_dir_path", lambda: builtin_skills_dir
    )
    monkeypatch.setattr(
        discovery, "get_codex_skills_dir", lambda **kwargs: user_codex_skills_dir
    )
    monkeypatch.setattr(
        discovery, "get_claude_skills_dir", lambda **kwargs: user_claude_skills_dir
    )
    monkeypatch.setattr(
        discovery,
        "get_opencode_skills_dir",
        lambda **kwargs: user_opencode_skills_dir,
    )
    monkeypatch.setattr(
        discovery, "get_app_skills_dir", lambda **kwargs: user_relay_teams_skills_dir
    )
    monkeypatch.setattr(
        discovery, "get_agents_skills_dir", lambda **kwargs: user_agents_skills_dir
    )
    monkeypatch.setattr(
        discovery,
        "get_project_root_or_none",
        lambda start_dir=None: project_root.resolve(),
    )
    _write_skill(
        project_root / ".claude" / "skills" / "claude-plan",
        name="claude-plan",
        description="Claude-compatible plan skill",
        instructions="Use Claude-compatible planning.",
    )
    _write_skill(
        project_root / ".codex" / "skills" / "codex-plan",
        name="codex-plan",
        description="Codex-compatible plan skill",
        instructions="Use Codex-compatible planning.",
    )
    _write_skill(
        project_root / ".opencode" / "skills" / "opencode-plan",
        name="opencode-plan",
        description="OpenCode-compatible plan skill",
        instructions="Use OpenCode-compatible planning.",
    )

    directory = SkillsDirectory.from_default_scopes(start_dir=project_root)
    directory.discover()

    claude_skill = directory.get_skill("claude-plan")
    codex_skill = directory.get_skill("codex-plan")
    opencode_skill = directory.get_skill("opencode-plan")

    assert claude_skill is not None
    assert codex_skill is not None
    assert opencode_skill is not None
    assert claude_skill.source == SkillSource.PROJECT_CLAUDE
    assert codex_skill.source == SkillSource.PROJECT_CODEX
    assert opencode_skill.source == SkillSource.PROJECT_OPENCODE


def test_discover_skips_invalid_and_excessively_nested_skills(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir / "valid",
        name="valid",
        description="valid skill",
        instructions="Use the valid skill.",
    )
    invalid_dir = skills_dir / "invalid"
    invalid_dir.mkdir(parents=True)
    (invalid_dir / "SKILL.md").write_text("name: invalid\n", encoding="utf-8")
    _write_skill(
        skills_dir / "a" / "b" / "c" / "d" / "too-deep",
        name="too-deep",
        description="nested skill",
        instructions="This should be ignored.",
    )
    directory = SkillsDirectory(
        sources=((SkillSource.USER_RELAY_TEAMS, skills_dir),),
        max_depth=3,
    )

    directory.discover()

    assert tuple(skill.ref for skill in directory.list_skills()) == ("valid",)
    assert directory.get_skill("invalid") is None
    assert directory.get_skill("too-deep") is None


def test_discover_keeps_skill_when_one_frontmatter_hook_group_is_empty(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "valid"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: valid\n"
        "description: valid skill\n"
        "hooks:\n"
        "  PreToolUse:\n"
        "    - hooks: []\n"
        "    - matcher: Read\n"
        "      hooks:\n"
        "        - type: command\n"
        "          command: echo ok\n"
        "---\n"
        "Use the valid skill.\n",
        encoding="utf-8",
    )
    directory = SkillsDirectory(
        sources=((SkillSource.USER_RELAY_TEAMS, skills_dir),),
        max_depth=3,
    )

    directory.discover()

    skill = directory.get_skill("valid")
    assert skill is not None
    groups = skill.metadata.hooks.hooks[HookEventName.PRE_TOOL_USE]
    assert len(groups) == 2
    assert groups[0].hooks == ()
    assert groups[1].matcher == "read"
    assert groups[1].hooks[0].command == "echo ok"


def test_discover_normalizes_legacy_frontmatter_hook_fields(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "valid"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: valid\n"
        "description: valid skill\n"
        "hooks:\n"
        "  PreToolUse:\n"
        "    - matcher: '*'\n"
        "      if_condition: Bash(git *)\n"
        "      tool_names:\n"
        "        - Read\n"
        "        - Write\n"
        "      hooks:\n"
        "        - type: command\n"
        "          command: echo ok\n"
        "---\n"
        "Use the valid skill.\n",
        encoding="utf-8",
    )
    directory = SkillsDirectory(
        sources=((SkillSource.USER_RELAY_TEAMS, skills_dir),),
        max_depth=3,
    )

    directory.discover()

    skill = directory.get_skill("valid")
    assert skill is not None
    groups = skill.metadata.hooks.hooks[HookEventName.PRE_TOOL_USE]
    assert [group.matcher for group in groups] == ["read", "write"]
    assert all(group.hooks[0].if_rule == "Bash(git *)" for group in groups)


def test_discover_ignores_unsupported_frontmatter_matcher_for_stop_hooks(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "valid"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: valid\n"
        "description: valid skill\n"
        "hooks:\n"
        "  Stop:\n"
        "    - matcher: manual\n"
        "      hooks:\n"
        "        - type: command\n"
        "          command: echo stop\n"
        "---\n"
        "Use the valid skill.\n",
        encoding="utf-8",
    )
    directory = SkillsDirectory(
        sources=((SkillSource.USER_RELAY_TEAMS, skills_dir),),
        max_depth=3,
    )

    directory.discover()

    skill = directory.get_skill("valid")
    assert skill is not None
    assert skill.metadata.hooks.hooks == {}


def test_load_skill_autodiscovers_assets_resources_and_scripts(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "deck"
    resources_dir = skill_dir / "resources"
    assets_dir = skill_dir / "assets"
    scripts_dir = skill_dir / "scripts"
    resources_dir.mkdir(parents=True)
    assets_dir.mkdir()
    scripts_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: deck\n"
        "description: build slide decks\n"
        "---\n"
        "Use the deck workflow.\n"
        "- build: Generate the deck.\n",
        encoding="utf-8",
    )
    (resources_dir / "usage.txt").write_text("Usage\n", encoding="utf-8")
    (assets_dir / "theme.css").write_text("body {}\n", encoding="utf-8")
    (scripts_dir / "build.py").write_text("print('build')\n", encoding="utf-8")
    directory = SkillsDirectory(
        sources=((SkillSource.PROJECT_AGENTS, tmp_path / "skills"),)
    )

    skill = directory._load_skill(
        path=skill_dir / "SKILL.md",
        source=SkillSource.PROJECT_AGENTS,
    )

    assert skill is not None
    assert skill.source == SkillSource.PROJECT_AGENTS
    assert tuple(sorted(skill.metadata.resources.keys())) == (
        "scripts/build.py",
        "theme.css",
        "usage.txt",
    )
    assert skill.metadata.resources["theme.css"].description == (
        "Auto-discovered resource: theme.css"
    )
    assert skill.metadata.resources["scripts/build.py"].description == (
        "Script source: build"
    )
    assert skill.metadata.scripts["build"].description == "Generate the deck."


def test_parse_frontmatter_hooks_tolerates_parser_failure() -> None:
    original = _parse_frontmatter_hooks.__globals__["parse_tolerant_hooks_payload"]

    def _raise(_: object) -> None:
        raise RuntimeError("boom")

    _parse_frontmatter_hooks.__globals__["parse_tolerant_hooks_payload"] = _raise
    try:
        hooks = _parse_frontmatter_hooks({"hooks": {"Stop": []}})
    finally:
        _parse_frontmatter_hooks.__globals__["parse_tolerant_hooks_payload"] = original

    assert hooks == HooksConfig()


def _write_skill(
    skill_dir: Path,
    *,
    name: str,
    description: str,
    instructions: str,
) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{instructions}\n",
        encoding="utf-8",
    )
