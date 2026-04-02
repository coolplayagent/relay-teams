# -*- coding: utf-8 -*-
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import cast


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_package_data(project_root: Path) -> dict[str, tuple[str, ...]]:
    pyproject_path = project_root / "pyproject.toml"
    with pyproject_path.open("rb") as handle:
        pyproject = tomllib.load(handle)

    tool_section = cast(dict[str, object], pyproject["tool"])
    setuptools_section = cast(dict[str, object], tool_section["setuptools"])
    package_data_section = cast(dict[str, object], setuptools_section["package-data"])

    package_data: dict[str, tuple[str, ...]] = {}
    for package_name, patterns_value in package_data_section.items():
        if not isinstance(package_name, str):
            continue
        if not isinstance(patterns_value, list):
            continue
        patterns = tuple(
            pattern for pattern in patterns_value if isinstance(pattern, str)
        )
        package_data[package_name] = patterns
    return package_data


def _tool_description_files(project_root: Path) -> tuple[Path, ...]:
    tool_root = project_root / "src" / "agent_teams" / "tools"
    return tuple(sorted(tool_root.rglob("*.txt")))


def _builtin_role_files(project_root: Path) -> tuple[Path, ...]:
    builtin_root = project_root / "src" / "agent_teams" / "builtin" / "roles"
    return tuple(sorted(builtin_root.glob("*.md")))


def _builtin_skill_files(project_root: Path) -> tuple[Path, ...]:
    builtin_root = project_root / "src" / "agent_teams" / "builtin" / "skills"
    return tuple(sorted(path for path in builtin_root.rglob("*") if path.is_file()))


def test_tool_description_files_are_declared_in_package_data() -> None:
    project_root = _project_root()
    package_data = _load_package_data(project_root)
    description_files = _tool_description_files(project_root)

    missing_files: list[str] = []
    for description_file in description_files:
        matched = False
        for package_name, patterns in package_data.items():
            if not package_name.startswith("agent_teams.tools"):
                continue
            package_root = project_root / "src" / Path(*package_name.split("."))
            if not description_file.is_relative_to(package_root):
                continue
            relative_path = description_file.relative_to(package_root)
            if any(relative_path.match(pattern) for pattern in patterns):
                matched = True
                break
        if not matched:
            missing_files.append(
                str(description_file.relative_to(project_root / "src"))
            )

    assert missing_files == []


def test_tool_package_data_declarations_match_existing_description_files() -> None:
    project_root = _project_root()
    package_data = _load_package_data(project_root)
    description_files = _tool_description_files(project_root)

    stale_declarations: list[str] = []
    for package_name, patterns in package_data.items():
        if not package_name.startswith("agent_teams.tools"):
            continue
        package_root = project_root / "src" / Path(*package_name.split("."))
        matched = False
        for description_file in description_files:
            if not description_file.is_relative_to(package_root):
                continue
            relative_path = description_file.relative_to(package_root)
            if any(relative_path.match(pattern) for pattern in patterns):
                matched = True
                break
        if not matched:
            stale_declarations.append(package_name)

    assert stale_declarations == []


def test_builtin_role_files_are_declared_in_package_data() -> None:
    project_root = _project_root()
    package_data = _load_package_data(project_root)
    role_files = _builtin_role_files(project_root)
    builtin_package_root = project_root / "src" / "agent_teams" / "builtin"
    builtin_patterns = package_data.get("agent_teams.builtin", ())

    missing_files = [
        str(role_file.relative_to(project_root / "src"))
        for role_file in role_files
        if not any(
            role_file.relative_to(builtin_package_root).match(pattern)
            for pattern in builtin_patterns
        )
    ]

    assert missing_files == []


def test_builtin_package_data_includes_live_role_matches() -> None:
    project_root = _project_root()
    package_data = _load_package_data(project_root)
    role_files = _builtin_role_files(project_root)
    builtin_package_root = project_root / "src" / "agent_teams" / "builtin"
    builtin_patterns = package_data.get("agent_teams.builtin", ())

    matching_patterns = [
        pattern
        for pattern in builtin_patterns
        if any(
            role_file.relative_to(builtin_package_root).match(pattern)
            for role_file in role_files
        )
    ]

    assert role_files != []
    assert matching_patterns != []


def test_builtin_skill_files_are_declared_in_package_data() -> None:
    project_root = _project_root()
    package_data = _load_package_data(project_root)
    skill_files = _builtin_skill_files(project_root)
    builtin_package_root = project_root / "src" / "agent_teams" / "builtin"
    builtin_patterns = package_data.get("agent_teams.builtin", ())

    missing_files = [
        str(skill_file.relative_to(project_root / "src"))
        for skill_file in skill_files
        if not any(
            skill_file.relative_to(builtin_package_root).match(pattern)
            for pattern in builtin_patterns
        )
    ]

    assert missing_files == []


def test_builtin_package_data_includes_live_skill_matches() -> None:
    project_root = _project_root()
    package_data = _load_package_data(project_root)
    skill_files = _builtin_skill_files(project_root)
    builtin_package_root = project_root / "src" / "agent_teams" / "builtin"
    builtin_patterns = package_data.get("agent_teams.builtin", ())

    matching_patterns = [
        pattern
        for pattern in builtin_patterns
        if pattern.startswith("skills/")
        and any(
            skill_file.relative_to(builtin_package_root).match(pattern)
            for skill_file in skill_files
        )
    ]

    assert skill_files != []
    assert matching_patterns != []
