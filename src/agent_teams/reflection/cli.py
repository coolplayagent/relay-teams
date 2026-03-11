# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
import json

import typer

from agent_teams.reflection.models import DailyMemoryKind

type RequestJsonCallable = Callable[
    [str, str, str, dict[str, object] | None], dict[str, object] | list[object]
]
type AutoStartCallable = Callable[[str, bool], None]


class ReflectionOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


def build_reflection_app(
    *,
    request_json: RequestJsonCallable,
    auto_start_if_needed: AutoStartCallable,
    default_base_url: str,
) -> typer.Typer:
    reflection_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
    jobs_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
    memory_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
    daily_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

    @jobs_app.command("list")
    def jobs_list(
        limit: int = typer.Option(50, "--limit"),
        output_format: ReflectionOutputFormat = typer.Option(
            ReflectionOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        result = request_json(
            base_url, "GET", f"/api/reflection/jobs?limit={limit}", None
        )
        if output_format == ReflectionOutputFormat.JSON:
            typer.echo(json.dumps(result, ensure_ascii=False))
            return
        _render_jobs_table(_require_list_response(result, "/api/reflection/jobs"))

    @jobs_app.command("retry")
    def jobs_retry(
        job_id: str = typer.Option(..., "--job-id"),
        output_format: ReflectionOutputFormat = typer.Option(
            ReflectionOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        result = request_json(
            base_url,
            "POST",
            f"/api/reflection/jobs/{job_id}/retry",
            {},
        )
        if output_format == ReflectionOutputFormat.JSON:
            typer.echo(json.dumps(result, ensure_ascii=False))
            return
        _render_jobs_table(
            [_require_object_response(result, f"/api/reflection/jobs/{job_id}/retry")]
        )

    @memory_app.command("show")
    def memory_show(
        session_id: str = typer.Option(..., "--session-id"),
        role_id: str = typer.Option(..., "--role-id"),
        output_format: ReflectionOutputFormat = typer.Option(
            ReflectionOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        result = request_json(
            base_url,
            "GET",
            f"/api/reflection/memory/session-roles/{session_id}/{role_id}",
            None,
        )
        _render_memory_response(
            payload=_require_object_response(
                result,
                f"/api/reflection/memory/session-roles/{session_id}/{role_id}",
            ),
            output_format=output_format,
        )

    @daily_app.command("show")
    def daily_show(
        instance_id: str = typer.Option(..., "--instance-id"),
        memory_date: str = typer.Option(..., "--date"),
        kind: DailyMemoryKind = typer.Option(
            DailyMemoryKind.DIGEST,
            "--kind",
            case_sensitive=False,
        ),
        output_format: ReflectionOutputFormat = typer.Option(
            ReflectionOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        result = request_json(
            base_url,
            "GET",
            f"/api/reflection/memory/instances/{instance_id}/daily/{memory_date}?kind={kind.value}",
            None,
        )
        _render_memory_response(
            payload=_require_object_response(
                result,
                f"/api/reflection/memory/instances/{instance_id}/daily/{memory_date}",
            ),
            output_format=output_format,
        )

    reflection_app.add_typer(jobs_app, name="jobs")
    reflection_app.add_typer(memory_app, name="memory")
    reflection_app.add_typer(daily_app, name="daily")
    return reflection_app


def _render_jobs_table(rows: list[dict[str, object]]) -> None:
    headers = ("job_id", "job_type", "status", "role_id", "instance_id", "trigger_date")
    typer.echo(" | ".join(headers))
    for row in rows:
        typer.echo(" | ".join(str(row.get(header, "") or "") for header in headers))


def _render_memory_response(
    *,
    payload: dict[str, object],
    output_format: ReflectionOutputFormat,
) -> None:
    if output_format == ReflectionOutputFormat.JSON:
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return
    typer.echo(f"path: {payload.get('path', '')}")
    typer.echo(f"exists: {payload.get('exists', False)}")
    typer.echo("")
    typer.echo(str(payload.get("content", "")))


def _require_object_response(
    payload: dict[str, object] | list[object],
    path: str,
) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    raise RuntimeError(f"Expected JSON object from {path}")


def _require_list_response(
    payload: dict[str, object] | list[object],
    path: str,
) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        raise RuntimeError(f"Expected JSON list from {path}")
    rows: list[dict[str, object]] = []
    for entry in payload:
        if isinstance(entry, dict):
            rows.append(entry)
    return rows
