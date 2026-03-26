# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
import json
import os
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from typing import TypedDict

import typer

from agent_teams.env.proxy_env import (
    load_proxy_env_config,
    sync_proxy_env_to_process_env,
)
from agent_teams.env.runtime_env import (
    get_app_env_file_path,
    get_project_env_file_path,
    get_user_env_file_path,
    load_env_file,
    load_secret_env_vars,
)
from agent_teams.secrets import is_sensitive_env_key as _is_sensitive_env_key

env_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

MASKED_VALUE = "<masked>"
TABLE_MAX_KEY_WIDTH = 42
TABLE_MAX_VALUE_WIDTH = 96
TABLE_MIN_VALUE_WIDTH = 24
TABLE_FALLBACK_TERMINAL_WIDTH = 120
DEFAULT_BASE_URL = "http://127.0.0.1:8000"


class EnvOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


class EnvListEntry(TypedDict):
    key: str
    value: str
    source: str
    masked: bool


class ProbeOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


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
        None, "--prefix", help="Only list environment variables with this key prefix."
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
        DEFAULT_BASE_URL,
        "--base-url",
        help="Agent Teams server base URL.",
    ),
    autostart: bool = typer.Option(
        True,
        "--autostart/--no-autostart",
        help="Auto-start local server when not already running.",
    ),
) -> None:
    _auto_start_if_needed(base_url, autostart)
    payload = _request_json(base_url, "POST", "/api/system/configs/proxy:reload")
    typer.echo(json.dumps(payload, ensure_ascii=False))


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
        DEFAULT_BASE_URL,
        "--base-url",
        help="Agent Teams server base URL.",
    ),
    autostart: bool = typer.Option(
        True,
        "--autostart/--no-autostart",
        help="Auto-start local server when not already running.",
    ),
) -> None:
    _auto_start_if_needed(base_url, autostart)
    payload: dict[str, object] = {"url": url}
    if timeout_ms is not None:
        payload["timeout_ms"] = timeout_ms
    result = _request_json(base_url, "POST", "/api/system/configs/web:probe", payload)
    if output_format == ProbeOutputFormat.JSON:
        typer.echo(json.dumps(result, ensure_ascii=False))
        return
    _render_probe_table(result)


def collect_env_entries(
    *, prefix: str | None, show_secrets: bool
) -> list[EnvListEntry]:
    _ = (get_user_env_file_path, get_project_env_file_path)
    app_env = load_env_file(get_app_env_file_path())
    app_env.update(load_secret_env_vars(get_app_env_file_path().parent))
    process_env = dict(os.environ)

    merged: dict[str, str] = {}
    source_by_key: dict[str, str] = {}

    merge_env_source(merged, source_by_key, app_env, "app")
    merge_env_source(merged, source_by_key, process_env, "process")

    entries: list[EnvListEntry] = []
    for key in sorted(merged):
        if prefix and not key.startswith(prefix):
            continue

        is_masked = (not show_secrets) and _is_sensitive_env_key(key)
        displayed_value = MASKED_VALUE if is_masked else merged[key]
        entries.append(
            EnvListEntry(
                key=key,
                value=displayed_value,
                source=source_by_key.get(key, "unknown"),
                masked=is_masked,
            )
        )
    return entries


def merge_env_source(
    merged: dict[str, str],
    source_by_key: dict[str, str],
    values: dict[str, str],
    source: str,
) -> None:
    for key, value in values.items():
        merged[key] = value
        source_by_key[key] = source


def is_sensitive_env_key(key: str) -> bool:
    return _is_sensitive_env_key(key)


def render_env_table(entries: list[EnvListEntry]) -> None:
    if not entries:
        typer.echo("No environment variables matched.")
        return

    total_count = len(entries)
    masked_count = sum(1 for entry in entries if entry["masked"])
    typer.echo(f"Environment Variables ({total_count} total, {masked_count} masked)")

    key_width = min(
        TABLE_MAX_KEY_WIDTH,
        max(len("Key"), *(len(entry["key"]) for entry in entries)),
    )
    source_width = max(len("Source"), *(len(entry["source"]) for entry in entries))
    value_width = resolve_value_column_width(entries, key_width, source_width)

    border = f"+-{'-' * key_width}-+-{'-' * source_width}-+-{'-' * value_width}-+"
    typer.echo(border)
    typer.echo(
        f"| {'Key'.ljust(key_width)} | "
        f"{'Source'.ljust(source_width)} | "
        f"{'Value'.ljust(value_width)} |"
    )
    typer.echo(border)

    for entry in entries:
        key_cell = truncate_for_table(entry["key"], key_width)
        source_cell = truncate_for_table(entry["source"], source_width)
        value_cell = truncate_for_table(entry["value"], value_width)
        typer.echo(
            f"| {key_cell.ljust(key_width)} | "
            f"{source_cell.ljust(source_width)} | "
            f"{value_cell.ljust(value_width)} |"
        )

    typer.echo(border)


def resolve_value_column_width(
    entries: list[EnvListEntry], key_width: int, source_width: int
) -> int:
    max_value_width = max(len("Value"), *(len(entry["value"]) for entry in entries))
    terminal_width = shutil.get_terminal_size(
        fallback=(TABLE_FALLBACK_TERMINAL_WIDTH, 24)
    ).columns
    frame_overhead = key_width + source_width + 10
    available_width = max(TABLE_MIN_VALUE_WIDTH, terminal_width - frame_overhead)
    bounded_by_content = min(TABLE_MAX_VALUE_WIDTH, max_value_width)
    return max(TABLE_MIN_VALUE_WIDTH, min(available_width, bounded_by_content))


def truncate_for_table(value: str, width: int) -> str:
    if width <= 3:
        return value[:width]
    if len(value) <= width:
        return value
    return f"{value[: width - 3]}..."


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


def _request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, object]:
    sync_proxy_env_to_process_env(load_proxy_env_config())
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(
        url=f"{base_url.rstrip('/')}{path}",
        method=method,
        data=body,
        headers=headers,
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                return {}
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
            return {"data": data}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code} {method} {path}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to connect to {base_url}: {exc}") from exc


def _is_server_healthy(base_url: str) -> bool:
    try:
        response = _request_json(
            base_url,
            "GET",
            "/api/system/health",
            timeout_seconds=1.5,
        )
        return response.get("status") == "ok"
    except Exception:
        return False


def _auto_start_if_needed(base_url: str, autostart: bool) -> None:
    if _is_server_healthy(base_url):
        return
    if not autostart:
        raise RuntimeError(
            "Agent Teams server is not running and --no-autostart was provided"
        )

    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError(
            f"Refusing to autostart server for non-local base URL: {base_url}"
        )

    _start_server_daemon(host=host, port=port)
    if not _wait_until_healthy(base_url):
        raise RuntimeError("Failed to start local Agent Teams server")


def _start_server_daemon(host: str, port: int) -> None:
    sync_proxy_env_to_process_env(load_proxy_env_config())
    command = [
        sys.executable,
        "-m",
        "agent_teams",
        "server",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if sys.platform.startswith("win"):
        create_new_process_group = int(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        detached_process = int(getattr(subprocess, "DETACHED_PROCESS", 0))
        create_no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        creationflags = create_new_process_group | detached_process | create_no_window
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
        startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0))
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        return

    subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def _wait_until_healthy(base_url: str, timeout_seconds: float = 20.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _is_server_healthy(base_url):
            return True
        time.sleep(0.25)
    return False
