# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
import json

import typer

type RequestJsonCallable = Callable[
    [str, str, str, dict[str, object] | None], dict[str, object] | list[object]
]
type AutoStartCallable = Callable[[str, bool], None]


class RunsOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


def build_runs_app(
    *,
    request_json: RequestJsonCallable,
    auto_start_if_needed: AutoStartCallable,
    default_base_url: str,
) -> typer.Typer:
    runs_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

    @runs_app.command("todo")
    def get_run_todo(
        run_id: str = typer.Option(..., "--run-id"),
        output_format: RunsOutputFormat = typer.Option(
            RunsOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        payload = request_json(
            base_url,
            "GET",
            f"/api/runs/{run_id}/todo",
            None,
        )
        response = _require_object_response(payload, f"/api/runs/{run_id}/todo")
        if output_format == RunsOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(_render_todo_table(response))

    return runs_app


def _render_todo_table(payload: dict[str, object]) -> str:
    todo = payload.get("todo")
    if not isinstance(todo, dict):
        return "No todo snapshot available."
    items = todo.get("items")
    header_rows = [
        ("Run ID", str(todo.get("run_id") or "-")),
        ("Session ID", str(todo.get("session_id") or "-")),
        ("Version", str(todo.get("version") or 0)),
        ("Updated At", str(todo.get("updated_at") or "-")),
        ("Updated By Role", str(todo.get("updated_by_role_id") or "-")),
        ("Updated By Instance", str(todo.get("updated_by_instance_id") or "-")),
    ]
    width = max(len(name) for name, _ in header_rows)
    lines = [f"{name.ljust(width)} : {value}" for name, value in header_rows]
    lines.append("")
    lines.append("Items")
    lines.append("-----")
    if not isinstance(items, list) or not items:
        lines.append("(empty)")
        return "\n".join(lines)
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "pending")
        content = str(item.get("content") or "")
        lines.append(f"{index}. [{status}] {content}")
    return "\n".join(lines)


def _require_object_response(
    payload: dict[str, object] | list[object],
    path: str,
) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    raise RuntimeError(f"Expected JSON object from {path}")
