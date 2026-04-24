# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
import json
from pathlib import Path
from typing import Optional, TypeAlias, Union
from urllib.parse import quote

import typer

RequestJsonResponse: TypeAlias = Union[dict[str, object], list[object]]
RequestJsonCallable: TypeAlias = Callable[
    [str, str, str, Optional[dict[str, object]]], RequestJsonResponse
]
AutoStartCallable: TypeAlias = Callable[[str, bool], None]


class CommandOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


def build_commands_app(
    *,
    request_json: RequestJsonCallable,
    auto_start_if_needed: AutoStartCallable,
    default_base_url: str,
) -> typer.Typer:
    commands_app = typer.Typer(
        no_args_is_help=True,
        pretty_exceptions_enable=False,
        help="Inspect project and app slash commands through the backend registry.",
    )

    @commands_app.command("list")
    def commands_list(
        workspace: Optional[str] = typer.Option(
            None,
            "--workspace",
            help="Workspace path or workspace id. Defaults to the current directory.",
        ),
        output_format: CommandOutputFormat = typer.Option(
            CommandOutputFormat.TABLE,
            "--format",
            help="Render as an ASCII table or JSON.",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(
            default_base_url,
            "--base-url",
            help="Agent Teams server URL.",
        ),
        autostart: bool = typer.Option(
            True,
            "--autostart/--no-autostart",
            help="Start the local server if it is not running.",
        ),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        workspace_id = _resolve_workspace_id(
            base_url=base_url,
            workspace=workspace,
            request_json=request_json,
        )
        response = request_json(
            base_url,
            "GET",
            f"/api/system/commands?workspace_id={quote(workspace_id, safe='')}",
            None,
        )
        rows = _require_list_response(response, "/api/system/commands")
        if output_format == CommandOutputFormat.JSON:
            typer.echo(json.dumps(rows, ensure_ascii=False))
            return
        _render_commands_table(rows)

    @commands_app.command("show")
    def commands_show(
        name: str = typer.Argument(..., help="Command name or alias to inspect."),
        workspace: Optional[str] = typer.Option(
            None,
            "--workspace",
            help="Workspace path or workspace id. Defaults to the current directory.",
        ),
        output_format: CommandOutputFormat = typer.Option(
            CommandOutputFormat.TABLE,
            "--format",
            help="Render as an ASCII table or JSON.",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(
            default_base_url,
            "--base-url",
            help="Agent Teams server URL.",
        ),
        autostart: bool = typer.Option(
            True,
            "--autostart/--no-autostart",
            help="Start the local server if it is not running.",
        ),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        workspace_id = _resolve_workspace_id(
            base_url=base_url,
            workspace=workspace,
            request_json=request_json,
        )
        response = request_json(
            base_url,
            "GET",
            (
                f"/api/system/commands/{quote(name, safe='')}"
                f"?workspace_id={quote(workspace_id, safe='')}"
            ),
            None,
        )
        command = _require_object_response(response, "/api/system/commands/{name}")
        if output_format == CommandOutputFormat.JSON:
            typer.echo(json.dumps(command, ensure_ascii=False))
            return
        _render_command_detail(command)

    return commands_app


def _resolve_workspace_id(
    *,
    base_url: str,
    workspace: Optional[str],
    request_json: RequestJsonCallable,
) -> str:
    raw_workspace = workspace.strip() if workspace is not None else ""
    if raw_workspace and not _looks_like_path(raw_workspace):
        workspace_path = Path(raw_workspace).expanduser()
        if not workspace_path.exists():
            return raw_workspace
        workspace_id = _try_get_workspace_id(
            base_url=base_url,
            workspace_id=raw_workspace,
            request_json=request_json,
        )
        if workspace_id is not None:
            return workspace_id
        return _pick_workspace_id(
            base_url=base_url,
            workspace_path=workspace_path.resolve(),
            request_json=request_json,
        )
    workspace_path = (
        Path.cwd().resolve()
        if not raw_workspace
        else Path(raw_workspace).expanduser().resolve()
    )
    return _pick_workspace_id(
        base_url=base_url,
        workspace_path=workspace_path,
        request_json=request_json,
    )


def _try_get_workspace_id(
    *,
    base_url: str,
    workspace_id: str,
    request_json: RequestJsonCallable,
) -> Optional[str]:
    try:
        response = request_json(
            base_url,
            "GET",
            f"/api/workspaces/{quote(workspace_id, safe='')}",
            None,
        )
    except RuntimeError:
        return None
    payload = _require_object_response(response, "/api/workspaces/{workspace_id}")
    resolved_workspace_id = payload.get("workspace_id")
    if not isinstance(resolved_workspace_id, str) or not resolved_workspace_id.strip():
        return None
    return resolved_workspace_id


def _pick_workspace_id(
    *,
    base_url: str,
    workspace_path: Path,
    request_json: RequestJsonCallable,
) -> str:
    response = request_json(
        base_url,
        "POST",
        "/api/workspaces/pick",
        {"root_path": str(workspace_path)},
    )
    payload = _require_object_response(response, "/api/workspaces/pick")
    workspace_payload = payload.get("workspace")
    if not isinstance(workspace_payload, dict):
        raise RuntimeError("Expected workspace details from /api/workspaces/pick")
    workspace_id = workspace_payload.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise RuntimeError("Workspace response is missing workspace_id")
    return workspace_id


def _looks_like_path(value: str) -> bool:
    candidate = value.strip()
    if candidate in {".", ".."}:
        return True
    if candidate.startswith("~"):
        return True
    if "\\" in candidate or "/" in candidate:
        return True
    if len(candidate) >= 2 and candidate[1] == ":":
        return True
    return False


def _render_commands_table(rows: list[object]) -> None:
    entries = [_as_object(row) for row in rows]
    if not entries:
        typer.echo("No commands discovered.")
        return
    name_width = max(len("Name"), *(_len_field(row, "name") for row in entries))
    scope_width = max(len("Scope"), *(_len_field(row, "scope") for row in entries))
    hint_width = max(
        len("Argument Hint"), *(_len_field(row, "argument_hint") for row in entries)
    )
    description_width = max(
        len("Description"), *(_len_field(row, "description") for row in entries)
    )
    border = (
        f"+-{'-' * name_width}-+-{'-' * scope_width}-+-{'-' * hint_width}-"
        f"+-{'-' * description_width}-+"
    )
    typer.echo(f"Commands ({len(entries)} total)")
    typer.echo(border)
    typer.echo(
        f"| {'Name'.ljust(name_width)} | "
        f"{'Scope'.ljust(scope_width)} | "
        f"{'Argument Hint'.ljust(hint_width)} | "
        f"{'Description'.ljust(description_width)} |"
    )
    typer.echo(border)
    for row in entries:
        typer.echo(
            f"| {_field(row, 'name').ljust(name_width)} | "
            f"{_field(row, 'scope').ljust(scope_width)} | "
            f"{_field(row, 'argument_hint').ljust(hint_width)} | "
            f"{_field(row, 'description').ljust(description_width)} |"
        )
    typer.echo(border)


def _render_command_detail(row: dict[str, object]) -> None:
    aliases = row.get("aliases")
    aliases_text = (
        ", ".join(str(item) for item in aliases) if isinstance(aliases, list) else ""
    )
    pairs = [
        ("Name", _field(row, "name")),
        ("Aliases", aliases_text),
        ("Scope", _field(row, "scope")),
        ("Argument Hint", _field(row, "argument_hint")),
        ("Allowed Modes", ", ".join(_iter_str_values(row.get("allowed_modes")))),
        ("Source Path", _field(row, "source_path")),
        ("Description", _field(row, "description")),
    ]
    field_width = max(len("Field"), *(len(field) for field, _ in pairs))
    value_width = max(len("Value"), *(len(value) for _, value in pairs))
    border = f"+-{'-' * field_width}-+-{'-' * value_width}-+"
    typer.echo("Command")
    typer.echo(border)
    typer.echo(f"| {'Field'.ljust(field_width)} | {'Value'.ljust(value_width)} |")
    typer.echo(border)
    for field, value in pairs:
        typer.echo(f"| {field.ljust(field_width)} | {value.ljust(value_width)} |")
    typer.echo(border)
    typer.echo("Template")
    typer.echo(_field(row, "template") or "<empty>")


def _require_object_response(
    payload: RequestJsonResponse,
    path: str,
) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    raise RuntimeError(f"Expected JSON object from {path}")


def _require_list_response(
    payload: RequestJsonResponse,
    path: str,
) -> list[object]:
    if isinstance(payload, list):
        return payload
    raise RuntimeError(f"Expected JSON array from {path}")


def _as_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _field(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) else ""


def _len_field(row: dict[str, object], key: str) -> int:
    return len(_field(row, key))


def _iter_str_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))
