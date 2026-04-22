# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
import json
from pathlib import Path
from typing import TypedDict

import typer

from relay_teams.skills.skill_models import (
    Skill,
    SkillResource,
    SkillSource,
    SkillScript,
)
from relay_teams.skills.skill_registry import SkillRegistry

skills_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help=(
        "Inspect skills discovered from built-in, user, and project directories.\n\n"
        "Load order:\n"
        "1. built-in skills\n"
        "2. ~/.relay-teams/skills\n"
        "3. ~/.agents/skills\n"
        "4. project .relay-teams/skills from cwd up to git root\n"
        "5. project .agents/skills from cwd up to git root\n\n"
        "If multiple sources define the same skill name, the later source wins.\n\n"
        "Common usage:\n"
        "- relay-teams skills list\n"
        "- relay-teams skills list --source project_agents --format json\n"
        "- relay-teams skills show time"
    ),
)


class SkillOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


class SkillSourceFilter(str, Enum):
    ALL = "all"
    BUILTIN = "builtin"
    USER_RELAY_TEAMS = "user_relay_teams"
    USER_AGENTS = "user_agents"
    PROJECT_RELAY_TEAMS = "project_relay_teams"
    PROJECT_AGENTS = "project_agents"


class SkillListEntry(TypedDict):
    ref: str
    name: str
    source: str
    directory: str
    description: str


@skills_app.command(
    "list",
    help=(
        "List all discovered skills across builtin and app scopes.\n\n"
        "If the same skill exists in multiple places, only the final winning entry is shown.\n\n"
        "Examples:\n"
        "- relay-teams skills list\n"
        "- relay-teams skills list --source builtin\n"
        "- relay-teams skills list --format json"
    ),
)
def skills_list(
    output_format: SkillOutputFormat = typer.Option(
        SkillOutputFormat.TABLE,
        "--format",
        help="Render as an ASCII table or JSON.",
        case_sensitive=False,
    ),
    source: SkillSourceFilter = typer.Option(
        SkillSourceFilter.ALL,
        "--source",
        help="Filter by resolved scope: all, builtin, or app.",
        case_sensitive=False,
    ),
) -> None:
    registry = load_skill_registry()
    skills = _filter_skills(registry.list_skill_definitions(), source)
    if output_format == SkillOutputFormat.JSON:
        typer.echo(
            json.dumps(
                [_to_skill_list_entry(skill) for skill in skills], ensure_ascii=False
            )
        )
        return
    render_skill_list_table(skills)


@skills_app.command(
    "show",
    help=(
        "Show a single skill definition.\n\n"
        "The argument is the skill name.\n\n"
        "Examples:\n"
        "- relay-teams skills show time\n"
        "- relay-teams skills show time --format json"
    ),
)
def skills_show(
    name: str = typer.Argument(..., help="Skill name to inspect."),
    output_format: SkillOutputFormat = typer.Option(
        SkillOutputFormat.TABLE,
        "--format",
        help="Render as an ASCII table or JSON.",
        case_sensitive=False,
    ),
) -> None:
    registry = load_skill_registry()
    skill = registry.get_skill_definition(name)
    if skill is None:
        raise typer.BadParameter(f"Unknown skill: {name}")
    if output_format == SkillOutputFormat.JSON:
        typer.echo(json.dumps(_to_skill_json(skill), ensure_ascii=False))
        return
    render_skill_detail_table(skill)


def load_skill_registry() -> SkillRegistry:
    return SkillRegistry.from_default_scopes(start_dir=Path.cwd())


def render_skill_list_table(skills: tuple[Skill, ...]) -> None:
    if not skills:
        typer.echo("No skills discovered.")
        return

    rows = [_to_skill_list_entry(skill) for skill in skills]
    typer.echo(f"Skills ({len(rows)} total)")
    name_width = max(len("Name"), *(len(row["name"]) for row in rows))
    source_width = max(len("Source"), *(len(row["source"]) for row in rows))
    directory_width = max(len("Directory"), *(len(row["directory"]) for row in rows))
    description_width = max(
        len("Description"), *(len(row["description"]) for row in rows)
    )

    border = (
        f"+-{'-' * name_width}-+-{'-' * source_width}-+-{'-' * directory_width}-"
        f"+-{'-' * description_width}-+"
    )
    typer.echo(border)
    typer.echo(
        f"| {'Name'.ljust(name_width)} | "
        f"{'Source'.ljust(source_width)} | "
        f"{'Directory'.ljust(directory_width)} | "
        f"{'Description'.ljust(description_width)} |"
    )
    typer.echo(border)
    for row in rows:
        typer.echo(
            f"| {row['name'].ljust(name_width)} | "
            f"{row['source'].ljust(source_width)} | "
            f"{row['directory'].ljust(directory_width)} | "
            f"{row['description'].ljust(description_width)} |"
        )
    typer.echo(border)


