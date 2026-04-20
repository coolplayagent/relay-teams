# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
import json
from pathlib import Path
from typing import TypedDict

import typer

from relay_teams.commands.command_models import CommandEntry, CommandScope
from relay_teams.commands.registry import CommandRegistry

commands_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help=(
        "Inspect project-level and app-level custom commands.\n\n"
        "Load order:\n"
        "1. <workspace>/.relay-teams/commands or .codex/commands (project scope)\n"
        "2. ~/.relay-teams/commands (app scope)\n\n"
        "Project scope takes priority over app scope for same-name commands.\n\n"
        "Common usage:\n"
        "- relay-teams commands list\n"
        "- relay-teams commands list --source project --format json\n"
        "- relay-teams commands show my-cmd"
    ),
)


class CommandOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


class CommandSourceFilter(str, Enum):
    ALL = "all"
    APP = "app"
    PROJECT = "project"


class CommandListEntry(TypedDict):
    name: str
    source: str
    description: str
    argument_hint: str


@commands_app.command(
    "list",
    help=(
        "List all discovered commands across project and app scopes.\n\n"
        "If the same command exists in both scopes, only the project version is shown.\n\n"
        "Examples:\n"
        "- relay-teams commands list\n"
        "- relay-teams commands list --source project\n"
        "- relay-teams commands list --format json"
    ),
)
def commands_list(
    output_format: CommandOutputFormat = typer.Option(
        CommandOutputFormat.TABLE,
        "--format",
        help="Render as an ASCII table or JSON.",
        case_sensitive=False,
    ),
    source: CommandSourceFilter = typer.Option(
        CommandSourceFilter.ALL,
        "--source",
        help="Filter by resolved scope: all, app, or project.",
        case_sensitive=False,
    ),
) -> None:
    registry = load_command_registry()
    commands = _filter_commands(registry.list_commands(), source)
    if output_format == CommandOutputFormat.JSON:
        typer.echo(
            json.dumps(
                [_to_command_list_entry(cmd) for cmd in commands], ensure_ascii=False
            )
        )
        return
    render_command_list_table(commands)


@commands_app.command(
    "show",
    help=(
        "Show a single command definition.\n\n"
        "The argument is the command name.\n\n"
        "Examples:\n"
        "- relay-teams commands show my-cmd\n"
        "- relay-teams commands show my-cmd --format json"
    ),
)
def commands_show(
    name: str = typer.Argument(..., help="Command name to inspect."),
    output_format: CommandOutputFormat = typer.Option(
        CommandOutputFormat.TABLE,
        "--format",
        help="Render as an ASCII table or JSON.",
        case_sensitive=False,
    ),
) -> None:
    registry = load_command_registry()
    command = registry.get_command(name)
    if command is None:
        raise typer.BadParameter(f"Unknown command: {name}")
    if output_format == CommandOutputFormat.JSON:
        typer.echo(json.dumps(_to_command_json(command), ensure_ascii=False))
        return
    render_command_detail_table(command)


def load_command_registry() -> CommandRegistry:
    return CommandRegistry.from_default_scopes()


def render_command_list_table(commands: tuple[CommandEntry, ...]) -> None:
    if not commands:
        typer.echo("No commands discovered.")
        return

    rows = [_to_command_list_entry(cmd) for cmd in commands]
    typer.echo(f"Commands ({len(rows)} total)")
    name_width = max(len("Name"), *(len(row["name"]) for row in rows))
    source_width = max(len("Source"), *(len(row["source"]) for row in rows))
    description_width = max(
        len("Description"), *(len(row["description"]) for row in rows)
    )

    border = (
        f"+-{'-' * name_width}-+-{'-' * source_width}-+-{'-' * description_width}-+"
    )
    typer.echo(border)
    typer.echo(
        f"| {'Name'.ljust(name_width)} | "
        f"{'Source'.ljust(source_width)} | "
        f"{'Description'.ljust(description_width)} |"
    )
    typer.echo(border)
    for row in rows:
        typer.echo(
            f"| {row['name'].ljust(name_width)} | "
            f"{row['source'].ljust(source_width)} | "
            f"{row['description'].ljust(description_width)} |"
        )
    typer.echo(border)


def render_command_detail_table(command: CommandEntry) -> None:
    summary_rows = [
        ("Name", command.name),
        ("Source", command.scope.value),
        ("Path", _to_path_text(command.path)),
        ("Description", command.description),
        ("Argument Hint", command.argument_hint),
        ("Allowed Modes", ", ".join(command.allowed_modes)),
    ]
    _render_key_value_table(title="Command", rows=summary_rows)
    typer.echo("Body")
    typer.echo(command.body or "<empty>")


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


def _filter_commands(
    commands: tuple[CommandEntry, ...], source: CommandSourceFilter
) -> tuple[CommandEntry, ...]:
    if source == CommandSourceFilter.ALL:
        return commands
    requested_scope = CommandScope(source.value)
    return tuple(cmd for cmd in commands if cmd.scope == requested_scope)


def _to_command_list_entry(command: CommandEntry) -> CommandListEntry:
    return CommandListEntry(
        name=command.name,
        source=command.scope.value,
        description=command.description,
        argument_hint=command.argument_hint,
    )


def _to_command_json(command: CommandEntry) -> dict[str, object]:
    return {
        "name": command.name,
        "description": command.description,
        "argument_hint": command.argument_hint,
        "allowed_modes": command.allowed_modes,
        "body": command.body,
        "source": command.scope.value,
        "path": _to_path_text(command.path),
    }


def _to_path_text(path: Path) -> str:
    return path.resolve().as_posix()
