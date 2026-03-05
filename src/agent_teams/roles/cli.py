# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
import json

import typer

type RequestJsonCallable = Callable[
    [str, str, str, dict[str, object] | None], dict[str, object] | list[object]
]
type AutoStartCallable = Callable[[str, bool], None]


def build_roles_app(
    *,
    request_json: RequestJsonCallable,
    auto_start_if_needed: AutoStartCallable,
    default_base_url: str,
) -> typer.Typer:
    roles_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

    @roles_app.command("validate")
    def roles_validate(
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        result = request_json(base_url, "POST", "/api/roles:validate", {})
        typer.echo(json.dumps(result, ensure_ascii=False))

    return roles_app
