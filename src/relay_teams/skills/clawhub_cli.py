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


class ClawHubOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


def build_clawhub_app(
    *,
    request_json: RequestJsonCallable,
    auto_start_if_needed: AutoStartCallable,
    default_base_url: str,
) -> typer.Typer:
    clawhub_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
    config_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
    skills_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

    @config_app.command("get")
    def config_get(
        output_format: ClawHubOutputFormat = typer.Option(
            ClawHubOutputFormat.TABLE,
            "--format",
            help="Render as an ASCII table or JSON.",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        payload = request_json(base_url, "GET", "/api/system/configs/clawhub", None)
        data = _require_object_response(payload, "/api/system/configs/clawhub")
        if output_format == ClawHubOutputFormat.JSON:
            typer.echo(json.dumps(data, ensure_ascii=False))
            return
        token_value = str(data.get("token") or "")
        typer.echo("ClawHub Config")
        typer.echo(f"Token configured: {'yes' if token_value else 'no'}")

    @config_app.command("save")
    def config_save(
        token: str | None = typer.Option(
            None,
            "--token",
            help="Persist the ClawHub token. Omit with --clear-token to remove it.",
        ),
        clear_token: bool = typer.Option(
            False,
            "--clear-token",
            help="Clear the saved ClawHub token.",
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        if token is not None and clear_token:
            raise typer.BadParameter(
                "--token and --clear-token cannot be used together"
            )
        auto_start_if_needed(base_url, autostart)
        payload: dict[str, object] = {"token": None if clear_token else token}
        result = request_json(
            base_url,
            "PUT",
            "/api/system/configs/clawhub",
            payload,
        )
        typer.echo(
            json.dumps(
                _require_object_response(result, "/api/system/configs/clawhub"),
                ensure_ascii=False,
            )
        )

    @skills_app.command("list")
    def skills_list(
        output_format: ClawHubOutputFormat = typer.Option(
            ClawHubOutputFormat.TABLE,
            "--format",
            help="Render as an ASCII table or JSON.",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        payload = request_json(
            base_url,
            "GET",
            "/api/system/configs/clawhub/skills",
            None,
        )
        items = _require_list_response(payload, "/api/system/configs/clawhub/skills")
        if output_format == ClawHubOutputFormat.JSON:
            typer.echo(json.dumps(items, ensure_ascii=False))
            return
        _render_skill_summary_table(items)

    @skills_app.command("get")
    def skills_get(
        skill_id: str = typer.Argument(..., help="ClawHub skill directory id."),
        output_format: ClawHubOutputFormat = typer.Option(
            ClawHubOutputFormat.TABLE,
            "--format",
            help="Render as an ASCII table or JSON.",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        payload = request_json(
            base_url,
            "GET",
            f"/api/system/configs/clawhub/skills/{skill_id}",
            None,
        )
        data = _require_object_response(
            payload, f"/api/system/configs/clawhub/skills/{skill_id}"
        )
        if output_format == ClawHubOutputFormat.JSON:
            typer.echo(json.dumps(data, ensure_ascii=False))
            return
        _render_skill_detail(data)

    @skills_app.command("save")
    def skills_save(
        skill_id: str = typer.Argument(..., help="ClawHub skill directory id."),
        config_json: str = typer.Option(
            ...,
            "--config-json",
            help="Full ClawHub skill JSON payload.",
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        payload = _parse_config_json(config_json)
        result = request_json(
            base_url,
            "PUT",
            f"/api/system/configs/clawhub/skills/{skill_id}",
            payload,
        )
        typer.echo(
            json.dumps(
                _require_object_response(
                    result, f"/api/system/configs/clawhub/skills/{skill_id}"
                ),
                ensure_ascii=False,
            )
        )

    @skills_app.command("delete")
    def skills_delete(
        skill_id: str = typer.Argument(..., help="ClawHub skill directory id."),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        result = request_json(
            base_url,
            "DELETE",
            f"/api/system/configs/clawhub/skills/{skill_id}",
            None,
        )
        typer.echo(
            json.dumps(
                _require_object_response(
                    result, f"/api/system/configs/clawhub/skills/{skill_id}"
                ),
                ensure_ascii=False,
            )
        )

    clawhub_app.add_typer(config_app, name="config")
    clawhub_app.add_typer(skills_app, name="skills")
    return clawhub_app


def _parse_config_json(raw: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter("--config-json must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("--config-json must be a JSON object")
    return {str(key): value for key, value in parsed.items()}


def _require_list_response(
    payload: dict[str, object] | list[object], path: str
) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    raise RuntimeError(f"Expected JSON array from {path}")


def _require_object_response(
    payload: dict[str, object] | list[object], path: str
) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    raise RuntimeError(f"Expected JSON object from {path}")


def _render_skill_summary_table(items: list[dict[str, object]]) -> None:
    if not items:
        typer.echo("No ClawHub skills configured.")
        return
    id_width = max(
        len("Skill ID"), *(len(str(item.get("skill_id") or "")) for item in items)
    )
    name_width = max(
        len("Runtime Name"),
        *(len(str(item.get("runtime_name") or "")) for item in items),
    )
    valid_width = max(
        len("Valid"), *(len("yes" if item.get("valid") else "no") for item in items)
    )
    border = f"+-{'-' * id_width}-+-{'-' * name_width}-+-{'-' * valid_width}-+"
    typer.echo(border)
    typer.echo(
        f"| {'Skill ID'.ljust(id_width)} | "
        f"{'Runtime Name'.ljust(name_width)} | "
        f"{'Valid'.ljust(valid_width)} |"
    )
    typer.echo(border)
    for item in items:
        typer.echo(
            f"| {str(item.get('skill_id') or '').ljust(id_width)} | "
            f"{str(item.get('runtime_name') or '').ljust(name_width)} | "
            f"{('yes' if item.get('valid') else 'no').ljust(valid_width)} |"
        )
    typer.echo(border)


def _render_skill_detail(item: dict[str, object]) -> None:
    typer.echo(f"Skill ID: {item.get('skill_id', '')}")
    typer.echo(f"Runtime Name: {item.get('runtime_name', '')}")
    typer.echo(f"Ref: {item.get('ref', '')}")
    typer.echo(f"Valid: {item.get('valid', False)}")
    if item.get("error"):
        typer.echo(f"Error: {item.get('error')}")
    typer.echo(f"Description: {item.get('description', '')}")
    typer.echo(f"Instructions: {item.get('instructions', '')}")
    files = item.get("files")
    if isinstance(files, list):
        typer.echo(f"Files: {len(files)}")
