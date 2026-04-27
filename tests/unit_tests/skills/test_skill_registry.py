# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import mkdtemp
import threading
from typing import cast

from pydantic import JsonValue
import pytest

from relay_teams.builtin import get_builtin_skills_dir
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.skills.discovery import SkillsDirectory
from relay_teams.skills.skill_models import SkillSource
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.tools.runtime.context import ToolContext


def test_get_toolset_tools_builds_skill_tools_without_annotation_errors() -> None:
    registry = SkillRegistry(directory=_skills_directory(Path(".agent_teams/skills")))

    tools = registry.get_toolset_tools(("time",))

    names = {tool.name for tool in tools}
    assert names == {"load_skill"}


def test_get_instruction_entries_returns_structured_data(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "time"
    _write_skill(
        skill_dir,
        name="time",
        description="timezone helper",
        instructions="Use UTC for all timestamps.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    entries = registry.get_instruction_entries(("time",))

    assert len(entries) == 1
    assert entries[0].name == "time"
    assert entries[0].description == "timezone helper"


def test_resolve_known_ignores_unknown_skills_when_strict_is_false(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC for all timestamps.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    resolved = registry.resolve_known(
        ("time", "missing_skill"),
        strict=False,
        consumer="tests.unit_tests.skills.test_skill_registry",
    )

    assert resolved == ("time",)


def test_resolve_known_trims_blank_entries_and_preserves_requested_order(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "alpha",
        name="alpha",
        description="alpha helper",
        instructions="Use alpha.",
    )
    _write_skill(
        tmp_path / "skills" / "beta",
        name="beta",
        description="beta helper",
        instructions="Use beta.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    resolved = registry.resolve_known(
        (" beta ", "", "alpha", "beta "),
        strict=False,
    )

    assert resolved == ("beta", "alpha", "beta")


def test_resolve_known_expands_wildcard_to_all_skill_names(tmp_path: Path) -> None:
    _write_skill(
        tmp_path / "skills" / "alpha",
        name="alpha",
        description="alpha helper",
        instructions="Use alpha.",
    )
    _write_skill(
        tmp_path / "skills" / "beta",
        name="beta",
        description="beta helper",
        instructions="Use beta.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    resolved = registry.resolve_known(("*", "alpha"), strict=True)

    assert resolved == ("alpha", "beta")


def test_resolve_known_can_preserve_wildcard_for_role_config(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    resolved = registry.resolve_known(
        ("*", "builtin:time"),
        strict=True,
        expand_wildcards=False,
    )

    assert resolved == ("*", "time")


def test_resolve_known_rejects_partial_wildcard_patterns(tmp_path: Path) -> None:
    _write_skill(
        tmp_path / "skills" / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    with pytest.raises(ValueError, match="Unknown skills: \\['time\\*'\\]"):
        registry.resolve_known(("time*",), strict=True)


def test_resolve_known_expands_wildcard_before_filtering_unknown_skills(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "alpha",
        name="alpha",
        description="alpha helper",
        instructions="Use alpha.",
    )
    _write_skill(
        tmp_path / "skills" / "beta",
        name="beta",
        description="beta helper",
        instructions="Use beta.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    resolved = registry.resolve_known(
        ("*", "missing", "builtin:alpha"),
        strict=False,
    )

    assert resolved == ("alpha", "beta")


def test_resolve_known_reports_unknown_skills_even_when_wildcard_is_present(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "alpha",
        name="alpha",
        description="alpha helper",
        instructions="Use alpha.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    with pytest.raises(ValueError, match="Unknown skills: \\['missing'\\]"):
        registry.resolve_known(("*", "missing"), strict=True)


def test_resolve_known_wildcard_on_empty_registry_is_empty(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    registry = SkillRegistry(directory=_skills_directory(skills_dir))

    assert registry.resolve_known(("*",), strict=True) == ()
    assert registry.resolve_known(("*", "missing"), strict=False) == ()


def test_resolve_known_preserves_wildcard_once_when_not_expanding(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    resolved = registry.resolve_known(
        (" * ", "missing", "*", "app:time"),
        strict=False,
        expand_wildcards=False,
    )

    assert resolved == ("*", "time")


def test_validate_known_accepts_exact_wildcard_and_rejects_partial_wildcard(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    registry = SkillRegistry(directory=_skills_directory(skills_dir))

    registry.validate_known(("*",))
    with pytest.raises(ValueError, match="Unknown skills: \\['builtin:\\*'\\]"):
        registry.validate_known(("builtin:*",))


def test_resolve_known_rejects_blank_entries_when_strict_is_true(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC for all timestamps.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    with pytest.raises(ValueError, match="Unknown skills: \\[''\\]"):
        registry.resolve_known(("", "time"))


def test_resolve_known_accepts_legacy_scoped_skill_refs_when_targets_exist(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "deepresearch",
        name="deepresearch",
        description="research helper",
        instructions="Research deeply.",
    )
    _write_skill(
        tmp_path / "skills" / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC for all timestamps.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    resolved = registry.resolve_known(
        ("builtin:deepresearch", "app:time"),
        strict=True,
    )

    assert resolved == ("deepresearch", "time")


def test_validate_known_accepts_legacy_scoped_skill_refs_when_targets_exist(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "deepresearch",
        name="deepresearch",
        description="research helper",
        instructions="Research deeply.",
    )
    _write_skill(
        tmp_path / "skills" / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC for all timestamps.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    registry.validate_known(("builtin:deepresearch", "app:time"))


def test_resolve_known_preserves_unknown_legacy_scoped_refs_in_errors(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC for all timestamps.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    with pytest.raises(ValueError, match="builtin:missing"):
        registry.resolve_known(("builtin:missing", "time"), strict=True)


def test_registry_from_skill_dirs_uses_user_override_for_same_name(
    tmp_path: Path,
) -> None:
    builtin_skill_dir = tmp_path / "builtin" / "skills" / "time"
    user_skill_dir = tmp_path / ".agent-teams" / "skills" / "time"
    _write_skill(
        builtin_skill_dir,
        name="time",
        description="builtin timezone helper",
        instructions="Use the builtin timezone.",
    )
    _write_skill(
        user_skill_dir,
        name="time",
        description="user timezone helper",
        instructions="Use UTC for all user timestamps.",
    )

    registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / ".agent-teams" / "skills",
        builtin_skills_dir=tmp_path / "builtin" / "skills",
    )

    skill = registry.get_skill_definition("time")
    entries = registry.get_instruction_entries(("time",))

    assert skill is not None
    assert skill.source == SkillSource.USER_RELAY_TEAMS
    assert skill.metadata.description == "user timezone helper"
    assert registry.resolve_known(("time",), strict=False) == ("time",)
    assert entries == (
        type(entries[0])(name="time", description="user timezone helper"),
    )


def test_registry_from_skill_dirs_loads_builtin_skill_when_user_skill_missing(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "builtin" / "skills" / "time",
        name="time",
        description="builtin timezone helper",
        instructions="Use the builtin timezone.",
    )

    registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / ".agent-teams" / "skills",
        builtin_skills_dir=tmp_path / "builtin" / "skills",
    )

    skill = registry.get_skill_definition("time")

    assert skill is not None
    assert skill.source == SkillSource.BUILTIN
    assert registry.list_names() == ("time",)


def test_registry_from_config_dirs_keeps_effective_skills_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    builtin_skills_dir = tmp_path / "builtin" / "skills"
    monkeypatch.setattr(
        "relay_teams.skills.discovery.get_builtin_skills_dir_path",
        lambda: builtin_skills_dir.resolve(),
    )

    _write_skill(
        builtin_skills_dir / "shared",
        name="shared",
        description="builtin shared skill",
        instructions="Builtin instructions.",
    )
    _write_skill(
        builtin_skills_dir / "builtin_only",
        name="builtin_only",
        description="builtin only skill",
        instructions="Builtin only instructions.",
    )
    _write_skill(
        app_config_dir / "skills" / "shared",
        name="shared",
        description="user shared skill",
        instructions="User instructions.",
    )
    _write_skill(
        app_config_dir / "skills" / "app_only",
        name="app_only",
        description="user only skill",
        instructions="User only instructions.",
    )

    registry = SkillRegistry.from_config_dirs(app_config_dir=app_config_dir)

    skills = registry.list_skill_definitions()
    app_only_skill = registry.get_skill_definition("app_only")
    shared_skill = registry.get_skill_definition("shared")
    builtin_only_skill = registry.get_skill_definition("builtin_only")

    assert tuple(skill.ref for skill in skills) == (
        "app_only",
        "builtin_only",
        "shared",
    )
    assert app_only_skill is not None
    assert app_only_skill.source == SkillSource.USER_RELAY_TEAMS
    assert shared_skill is not None
    assert shared_skill.source == SkillSource.USER_RELAY_TEAMS
    assert builtin_only_skill is not None
    assert builtin_only_skill.source == SkillSource.BUILTIN


def test_registry_from_config_dirs_handles_missing_user_skills_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    monkeypatch.setattr(
        "relay_teams.skills.discovery.get_builtin_skills_dir_path",
        lambda: (tmp_path / "builtin" / "skills").resolve(),
    )

    registry = SkillRegistry.from_config_dirs(app_config_dir=app_config_dir)

    assert not (app_config_dir / "skills").exists()
    assert registry.list_skill_definitions() == ()


def test_skills_directory_discover_replaces_skill_cache_atomically(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "alpha",
        name="alpha",
        description="alpha skill",
        instructions="Use alpha.",
    )
    _write_skill(
        tmp_path / "skills" / "beta",
        name="beta",
        description="beta skill",
        instructions="Use beta.",
    )
    directory = _skills_directory(tmp_path / "skills")
    directory.discover()
    alpha_manifest = tmp_path / "skills" / "alpha" / "SKILL.md"
    alpha_manifest.write_text(
        f"{alpha_manifest.read_text(encoding='utf-8')}\n",
        encoding="utf-8",
    )
    original_load_skill = directory._load_skill
    load_started = threading.Event()
    allow_continue = threading.Event()

    def blocking_load_skill(
        *,
        path: Path,
        source: SkillSource,
        load_warnings: list[tuple[Path, str]] | None = None,
    ):
        if path.parent.name == "alpha":
            load_started.set()
            assert allow_continue.wait(timeout=5)
        return original_load_skill(
            path=path,
            source=source,
            load_warnings=load_warnings,
        )

    directory._load_skill = blocking_load_skill
    worker = threading.Thread(target=directory.discover)
    worker.start()
    assert load_started.wait(timeout=5)

    refs_during_discover = {skill.ref for skill in directory.list_skills()}

    allow_continue.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert refs_during_discover == {"alpha", "beta"}


def test_registry_loads_builtin_skill_installer_definition(tmp_path: Path) -> None:
    registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / ".agent-teams" / "skills",
        builtin_skills_dir=get_builtin_skills_dir(),
    )

    skill = registry.get_skill_definition("skill-installer")

    assert skill is not None
    assert skill.source == SkillSource.BUILTIN
    assert tuple(sorted(skill.metadata.scripts.keys())) == (
        "bind-skill-to-role",
        "install-clawhub-skill",
        "install-skill-from-github",
        "list-skills",
        "search-and-install-clawhub-skill",
        "search-clawhub-skills",
    )


def test_load_skill_returns_manifest_and_selected_absolute_file_paths(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills" / "time"
    resources_dir = skill_dir / "resources"
    scripts_dir = skill_dir / "scripts"
    resources_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    manifest_path = skill_dir / "SKILL.md"
    manifest_content = (
        "---\n"
        "name: time\n"
        "description: timezone helper\n"
        "resources:\n"
        "  usage.txt:\n"
        "    description: Usage notes.\n"
        "    path: resources/usage.txt\n"
        "---\n"
        "Use UTC for all timestamps.\n"
    )
    manifest_path.write_text(manifest_content, encoding="utf-8")
    usage_path = resources_dir / "usage.txt"
    usage_path.write_text("Use UTC.\n", encoding="utf-8")
    script_path = scripts_dir / "trace_context.py"
    script_path.write_text("print('trace')\n", encoding="utf-8")
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, _FakeCtx())),
            name="time",
        )
    )

    assert result["ok"] is True
    data = cast(dict[str, JsonValue], result["data"])
    assert data["ref"] == "time"
    assert data["source"] == "user_relay_teams"
    assert data["manifest_path"] == manifest_path.resolve().as_posix()
    assert data["manifest_content"] == manifest_content
    assert data["instructions"] == "Use UTC for all timestamps."
    assert data["directory"] == skill_dir.resolve().as_posix()
    assert cast(dict[str, JsonValue], data["resources"])["usage.txt"] == {
        "name": "usage.txt",
        "description": "Usage notes.",
        "path": usage_path.resolve().as_posix(),
        "content": None,
    }
    assert cast(dict[str, JsonValue], data["scripts"])["trace_context"] == {
        "name": "trace_context",
        "description": "Execute trace_context script.",
        "path": script_path.resolve().as_posix(),
    }
    assert data["files_truncated"] is False
    assert data["files_omitted_count"] == 0
    assert sorted(cast(list[str], data["files"])) == sorted(
        [
            manifest_path.resolve().as_posix(),
            script_path.resolve().as_posix(),
            usage_path.resolve().as_posix(),
        ]
    )


def test_load_skill_uses_runtime_role_resolver_when_available(tmp_path: Path) -> None:
    _write_skill(
        tmp_path / "skills" / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))
    ctx = _FakeCtx()
    ctx.deps.runtime_role_resolver = _FakeRuntimeRoleResolver(
        RoleDefinition(
            role_id="runtime_writer",
            name="Runtime Writer",
            description="Resolved at runtime.",
            version="1",
            tools=(),
            skills=("time",),
            system_prompt="Use runtime role.",
        )
    )

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, ctx)),
            name="time",
        )
    )

    assert result["ok"] is True
    data = cast(dict[str, JsonValue], result["data"])
    assert data["ref"] == "time"


def test_load_skill_falls_back_to_role_registry_when_runtime_role_missing(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))
    ctx = _ctx_with_role_skills(("time",))
    ctx.deps.runtime_role_resolver = _FakeRuntimeRoleResolver(error=KeyError("missing"))

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, ctx)),
            name="time",
        )
    )

    assert result["ok"] is True
    data = cast(dict[str, JsonValue], result["data"])
    assert data["ref"] == "time"


