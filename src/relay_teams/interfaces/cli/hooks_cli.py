from __future__ import annotations

from collections.abc import Callable
from enum import Enum
import json

import typer

type RequestJsonCallable = Callable[
    [str, str, str, dict[str, object] | None], dict[str, object] | list[object]
]
type AutoStartCallable = Callable[[str, bool], None]


class HooksOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


def build_hooks_app(
    *,
    request_json: RequestJsonCallable,
    auto_start_if_needed: AutoStartCallable,
    default_base_url: str,
) -> typer.Typer:
    hooks_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

    @hooks_app.command("show")
    def show_hooks(
        output_format: HooksOutputFormat = typer.Option(
            HooksOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        payload = request_json(base_url, "GET", "/api/system/configs/hooks", None)
        response = _require_object_response(payload, "/api/system/configs/hooks")
        if output_format == HooksOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(_render_table(response))

    @hooks_app.command("validate")
    def validate_hooks(
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        payload = request_json(base_url, "GET", "/api/system/configs/hooks", None)
        response = _require_object_response(payload, "/api/system/configs/hooks")
        _ = request_json(
            base_url,
            "POST",
            "/api/system/configs/hooks:validate",
            response,
        )
        typer.echo("hooks config is valid")

    @hooks_app.command("list")
    def list_hooks(
        output_format: HooksOutputFormat = typer.Option(
            HooksOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        payload = request_json(base_url, "GET", "/api/system/configs/hooks", None)
        response = _require_object_response(payload, "/api/system/configs/hooks")
        if output_format == HooksOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(_render_table(response))

    return hooks_app


def _render_table(payload: dict[str, object]) -> str:
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict) or not hooks:
        return "No hooks configured."
    rows: list[str] = [
        "Event | Matcher | Handler | Type",
        "--------------------------------",
    ]
    for event_name, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            matcher = str(group.get("matcher", "*"))
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                continue
            for handler in handlers:
                if not isinstance(handler, dict):
                    continue
                rows.append(
                    " | ".join(
                        [
                            str(event_name),
                            matcher,
                            str(
                                handler.get("name")
                                or handler.get("command")
                                or handler.get("url")
                                or ""
                            ),
                            str(handler.get("type") or ""),
                        ]
                    )
                )
    return "\n".join(rows)


def _require_object_response(
    payload: dict[str, object] | list[object],
    path: str,
) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    raise RuntimeError(f"Expected JSON object from {path}")
