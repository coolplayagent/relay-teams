# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
import json

import typer

type RequestJsonCallable = Callable[
    [str, str, str, dict[str, object] | None], dict[str, object] | list[object]
]
type AutoStartCallable = Callable[[str, bool], None]


def build_approvals_app(
    *,
    request_json: RequestJsonCallable,
    auto_start_if_needed: AutoStartCallable,
    default_base_url: str,
) -> typer.Typer:
    approvals_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

    @approvals_app.command("list")
    def tool_approvals_list(
        run_id: str = typer.Option(..., "--run-id"),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        result = request_json(base_url, "GET", f"/api/runs/{run_id}/tool-approvals", None)
        approvals = result if isinstance(result, list) else result.get("data", [])
        typer.echo(json.dumps(approvals, ensure_ascii=False))

    @approvals_app.command("resolve")
    def tool_approvals_resolve(
        run_id: str = typer.Option(..., "--run-id"),
        tool_call_id: str = typer.Option(..., "--tool-call-id"),
        action: str = typer.Option(..., "--action", help="approve or deny"),
        feedback: str = typer.Option("", "--feedback"),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        if action not in {"approve", "deny"}:
            raise typer.BadParameter("action must be approve or deny")
        result = request_json(
            base_url,
            "POST",
            f"/api/runs/{run_id}/tool-approvals/{tool_call_id}/resolve",
            {"action": action, "feedback": feedback},
        )
        typer.echo(json.dumps(result, ensure_ascii=False))

    return approvals_app