def test_load_skill_uses_user_override_when_builtin_and_user_share_name(
    tmp_path: Path,
) -> None:
    user_skill_dir = tmp_path / ".agent-teams" / "skills" / "deepresearch"
    builtin_skill_dir = tmp_path / "builtin" / "skills" / "deepresearch"
    _write_skill(
        user_skill_dir,
        name="deepresearch",
        description="user deepresearch",
        instructions="Use user deepresearch.",
    )
    _write_skill(
        builtin_skill_dir,
        name="deepresearch",
        description="builtin deepresearch",
        instructions="Use builtin deepresearch.",
    )
    registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / ".agent-teams" / "skills",
        builtin_skills_dir=tmp_path / "builtin" / "skills",
    )
    ctx = _ctx_with_role_skills(("deepresearch",))

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, ctx)),
            name="deepresearch",
        )
    )

    assert result["ok"] is True
    data = cast(dict[str, JsonValue], result["data"])
    assert data["ref"] == "deepresearch"
    assert data["source"] == "user_relay_teams"
    assert data["description"] == "user deepresearch"
    assert data["instructions"] == "Use user deepresearch."


def test_load_skill_returns_builtin_skill_when_no_override_exists(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "builtin" / "skills" / "deepresearch",
        name="deepresearch",
        description="builtin deepresearch",
        instructions="Use builtin deepresearch.",
    )
    registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / ".agent-teams" / "skills",
        builtin_skills_dir=tmp_path / "builtin" / "skills",
    )
    ctx = _ctx_with_role_skills(("deepresearch",))

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, ctx)),
            name="deepresearch",
        )
    )

    assert result["ok"] is True
    data = cast(dict[str, JsonValue], result["data"])
    assert data["ref"] == "deepresearch"
    assert data["source"] == "builtin"
    assert data["description"] == "builtin deepresearch"


