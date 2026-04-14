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
        typer.echo(_render_hooks_table(response))

    @hooks_app.command("validate")
    def validate_hooks(
        output_format: HooksOutputFormat = typer.Option(
            HooksOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        payload = request_json(
            base_url,
            "POST",
            "/api/system/configs/hooks:validate",
            None,
        )
        response = _require_object_response(
            payload,
            "/api/system/configs/hooks:validate",
        )
        if output_format == HooksOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(_render_validation_table(response))

    return hooks_app


def _render_hooks_table(payload: dict[str, object]) -> str:
    summary = payload.get("summary")
    rows = [
        ("Config Path", str(payload.get("config_path", ""))),
        ("Exists", _fmt_bool(payload.get("exists"))),
        ("Events", _summary_value(summary, "event_count")),
        ("Matcher Groups", _summary_value(summary, "matcher_group_count")),
        ("Handlers", _summary_value(summary, "handler_count")),
    ]
    width = max(len(name) for name, _ in rows)
    return "\n".join(f"{name.ljust(width)} : {value}" for name, value in rows)


def _render_validation_table(payload: dict[str, object]) -> str:
    summary = payload.get("summary")
    rows = [
        ("Valid", _fmt_bool(payload.get("valid"))),
        ("Config Path", str(payload.get("config_path", ""))),
        ("Exists", _fmt_bool(payload.get("exists"))),
        ("Events", _summary_value(summary, "event_count")),
        ("Matcher Groups", _summary_value(summary, "matcher_group_count")),
        ("Handlers", _summary_value(summary, "handler_count")),
        ("Error", str(payload.get("error", "") or "-")),
    ]
    width = max(len(name) for name, _ in rows)
    return "\n".join(f"{name.ljust(width)} : {value}" for name, value in rows)


def _summary_value(summary: object, key: str) -> str:
    if not isinstance(summary, dict):
        return "0"
    value = summary.get(key, 0)
    return str(value if isinstance(value, int) else 0)


def _fmt_bool(value: object) -> str:
    return "yes" if value is True else "no"


def _require_object_response(
    payload: dict[str, object] | list[object],
    path: str,
) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    raise RuntimeError(f"Expected JSON object from {path}")
