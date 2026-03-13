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


class PromptOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


class PromptSection(str, Enum):
    RUNTIME = "runtime"
    PROVIDER = "provider"
    USER = "user"
    TOOLS = "tools"
    SKILLS = "skills"
    ALL = "all"


def build_prompts_app(
    *,
    request_json: RequestJsonCallable,
    auto_start_if_needed: AutoStartCallable,
    default_base_url: str,
) -> typer.Typer:
    prompts_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

    @prompts_app.command("get")
    def prompts_get(
        role_id: str | None = typer.Option(None, "--role-id"),
        objective: str | None = typer.Option(None, "--objective"),
        shared_state_json: str = typer.Option("{}", "--shared-state-json"),
        tool: list[str] = typer.Option(
            [], "--tool", help="Override tools. Repeat option for multiple values."
        ),
        skill: list[str] = typer.Option(
            [], "--skill", help="Override skills. Repeat option for multiple values."
        ),
        section: PromptSection = typer.Option(
            PromptSection.PROVIDER,
            "--section",
            case_sensitive=False,
        ),
        output_format: PromptOutputFormat = typer.Option(
            PromptOutputFormat.TABLE,
            "--format",
            help="Output format: table or json.",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        if role_id is None or not role_id.strip():
            roles_payload = request_json(base_url, "GET", "/api/roles", None)
            role_ids = _extract_role_ids(roles_payload)
            _print_missing_role_hint(role_ids)
            raise typer.Exit(code=2)

        shared_state = _parse_shared_state_json(shared_state_json)
        payload: dict[str, object] = {
            "role_id": role_id.strip(),
            "shared_state": shared_state,
        }
        if objective is not None:
            payload["objective"] = objective
        if tool:
            payload["tools"] = [item for item in tool]
        if skill:
            payload["skills"] = [item for item in skill]

        result = request_json(base_url, "POST", "/api/prompts:preview", payload)
        response = _require_object_response(result, "/api/prompts:preview")
        selected = _select_prompt_sections(response, section)

        if output_format == PromptOutputFormat.JSON:
            typer.echo(json.dumps(selected, ensure_ascii=False))
            return
        _render_prompt_text(selected)

    return prompts_app


def _extract_role_ids(payload: dict[str, object] | list[object]) -> list[str]:
    entries = payload
    if isinstance(payload, dict):
        data = payload.get("data")
        entries = data if isinstance(data, list) else []
    if not isinstance(entries, list):
        return []
    role_ids: list[str] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        value = item.get("role_id")
        if isinstance(value, str):
            role_ids.append(value)
    role_ids.sort()
    return role_ids


def _print_missing_role_hint(role_ids: list[str]) -> None:
    typer.echo("Missing required option: --role-id")
    if role_ids:
        typer.echo("Available roles:")
        for role_id in role_ids:
            typer.echo(f"- {role_id}")
    typer.echo("Usage: agent-teams prompts get --role-id <role_id>")


def _parse_shared_state_json(raw: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter("--shared-state-json must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("--shared-state-json must be a JSON object")
    return {str(key): value for key, value in parsed.items()}


def _select_prompt_sections(
    payload: dict[str, object], section: PromptSection
) -> dict[str, object]:
    base: dict[str, object] = {}
    if section == PromptSection.ALL:
        return {
            "provider_system_prompt": payload.get("provider_system_prompt", ""),
            "user_prompt": payload.get("user_prompt", ""),
        }
    if section == PromptSection.RUNTIME:
        return {
            **base,
            "runtime_system_prompt": payload.get("runtime_system_prompt", ""),
        }
    if section == PromptSection.PROVIDER:
        return {
            **base,
            "provider_system_prompt": payload.get("provider_system_prompt", ""),
        }
    if section == PromptSection.USER:
        return {**base, "user_prompt": payload.get("user_prompt", "")}
    if section == PromptSection.TOOLS:
        return {
            **base,
            "tools": payload.get("tools", []),
            "tool_prompt": payload.get("tool_prompt", ""),
        }
    return {
        **base,
        "skills": payload.get("skills", []),
        "skill_prompt": payload.get("skill_prompt", ""),
    }


def _render_prompt_text(payload: dict[str, object]) -> None:
    if not payload:
        typer.echo("No prompt content returned.")
        return

    prompt_order = (
        "runtime_system_prompt",
        "provider_system_prompt",
        "user_prompt",
        "tool_prompt",
        "skill_prompt",
    )

    rendered_prompt = False
    for key in prompt_order:
        if key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, str):
            continue
        if rendered_prompt:
            typer.echo("")
        typer.echo(value)
        rendered_prompt = True


def _require_object_response(
    payload: dict[str, object] | list[object], path: str
) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    raise RuntimeError(f"Expected JSON object from {path}")