def test_load_skill_accepts_legacy_scoped_name_for_authorized_role(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))
    ctx = _ctx_with_role_skills(("time",))

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, ctx)),
            name="builtin:time",
        )
    )

    assert result["ok"] is True
    data = cast(dict[str, JsonValue], result["data"])
    assert data["ref"] == "time"
    assert data["name"] == "time"


def test_load_skill_allows_any_known_skill_for_wildcard_authorized_role(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "planner",
        name="planner",
        description="planning helper",
        instructions="Plan the work.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))
    ctx = _ctx_with_role_skills(("*",))

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, ctx)),
            name="builtin:planner",
        )
    )

    assert result["ok"] is True
    data = cast(dict[str, JsonValue], result["data"])
    assert data["ref"] == "planner"


def test_load_skill_rejects_unknown_skill_for_wildcard_authorized_role(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    registry = SkillRegistry(directory=_skills_directory(skills_dir))
    ctx = _ctx_with_role_skills(("*",))

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, ctx)),
            name="missing",
        )
    )

    assert result["ok"] is False
    error = cast(dict[str, JsonValue], result["error"])
    assert "Role spec_coder is not authorized to load skill: missing" in cast(
        str, error["message"]
    )


def test_load_skill_rejects_wildcard_as_concrete_skill_request(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "planner",
        name="planner",
        description="planning helper",
        instructions="Plan the work.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))
    ctx = _ctx_with_role_skills(("*",))

    for requested_name in ("*", "builtin:*"):
        result = asyncio.run(
            registry.load_skill(
                cast(ToolContext, cast(object, ctx)),
                name=requested_name,
            )
        )

        assert result["ok"] is False
        error = cast(dict[str, JsonValue], result["error"])
        assert (
            f"Role spec_coder is not authorized to load skill: {requested_name}"
            in cast(str, error["message"])
        )


def test_load_skill_rejects_role_unauthorized_skill(tmp_path: Path) -> None:
    _write_skill(
        tmp_path / "skills" / "planner",
        name="planner",
        description="planning helper",
        instructions="Plan the work.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, _FakeCtx())),
            name="planner",
        )
    )

    assert result["ok"] is False
    error = cast(dict[str, JsonValue], result["error"])
    assert (
        error["message"] == "Role spec_coder is not authorized to load skill: planner"
    )