def render_skill_detail_table(skill: Skill) -> None:
    summary_rows = [
        ("Ref", skill.ref),
        ("Name", skill.metadata.name),
        ("Source", skill.source.value),
        ("Directory", _to_path_text(skill.directory)),
        ("Manifest", _to_path_text(skill.directory / "SKILL.md")),
        ("Description", skill.metadata.description),
    ]
    _render_key_value_table(title="Skill", rows=summary_rows)
    _render_named_paths_table(
        title="Resources",
        rows=tuple(skill.metadata.resources.values()),
        empty_message="No resources.",
    )
    _render_named_paths_table(
        title="Scripts",
        rows=tuple(skill.metadata.scripts.values()),
        empty_message="No scripts.",
    )
    typer.echo("Files")
    typer.echo(
        "\n".join(_iter_skill_file_paths(skill.directory)) or "No files discovered."
    )
    typer.echo("Instructions")
    typer.echo(skill.metadata.instructions or "<empty>")


def _render_key_value_table(title: str, rows: list[tuple[str, str]]) -> None:
    typer.echo(title)
    field_width = max(len("Field"), *(len(field) for field, _ in rows))
    value_width = max(len("Value"), *(len(value) for _, value in rows))
    border = f"+-{'-' * field_width}-+-{'-' * value_width}-+"
    typer.echo(border)
    typer.echo(f"| {'Field'.ljust(field_width)} | {'Value'.ljust(value_width)} |")
    typer.echo(border)
    for field, value in rows:
        typer.echo(f"| {field.ljust(field_width)} | {value.ljust(value_width)} |")
    typer.echo(border)


def _render_named_paths_table(
    *,
    title: str,
    rows: tuple[SkillResource | SkillScript, ...],
    empty_message: str,
) -> None:
    typer.echo(title)
    if not rows:
        typer.echo(empty_message)
        return

    name_width = max(len("Name"), *(len(item.name) for item in rows))
    path_width = max(len("Path"), *(len(str(item.path)) for item in rows))
    description_width = max(
        len("Description"), *(len(item.description) for item in rows)
    )
    border = f"+-{'-' * name_width}-+-{'-' * path_width}-+-{'-' * description_width}-+"
    typer.echo(border)
    typer.echo(
        f"| {'Name'.ljust(name_width)} | "
        f"{'Path'.ljust(path_width)} | "
        f"{'Description'.ljust(description_width)} |"
    )
    typer.echo(border)
    for item in rows:
        typer.echo(
            f"| {item.name.ljust(name_width)} | "
            f"{str(item.path).ljust(path_width)} | "
            f"{item.description.ljust(description_width)} |"
        )
    typer.echo(border)


def _filter_skills(
    skills: tuple[Skill, ...], source: SkillSourceFilter
) -> tuple[Skill, ...]:
    if source == SkillSourceFilter.ALL:
        return skills
    requested_source = SkillSource(source.value)
    return tuple(skill for skill in skills if skill.source == requested_source)


def _to_skill_list_entry(skill: Skill) -> SkillListEntry:
    return SkillListEntry(
        ref=skill.ref,
        name=skill.metadata.name,
        source=skill.source.value,
        directory=_to_path_text(skill.directory),
        description=skill.metadata.description,
    )


def _to_skill_json(skill: Skill) -> dict[str, object]:
    return {
        "ref": skill.ref,
        "name": skill.metadata.name,
        "description": skill.metadata.description,
        "manifest_path": _to_path_text(skill.directory / "SKILL.md"),
        "manifest_content": (skill.directory / "SKILL.md").read_text(encoding="utf-8"),
        "instructions": skill.metadata.instructions,
        "source": skill.source.value,
        "directory": _to_path_text(skill.directory),
        "resources": [
            {
                "name": resource.name,
                "description": resource.description,
                "path": _to_path_text(resource.path)
                if resource.path is not None
                else None,
                "content": resource.content,
            }
            for resource in skill.metadata.resources.values()
        ],
        "scripts": [
            {
                "name": script.name,
                "description": script.description,
                "path": _to_path_text(script.path),
            }
            for script in skill.metadata.scripts.values()
        ],
        "files": list(_iter_skill_file_paths(skill.directory)),
    }


def _to_path_text(path: Path) -> str:
    return path.resolve().as_posix()


def _iter_skill_file_paths(skill_dir: Path) -> tuple[str, ...]:
    return tuple(
        sorted(_to_path_text(path) for path in skill_dir.rglob("*") if path.is_file())
    )
