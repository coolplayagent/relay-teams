# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
import json
import os
import re
import shutil
from typing import TypedDict

import typer

from agent_teams.env.runtime_env import (
    get_project_env_file_path,
    get_user_env_file_path,
    load_env_file,
)

env_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

MASKED_VALUE = "<masked>"
SENSITIVE_ENV_TOKENS: tuple[str, ...] = ("KEY", "TOKEN", "SECRET", "PASSWORD")
TABLE_MAX_KEY_WIDTH = 42
TABLE_MAX_VALUE_WIDTH = 96
TABLE_MIN_VALUE_WIDTH = 24
TABLE_FALLBACK_TERMINAL_WIDTH = 120


class EnvOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


class EnvListEntry(TypedDict):
    key: str
    value: str
    source: str
    masked: bool


@env_app.command("list")
def env_list(
    output_format: EnvOutputFormat = typer.Option(
        EnvOutputFormat.TABLE,
        "--format",
        help="Output format: table or json.",
        case_sensitive=False,
    ),
    show_secrets: bool = typer.Option(
        False, "--show-secrets", help="Show secret values without masking."
    ),
    prefix: str | None = typer.Option(
        None, "--prefix", help="Only list environment variables with this key prefix."
    ),
) -> None:
    entries = collect_env_entries(prefix=prefix, show_secrets=show_secrets)
    if output_format == EnvOutputFormat.JSON:
        typer.echo(json.dumps(entries, ensure_ascii=False))
        return
    render_env_table(entries)


def collect_env_entries(
    *, prefix: str | None, show_secrets: bool
) -> list[EnvListEntry]:
    user_env = load_env_file(get_user_env_file_path())
    project_env = load_env_file(get_project_env_file_path())
    process_env = dict(os.environ)

    merged: dict[str, str] = {}
    source_by_key: dict[str, str] = {}

    merge_env_source(merged, source_by_key, user_env, "user")
    merge_env_source(merged, source_by_key, project_env, "project")
    merge_env_source(merged, source_by_key, process_env, "process")

    entries: list[EnvListEntry] = []
    for key in sorted(merged):
        if prefix and not key.startswith(prefix):
            continue

        is_masked = (not show_secrets) and is_sensitive_env_key(key)
        displayed_value = MASKED_VALUE if is_masked else merged[key]
        entries.append(
            EnvListEntry(
                key=key,
                value=displayed_value,
                source=source_by_key.get(key, "unknown"),
                masked=is_masked,
            )
        )
    return entries


def merge_env_source(
    merged: dict[str, str],
    source_by_key: dict[str, str],
    values: dict[str, str],
    source: str,
) -> None:
    for key, value in values.items():
        merged[key] = value
        source_by_key[key] = source


def is_sensitive_env_key(key: str) -> bool:
    normalized_key = key.upper()
    tokens = [token for token in re.split(r"[^A-Z0-9]+", normalized_key) if token]
    for sensitive_token in SENSITIVE_ENV_TOKENS:
        if sensitive_token in tokens:
            return True
    return False


def render_env_table(entries: list[EnvListEntry]) -> None:
    if not entries:
        typer.echo("No environment variables matched.")
        return

    total_count = len(entries)
    masked_count = sum(1 for entry in entries if entry["masked"])
    typer.echo(f"Environment Variables ({total_count} total, {masked_count} masked)")

    key_width = min(
        TABLE_MAX_KEY_WIDTH,
        max(len("Key"), *(len(entry["key"]) for entry in entries)),
    )
    source_width = max(len("Source"), *(len(entry["source"]) for entry in entries))
    value_width = resolve_value_column_width(entries, key_width, source_width)

    border = f"+-{'-' * key_width}-+-{'-' * source_width}-+-{'-' * value_width}-+"
    typer.echo(border)
    typer.echo(
        f"| {'Key'.ljust(key_width)} | "
        f"{'Source'.ljust(source_width)} | "
        f"{'Value'.ljust(value_width)} |"
    )
    typer.echo(border)

    for entry in entries:
        key_cell = truncate_for_table(entry["key"], key_width)
        source_cell = truncate_for_table(entry["source"], source_width)
        value_cell = truncate_for_table(entry["value"], value_width)
        typer.echo(
            f"| {key_cell.ljust(key_width)} | "
            f"{source_cell.ljust(source_width)} | "
            f"{value_cell.ljust(value_width)} |"
        )

    typer.echo(border)


def resolve_value_column_width(
    entries: list[EnvListEntry], key_width: int, source_width: int
) -> int:
    max_value_width = max(len("Value"), *(len(entry["value"]) for entry in entries))
    terminal_width = shutil.get_terminal_size(
        fallback=(TABLE_FALLBACK_TERMINAL_WIDTH, 24)
    ).columns
    frame_overhead = key_width + source_width + 10
    available_width = max(TABLE_MIN_VALUE_WIDTH, terminal_width - frame_overhead)
    bounded_by_content = min(TABLE_MAX_VALUE_WIDTH, max_value_width)
    return max(TABLE_MIN_VALUE_WIDTH, min(available_width, bounded_by_content))


def truncate_for_table(value: str, width: int) -> str:
    if width <= 3:
        return value[:width]
    if len(value) <= width:
        return value
    return f"{value[: width - 3]}..."
