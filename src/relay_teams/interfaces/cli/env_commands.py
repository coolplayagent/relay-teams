# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
import json

import typer

from relay_teams.env.env_cli import (
    EnvOutputFormat,
    collect_env_entries,
    render_env_table,
)

type RequestJsonCallable = Callable[
    [str, str, str, dict[str, object] | None], dict[str, object] | list[object]
]
type AutoStartCallable = Callable[[str, bool, bool, bool], None]


class ProbeOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


def build_env_app(
    *,
    request_json: RequestJsonCallable,
    auto_start_if_needed: AutoStartCallable,
    default_base_url: str,
) -> typer.Typer:
    env_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

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
            None,
            "--prefix",
            help="Only list environment variables with this key prefix.",
        ),
    ) -> None:
        entries = collect_env_entries(prefix=prefix, show_secrets=show_secrets)
        if output_format == EnvOutputFormat.JSON:
            typer.echo(json.dumps(entries, ensure_ascii=False))
            return
        render_env_table(entries)

    @env_app.command("proxy-reload")
    def proxy_reload(
        base_url: str = typer.Option(
            default_base_url,
            "--base-url",
            help="Agent Teams server base URL.",
        ),
        autostart: bool = typer.Option(
            True,
            "--autostart/--no-autostart",
            help="Auto-start local server when not already running.",
        ),
        daemon: bool = typer.Option(
            False,
            "--daemon",
            "-d",
            help="Run the server as a background process when autostarting.",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            help="Force kill any existing server process before autostarting.",
        ),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        result = request_json(
            base_url,
            "POST",
            "/api/system/configs/proxy:reload",
            None,
        )
        typer.echo(json.dumps(_require_object_response(result), ensure_ascii=False))

    @env_app.command("probe-web")
    def probe_web(
        url: str = typer.Argument(..., help="Target http/https URL to probe."),
        timeout_ms: int | None = typer.Option(
            None,
            "--timeout-ms",
            help="Optional request timeout in milliseconds.",
        ),
        output_format: ProbeOutputFormat = typer.Option(
            ProbeOutputFormat.TABLE,
            "--format",
            help="Output format: table or json.",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(
            default_base_url,
            "--base-url",
            help="Agent Teams server base URL.",
        ),
        autostart: bool = typer.Option(
            True,
            "--autostart/--no-autostart",
            help="Auto-start local server when not already running.",
        ),
        daemon: bool = typer.Option(
            False,
            "--daemon",
            "-d",
            help="Run the server as a background process when autostarting.",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            help="Force kill any existing server process before autostarting.",
        ),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        payload: dict[str, object] = {"url": url}
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        result = _require_object_response(
            request_json(
                base_url,
                "POST",
                "/api/system/configs/web:probe",
                payload,
            )
        )
        if output_format == ProbeOutputFormat.JSON:
            typer.echo(json.dumps(result, ensure_ascii=False))
            return
        _render_probe_table(result)

    return env_app


def _render_probe_table(payload: dict[str, object]) -> None:
    diagnostics = payload.get("diagnostics", {})
    redirected = False
    used_proxy = False
    if isinstance(diagnostics, dict):
        redirected = bool(diagnostics.get("redirected", False))
        used_proxy = bool(diagnostics.get("used_proxy", False))

    rows = [
        ("OK", str(payload.get("ok", False)).lower()),
        ("URL", str(payload.get("url", ""))),
        ("Final URL", str(payload.get("final_url", ""))),
        ("Method", str(payload.get("used_method", ""))),
        ("Status", str(payload.get("status_code", ""))),
        ("Latency(ms)", str(payload.get("latency_ms", ""))),
        ("Used Proxy", str(used_proxy).lower()),
        ("Redirected", str(redirected).lower()),
        ("Error Code", str(payload.get("error_code", ""))),
        ("Error", str(payload.get("error_message", ""))),
    ]
    key_width = max(len("Field"), *(len(row[0]) for row in rows))
    value_width = max(len("Value"), *(len(row[1]) for row in rows))
    border = f"+-{'-' * key_width}-+-{'-' * value_width}-+"
    typer.echo(border)
    typer.echo(f"| {'Field'.ljust(key_width)} | {'Value'.ljust(value_width)} |")
    typer.echo(border)
    for key, value in rows:
        typer.echo(f"| {key.ljust(key_width)} | {value.ljust(value_width)} |")
    typer.echo(border)


def _require_object_response(
    payload: dict[str, object] | list[object],
) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    raise RuntimeError("Expected JSON object from env command endpoint")