def test_load_skill_reports_missing_skill_for_authorized_stale_role_ref() -> None:
    registry = SkillRegistry(directory=_skills_directory(Path("missing-skills")))
    ctx = _ctx_with_role_skills(("planner",))

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, ctx)),
            name="planner",
        )
    )

    assert result["ok"] is False
    error = cast(dict[str, JsonValue], result["error"])
    assert "Skill not found: planner" in cast(str, error["message"])


@pytest.mark.timeout(5)
def test_load_skill_omits_large_dependency_trees_from_file_listing(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills" / "deck"
    docs_dir = skill_dir / "docs"
    node_modules_dir = skill_dir / "node_modules" / "pkg"
    docs_dir.mkdir(parents=True)
    node_modules_dir.mkdir(parents=True)
    manifest_path = skill_dir / "SKILL.md"
    manifest_path.write_text(
        "---\n"
        "name: deck\n"
        "description: build slide decks\n"
        "---\n"
        "Use the planner and designer workflow.\n",
        encoding="utf-8",
    )
    bundled_dependency = node_modules_dir / "index.js"
    bundled_dependency.write_text("export const bundled = true;\n", encoding="utf-8")
    for index in range(240):
        (docs_dir / f"section_{index:03}.md").write_text(
            f"Section {index}\n",
            encoding="utf-8",
        )

    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, _FakeCtx())),
            name="deck",
        )
    )

    assert result["ok"] is True
    data = cast(dict[str, JsonValue], result["data"])
    assert data["ref"] == "deck"
    files = cast(list[str], data["files"])
    assert manifest_path.resolve().as_posix() in files
    assert bundled_dependency.resolve().as_posix() not in files
    assert all("node_modules" not in path for path in files)
    assert data["files_truncated"] is True
    assert cast(int, data["files_omitted_count"]) > 0
    assert len(files) < 241


