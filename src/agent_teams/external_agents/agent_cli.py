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


class AgentOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


def build_external_agents_app(
    *,
    request_json: RequestJsonCallable,
    auto_start_if_needed: AutoStartCallable,
    default_base_url: str,
) -> typer.Typer:
    agents_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

    @agents_app.command("list")
    def agents_list(
        output_format: AgentOutputFormat = typer.Option(
            AgentOutputFormat.TABLE,
            "--format",
            help="Render as an ASCII table or JSON.",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        payload = request_json(base_url, "GET", "/api/system/configs/agents", None)
        items = _require_list_response(payload, "/api/system/configs/agents")
        if output_format == AgentOutputFormat.JSON:
            typer.echo(json.dumps(items, ensure_ascii=False))
            return
        _render_agent_summary_table(items)

    @agents_app.command("get")
    def agents_get(
        agent_id: str = typer.Argument(..., help="External agent id."),
        output_format: AgentOutputFormat = typer.Option(
            AgentOutputFormat.TABLE,
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
            f"/api/system/configs/agents/{agent_id}",
            None,
        )
        data = _require_object_response(
            payload, f"/api/system/configs/agents/{agent_id}"
        )
        if output_format == AgentOutputFormat.JSON:
            typer.echo(json.dumps(data, ensure_ascii=False))
            return
        _render_agent_detail(data)

    @agents_app.command("save")
    def agents_save(
        agent_id: str = typer.Argument(..., help="External agent id."),
        config_json: str = typer.Option(
            ...,
            "--config-json",
            help="Full ExternalAgentConfig JSON payload.",
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        payload = _parse_config_json(config_json)
        result = request_json(
            base_url,
            "PUT",
            f"/api/system/configs/agents/{agent_id}",
            payload,
        )
        typer.echo(
            json.dumps(
                _require_object_response(result, "agents save"), ensure_ascii=False
            )
        )

    @agents_app.command("delete")
    def agents_delete(
        agent_id: str = typer.Argument(..., help="External agent id."),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        result = request_json(
            base_url,
            "DELETE",
            f"/api/system/configs/agents/{agent_id}",
            None,
        )
        typer.echo(
            json.dumps(
                _require_object_response(result, "agents delete"), ensure_ascii=False
            )
        )

    @agents_app.command("test")
    def agents_test(
        agent_id: str = typer.Argument(..., help="External agent id."),
        output_format: AgentOutputFormat = typer.Option(
            AgentOutputFormat.TABLE,
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
            "POST",
            f"/api/system/configs/agents/{agent_id}:test",
            None,
        )
        data = _require_object_response(
            payload, f"/api/system/configs/agents/{agent_id}:test"
        )
        if output_format == AgentOutputFormat.JSON:
            typer.echo(json.dumps(data, ensure_ascii=False))
            return
        _render_test_result(agent_id, data)

    return agents_app


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
        typer.echo("No external agents configured.")
        return
    id_width = max(
        len("Agent ID"), *(len(str(item.get("agent_id") or "")) for item in items)
    )
    name_width = max(len("Name"), *(len(str(item.get("name") or "")) for item in items))
    transport_width = max(
        len("Transport"),
        *(len(str(item.get("transport") or "")) for item in items),
    )
    border = f"+-{'-' * id_width}-+-{'-' * name_width}-+-{'-' * transport_width}-+"
    typer.echo(border)
    typer.echo(
        f"| {'Agent ID'.ljust(id_width)} | {'Name'.ljust(name_width)} | {'Transport'.ljust(transport_width)} |"
    )
    typer.echo(border)
    for item in items:
        typer.echo(
            f"| {str(item.get('agent_id') or '').ljust(id_width)} | "
            f"{str(item.get('name') or '').ljust(name_width)} | "
            f"{str(item.get('transport') or '').ljust(transport_width)} |"
        )
    typer.echo(border)


def _render_agent_detail(item: dict[str, object]) -> None:
    typer.echo(f"Agent ID: {item.get('agent_id', '')}")
    typer.echo(f"Name: {item.get('name', '')}")
    typer.echo(f"Description: {item.get('description', '')}")
    transport = item.get("transport")
    if isinstance(transport, dict):
        typer.echo(f"Transport: {transport.get('transport', '')}")
        typer.echo(json.dumps(transport, ensure_ascii=False, indent=2))
        return
    typer.echo(f"Transport: {transport}")


def _render_test_result(agent_id: str, item: dict[str, object]) -> None:
    typer.echo(f"Agent: {agent_id}")
    typer.echo(f"OK: {item.get('ok', False)}")
    message = str(item.get("message") or "").strip()
    if message:
        typer.echo(f"Message: {message}")
    if item.get("agent_name"):
        typer.echo(f"Agent Name: {item.get('agent_name')}")
    if item.get("agent_version"):
        typer.echo(f"Agent Version: {item.get('agent_version')}")
    if item.get("protocol_version") is not None:
        typer.echo(f"Protocol Version: {item.get('protocol_version')}")
