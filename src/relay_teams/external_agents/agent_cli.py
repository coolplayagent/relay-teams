# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
import json
from urllib.parse import quote

import typer

type RequestJsonCallable = Callable[
    [str, str, str, dict[str, object] | None], dict[str, object] | list[object]
]
type AutoStartCallable = Callable[[str, bool, bool, bool], None]


class AgentOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


def build_agent_runtimes_app(
    *,
    request_json: RequestJsonCallable,
    auto_start_if_needed: AutoStartCallable,
    default_base_url: str,
) -> typer.Typer:
    agent_runtimes_app = typer.Typer(
        no_args_is_help=True,
        pretty_exceptions_enable=False,
    )

    @agent_runtimes_app.command("list")
    def agent_runtimes_list(
        output_format: AgentOutputFormat = typer.Option(
            AgentOutputFormat.TABLE,
            "--format",
            help="Render as an ASCII table or JSON.",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
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
        payload = request_json(
            base_url,
            "GET",
            "/api/system/configs/agent-runtimes",
            None,
        )
        items = _require_list_response(payload, "/api/system/configs/agent-runtimes")
        if output_format == AgentOutputFormat.JSON:
            typer.echo(json.dumps(items, ensure_ascii=False))
            return
        _render_agent_summary_table(items)

    @agent_runtimes_app.command("get")
    def agent_runtimes_get(
        agent_id: str = typer.Argument(..., help="Agent runtime id."),
        output_format: AgentOutputFormat = typer.Option(
            AgentOutputFormat.TABLE,
            "--format",
            help="Render as an ASCII table or JSON.",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
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
        payload = request_json(
            base_url,
            "GET",
            _agent_runtime_path(agent_id),
            None,
        )
        data = _require_object_response(payload, _agent_runtime_path(agent_id))
        if output_format == AgentOutputFormat.JSON:
            typer.echo(json.dumps(data, ensure_ascii=False))
            return
        _render_agent_detail(data)

    @agent_runtimes_app.command("save")
    def agent_runtimes_save(
        agent_id: str = typer.Argument(..., help="Agent runtime id."),
        config_json: str = typer.Option(
            ...,
            "--config-json",
            help="Full agent runtime config JSON payload.",
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
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
        payload = _parse_config_json(config_json)
        result = request_json(
            base_url,
            "PUT",
            _agent_runtime_path(agent_id),
            payload,
        )
        typer.echo(
            json.dumps(
                _require_object_response(result, "agent-runtimes save"),
                ensure_ascii=False,
            )
        )

    @agent_runtimes_app.command("delete")
    def agent_runtimes_delete(
        agent_id: str = typer.Argument(..., help="Agent runtime id."),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
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
            "DELETE",
            _agent_runtime_path(agent_id),
            None,
        )
        typer.echo(
            json.dumps(
                _require_object_response(result, "agent-runtimes delete"),
                ensure_ascii=False,
            )
        )

    @agent_runtimes_app.command("test")
    def agent_runtimes_test(
        agent_id: str = typer.Argument(..., help="Agent runtime id."),
        output_format: AgentOutputFormat = typer.Option(
            AgentOutputFormat.TABLE,
            "--format",
            help="Render as an ASCII table or JSON.",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
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
        payload = request_json(
            base_url,
            "POST",
            _agent_runtime_path(agent_id, suffix=":test"),
            None,
        )
        data = _require_object_response(
            payload, _agent_runtime_path(agent_id, suffix=":test")
        )
        if output_format == AgentOutputFormat.JSON:
            typer.echo(json.dumps(data, ensure_ascii=False))
            return
        _render_test_result(agent_id, data)

    return agent_runtimes_app


def _agent_runtime_path(agent_id: str, *, suffix: str = "") -> str:
    encoded_agent_id = quote(agent_id, safe="")
    return f"/api/system/configs/agent-runtimes/{encoded_agent_id}{suffix}"


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


def _render_agent_summary_table(items: list[dict[str, object]]) -> None:
    if not items:
        typer.echo("No agent runtimes configured.")
        return
    id_width = max(
        len("Agent ID"), *(len(str(item.get("agent_id") or "")) for item in items)
    )
    name_width = max(len("Name"), *(len(str(item.get("name") or "")) for item in items))
    protocol_width = max(
        len("Protocol"),
        *(len(str(item.get("protocol") or "")) for item in items),
    )
    transport_width = max(
        len("Transport"),
        *(len(str(item.get("transport") or "")) for item in items),
    )
    border = (
        f"+-{'-' * id_width}-+-{'-' * name_width}-+-{'-' * protocol_width}-+-"
        f"{'-' * transport_width}-+"
    )
    typer.echo(border)
    typer.echo(
        f"| {'Agent ID'.ljust(id_width)} | {'Name'.ljust(name_width)} | "
        f"{'Protocol'.ljust(protocol_width)} | "
        f"{'Transport'.ljust(transport_width)} |"
    )
    typer.echo(border)
    for item in items:
        typer.echo(
            f"| {str(item.get('agent_id') or '').ljust(id_width)} | "
            f"{str(item.get('name') or '').ljust(name_width)} | "
            f"{str(item.get('protocol') or '').ljust(protocol_width)} | "
            f"{str(item.get('transport') or '').ljust(transport_width)} |"
        )
    typer.echo(border)


def _render_agent_detail(item: dict[str, object]) -> None:
    typer.echo(f"Agent ID: {item.get('agent_id', '')}")
    typer.echo(f"Name: {item.get('name', '')}")
    typer.echo(f"Description: {item.get('description', '')}")
    typer.echo(f"Protocol: {item.get('protocol', 'acp')}")
    transport = item.get("transport")
    if isinstance(transport, dict):
        typer.echo(f"Transport: {transport.get('transport', '')}")
        typer.echo(json.dumps(transport, ensure_ascii=False, indent=2))
        return
    typer.echo(f"Transport: {transport}")


def _render_test_result(agent_id: str, item: dict[str, object]) -> None:
    typer.echo(f"Agent Runtime: {agent_id}")
    typer.echo(f"OK: {item.get('ok', False)}")
    typer.echo(f"Protocol: {item.get('protocol', 'acp')}")
    message = str(item.get("message") or "").strip()
    if message:
        typer.echo(f"Message: {message}")
    if item.get("agent_name"):
        typer.echo(f"Agent Name: {item.get('agent_name')}")
    if item.get("agent_version"):
        typer.echo(f"Agent Version: {item.get('agent_version')}")
    if item.get("protocol_version") is not None:
        typer.echo(f"Protocol Version: {item.get('protocol_version')}")
    if item.get("protocol_version_text"):
        typer.echo(f"Protocol Version: {item.get('protocol_version_text')}")