def test_load_skill_excludes_cached_files_and_prioritizes_manifest_resources_and_scripts(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills" / "time"
    resources_dir = skill_dir / "resources"
    scripts_dir = skill_dir / "scripts"
    git_dir = skill_dir / ".git"
    pycache_dir = skill_dir / "__pycache__"
    resources_dir.mkdir(parents=True)
    scripts_dir.mkdir()
    git_dir.mkdir()
    pycache_dir.mkdir()
    manifest_path = skill_dir / "SKILL.md"
    resource_path = resources_dir / "usage.txt"
    script_path = scripts_dir / "trace.py"
    notes_path = skill_dir / "notes.txt"
    manifest_path.write_text(
        "---\nname: time\ndescription: timezone helper\n---\nUse UTC.\n",
        encoding="utf-8",
    )
    resource_path.write_text("Usage\n", encoding="utf-8")
    script_path.write_text("print('trace')\n", encoding="utf-8")
    notes_path.write_text("Notes\n", encoding="utf-8")
    (git_dir / "config").write_text("[core]\n", encoding="utf-8")
    (pycache_dir / "trace.pyc").write_bytes(b"pyc")
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    result = asyncio.run(
        registry.load_skill(
            cast(ToolContext, cast(object, _ctx_with_role_skills(("time",)))),
            name="time",
        )
    )

    assert result["ok"] is True
    data = cast(dict[str, JsonValue], result["data"])
    assert data["files"] == [
        manifest_path.resolve().as_posix(),
        resource_path.resolve().as_posix(),
        script_path.resolve().as_posix(),
        notes_path.resolve().as_posix(),
    ]


def test_validate_known_rejects_unknown_skill(tmp_path: Path) -> None:
    _write_skill(
        tmp_path / "skills" / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    with pytest.raises(ValueError, match="Unknown skills: \\['missing_skill'\\]"):
        registry.validate_known(("time", "missing_skill"))


def test_validate_known_rejects_blank_skill_name(tmp_path: Path) -> None:
    _write_skill(
        tmp_path / "skills" / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC.",
    )
    registry = SkillRegistry(directory=_skills_directory(tmp_path / "skills"))

    with pytest.raises(ValueError, match="Unknown skills: \\[''\\]"):
        registry.validate_known(("time", ""))


def _skills_directory(
    skills_dir: Path,
    *,
    source: SkillSource = SkillSource.USER_RELAY_TEAMS,
) -> SkillsDirectory:
    return SkillsDirectory(sources=((source, skills_dir),))


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


def _ctx_with_role_skills(skill_names: tuple[str, ...]) -> _FakeCtx:
    ctx = _FakeCtx()
    ctx.deps.role_registry = RoleRegistry()
    ctx.deps.role_registry.register(
        RoleDefinition(
            role_id="spec_coder",
            name="Spec Coder",
            description="Implements requested changes.",
            version="1",
            tools=(),
            skills=skill_names,
            system_prompt="Implement tasks.",
        )
    )
    return ctx


class _FakeRunEventHub:
    def publish(self, event: object) -> None:
        _ = event


class _FakeRunControlManager:
    def raise_if_cancelled(
        self,
        *,
        run_id: str,
        instance_id: str | None = None,
    ) -> None:
        _ = (run_id, instance_id)


class _FakeApprovalManager:
    def open_approval(self, **kwargs: object) -> None:
        _ = kwargs

    def wait_for_approval(self, **kwargs: object) -> tuple[str, str]:
        _ = kwargs
        return ("approve", "")

    def close_approval(self, **kwargs: object) -> None:
        _ = kwargs


class _FakePolicy:
    timeout_seconds = 0.01

    def requires_approval(self, tool_name: str) -> bool:
        _ = tool_name
        return False


class _FakeRunRuntimeRepo:
    def ensure(
        self,
        *,
        run_id: str,
        session_id: str,
        root_task_id: str,
    ) -> None:
        _ = (run_id, session_id, root_task_id)

    def update(self, run_id: str, **kwargs: object) -> None:
        _ = (run_id, kwargs)


class _FakeRuntimeRoleResolver:
    def __init__(
        self,
        role: RoleDefinition | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._role = role
        self._error = error

    def get_effective_role(self, *, run_id: str, role_id: str) -> RoleDefinition:
        _ = (run_id, role_id)
        if self._error is not None:
            raise self._error
        assert self._role is not None
        return self._role


class _FakeDeps:
    def __init__(self) -> None:
        self.run_id = "run-1"
        self.trace_id = "trace-1"
        self.task_id = "task-1"
        self.session_id = "session-1"
        self.instance_id = "inst-1"
        self.role_id = "spec_coder"
        self.role_registry = RoleRegistry()
        self.role_registry.register(
            RoleDefinition(
                role_id="spec_coder",
                name="Spec Coder",
                description="Implements requested changes.",
                version="1",
                tools=(),
                skills=("time", "deck"),
                system_prompt="Implement tasks.",
            )
        )
        self.runtime_role_resolver: _FakeRuntimeRoleResolver | None = None
        self.run_event_hub = _FakeRunEventHub()
        self.run_control_manager = _FakeRunControlManager()
        self.tool_approval_manager = _FakeApprovalManager()
        self.tool_approval_policy = _FakePolicy()
        self.run_runtime_repo = _FakeRunRuntimeRepo()
        self.notification_service = None
        self.hook_runtime_env: dict[str, str] = {}
        self.shared_store = SharedStateRepository(Path(mkdtemp()) / "state.db")


class _FakeCtx:
    def __init__(self) -> None:
        self.deps = _FakeDeps()
        self.tool_call_id = "toolcall-1"
        self.retry = 0
