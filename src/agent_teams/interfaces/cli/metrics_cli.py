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


class MetricsOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"
    PRETTYLOG = "prettylog"


class MetricsScope(str, Enum):
    GLOBAL = "global"
    SESSION = "session"
    RUN = "run"


def build_metrics_app(
    *,
    request_json: RequestJsonCallable,
    auto_start_if_needed: AutoStartCallable,
    default_base_url: str,
) -> typer.Typer:
    metrics_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

    @metrics_app.command("overview")
    def metrics_overview(
        scope: MetricsScope = typer.Option(MetricsScope.GLOBAL, "--scope"),
        scope_id: str = typer.Option("", "--scope-id"),
        time_window_minutes: int = typer.Option(1440, "--window-minutes", min=1),
        output_format: MetricsOutputFormat = typer.Option(
            MetricsOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        _validate_scope(scope=scope, scope_id=scope_id)
        auto_start_if_needed(base_url, autostart)
        payload = request_json(
            base_url,
            "GET",
            f"/api/observability/overview?scope={scope.value}&scope_id={scope_id}&time_window_minutes={time_window_minutes}",
            None,
        )
        response = _require_object_response(payload, "/api/observability/overview")
        if output_format == MetricsOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        if output_format == MetricsOutputFormat.PRETTYLOG:
            typer.echo(_render_prettylog_overview(response))
            return
        typer.echo(_render_overview_table(response))

    @metrics_app.command("breakdowns")
    def metrics_breakdowns(
        scope: MetricsScope = typer.Option(MetricsScope.GLOBAL, "--scope"),
        scope_id: str = typer.Option("", "--scope-id"),
        time_window_minutes: int = typer.Option(1440, "--window-minutes", min=1),
        output_format: MetricsOutputFormat = typer.Option(
            MetricsOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        _validate_scope(scope=scope, scope_id=scope_id)
        auto_start_if_needed(base_url, autostart)
        payload = request_json(
            base_url,
            "GET",
            f"/api/observability/breakdowns?scope={scope.value}&scope_id={scope_id}&time_window_minutes={time_window_minutes}",
            None,
        )
        response = _require_object_response(payload, "/api/observability/breakdowns")
        if output_format == MetricsOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        if output_format == MetricsOutputFormat.PRETTYLOG:
            typer.echo(_render_prettylog_breakdowns(response))
            return
        typer.echo(_render_breakdowns_table(response))

    @metrics_app.command("tail")
    def metrics_tail(
        scope: MetricsScope = typer.Option(MetricsScope.GLOBAL, "--scope"),
        scope_id: str = typer.Option("", "--scope-id"),
        time_window_minutes: int = typer.Option(60, "--window-minutes", min=1),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        _validate_scope(scope=scope, scope_id=scope_id)
        auto_start_if_needed(base_url, autostart)
        payload = request_json(
            base_url,
            "GET",
            f"/api/observability/overview?scope={scope.value}&scope_id={scope_id}&time_window_minutes={time_window_minutes}",
            None,
        )
        response = _require_object_response(payload, "/api/observability/overview")
        typer.echo(_render_prettylog_overview(response))

    return metrics_app


def _validate_scope(*, scope: MetricsScope, scope_id: str) -> None:
    if scope != MetricsScope.GLOBAL and not scope_id.strip():
        raise typer.BadParameter("--scope-id is required when scope is session or run")


def _render_overview_table(payload: dict[str, object]) -> str:
    kpis = payload.get("kpis")
    if not isinstance(kpis, dict):
        return "No metrics available."
    rows = [
        ("Scope", str(payload.get("scope", "global"))),
        ("Scope ID", str(payload.get("scope_id", "") or "-")),
        ("Steps", _fmt_num(kpis.get("steps"))),
        ("Input Tokens", _fmt_num(kpis.get("input_tokens"))),
        ("Cached Tokens", _fmt_num(kpis.get("cached_input_tokens"))),
        ("Output Tokens", _fmt_num(kpis.get("output_tokens"))),
        ("Cached Ratio", _fmt_ratio(kpis.get("cached_token_ratio"))),
        ("Tool Calls", _fmt_num(kpis.get("tool_calls"))),
        ("Tool Success", _fmt_ratio(kpis.get("tool_success_rate"))),
        ("Avg Tool ms", _fmt_num(kpis.get("tool_avg_duration_ms"))),
        ("Skill Calls", _fmt_num(kpis.get("skill_calls"))),
        ("MCP Calls", _fmt_num(kpis.get("mcp_calls"))),
    ]
    width = max(len(name) for name, _ in rows)
    return "\n".join(f"{name.ljust(width)} : {value}" for name, value in rows)


def _render_breakdowns_table(payload: dict[str, object]) -> str:
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        return "No breakdown rows available."
    header = "Tool | Source | Calls | Success | Avg ms"
    body = [header, "-" * len(header)]
    for row in rows:
        if not isinstance(row, dict):
            continue
        body.append(
            " | ".join(
                [
                    str(row.get("tool_name", "")),
                    str(row.get("tool_source", "local") or "local"),
                    _fmt_num(row.get("calls")),
                    _fmt_ratio(row.get("success_rate")),
                    _fmt_num(row.get("avg_duration_ms")),
                ]
            )
        )
    return "\n".join(body)


def _render_prettylog_overview(payload: dict[str, object]) -> str:
    kpis = payload.get("kpis")
    if not isinstance(kpis, dict):
        return "[metrics] no overview data"
    return (
        f"[metrics] scope={payload.get('scope')} scope_id={payload.get('scope_id') or '-'} "
        f"steps={_fmt_num(kpis.get('steps'))} input={_fmt_num(kpis.get('input_tokens'))} "
        f"output={_fmt_num(kpis.get('output_tokens'))} tool_calls={_fmt_num(kpis.get('tool_calls'))} "
        f"tool_success={_fmt_ratio(kpis.get('tool_success_rate'))}"
    )


def _render_prettylog_breakdowns(payload: dict[str, object]) -> str:
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        return "[metrics] no breakdown data"
    lines: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"[metrics] tool={row.get('tool_name')} source={row.get('tool_source') or 'local'} "
            f"calls={_fmt_num(row.get('calls'))} success={_fmt_ratio(row.get('success_rate'))} "
            f"avg_ms={_fmt_num(row.get('avg_duration_ms'))}"
        )
    return "\n".join(lines)


def _fmt_num(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}" if isinstance(value, float) else str(value)
    return "0"


def _fmt_ratio(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value) * 100:.1f}%"
    return "0.0%"


def _require_object_response(
    payload: dict[str, object] | list[object],
    path: str,
) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    raise RuntimeError(f"Expected JSON object from {path}")
