# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
import json

import typer

DEFAULT_ALLOWED_SOURCE_TYPES = {"schedule", "webhook", "im", "rss", "custom"}

type RequestJsonCallable = Callable[
    [str, str, str, dict[str, object] | None, dict[str, str] | None, float],
    dict[str, object] | list[object],
]
type AutoStartCallable = Callable[[str, bool], None]


def build_triggers_app(
    *,
    request_json: RequestJsonCallable,
    auto_start_if_needed: AutoStartCallable,
    default_base_url: str,
) -> typer.Typer:
    triggers_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

    @triggers_app.command("list")
    def triggers_list(
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        result = request_json(base_url, "GET", "/api/triggers", None, None, 30.0)
        typer.echo(json.dumps(result, ensure_ascii=False))

    @triggers_app.command("get")
    def triggers_get(
        trigger_id: str = typer.Option(..., "--trigger-id"),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        result = request_json(
            base_url, "GET", f"/api/triggers/{trigger_id}", None, None, 30.0
        )
        typer.echo(json.dumps(result, ensure_ascii=False))

    @triggers_app.command("create")
    def triggers_create(
        name: str = typer.Option(..., "--name"),
        source_type: str = typer.Option(..., "--source-type"),
        display_name: str | None = typer.Option(None, "--display-name"),
        source_config_json: str = typer.Option("{}", "--source-config-json"),
        auth_policies_json: str = typer.Option(
            '[{"mode":"none"}]', "--auth-policies-json"
        ),
        target_config_json: str | None = typer.Option(None, "--target-config-json"),
        secret_config_json: str | None = typer.Option(None, "--secret-config-json"),
        public_token: str | None = typer.Option(None, "--public-token"),
        enabled: bool = typer.Option(True, "--enabled/--disabled"),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        source_type_normalized = _validate_source_type(source_type)
        source_config = _parse_json_object_option(
            source_config_json, option_name="--source-config-json"
        )
        auth_policies = _parse_json_list_option(
            auth_policies_json, option_name="--auth-policies-json"
        )
        target_config = (
            _parse_json_object_option(
                target_config_json, option_name="--target-config-json"
            )
            if target_config_json is not None
            else None
        )
        secret_config = (
            _object_to_string_map(
                _parse_json_object_option(
                    secret_config_json, option_name="--secret-config-json"
                ),
                "--secret-config-json",
            )
            if secret_config_json is not None
            else None
        )

        payload: dict[str, object] = {
            "name": name,
            "source_type": source_type_normalized,
            "source_config": source_config,
            "auth_policies": auth_policies,
            "enabled": enabled,
        }
        if display_name is not None:
            payload["display_name"] = display_name
        if target_config is not None:
            payload["target_config"] = target_config
        if secret_config is not None:
            payload["secret_config"] = secret_config
        if public_token is not None:
            payload["public_token"] = public_token

        auto_start_if_needed(base_url, autostart)
        result = request_json(base_url, "POST", "/api/triggers", payload, None, 30.0)
        typer.echo(json.dumps(result, ensure_ascii=False))

    @triggers_app.command("update")
    def triggers_update(
        trigger_id: str = typer.Option(..., "--trigger-id"),
        name: str | None = typer.Option(None, "--name"),
        display_name: str | None = typer.Option(None, "--display-name"),
        source_config_json: str | None = typer.Option(None, "--source-config-json"),
        auth_policies_json: str | None = typer.Option(None, "--auth-policies-json"),
        target_config_json: str | None = typer.Option(None, "--target-config-json"),
        secret_config_json: str | None = typer.Option(None, "--secret-config-json"),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        payload: dict[str, object] = {}
        if name is not None:
            payload["name"] = name
        if display_name is not None:
            payload["display_name"] = display_name
        if source_config_json is not None:
            payload["source_config"] = _parse_json_object_option(
                source_config_json, option_name="--source-config-json"
            )
        if auth_policies_json is not None:
            payload["auth_policies"] = _parse_json_list_option(
                auth_policies_json, option_name="--auth-policies-json"
            )
        if target_config_json is not None:
            payload["target_config"] = _parse_json_object_option(
                target_config_json, option_name="--target-config-json"
            )
        if secret_config_json is not None:
            payload["secret_config"] = _object_to_string_map(
                _parse_json_object_option(
                    secret_config_json, option_name="--secret-config-json"
                ),
                "--secret-config-json",
            )
        if not payload:
            raise typer.BadParameter(
                "At least one mutable field must be provided for update"
            )

        auto_start_if_needed(base_url, autostart)
        result = request_json(
            base_url, "PATCH", f"/api/triggers/{trigger_id}", payload, None, 30.0
        )
        typer.echo(json.dumps(result, ensure_ascii=False))

    @triggers_app.command("enable")
    def triggers_enable(
        trigger_id: str = typer.Option(..., "--trigger-id"),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        result = request_json(
            base_url, "POST", f"/api/triggers/{trigger_id}:enable", {}, None, 30.0
        )
        typer.echo(json.dumps(result, ensure_ascii=False))

    @triggers_app.command("disable")
    def triggers_disable(
        trigger_id: str = typer.Option(..., "--trigger-id"),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        result = request_json(
            base_url, "POST", f"/api/triggers/{trigger_id}:disable", {}, None, 30.0
        )
        typer.echo(json.dumps(result, ensure_ascii=False))

    @triggers_app.command("rotate-token")
    def triggers_rotate_token(
        trigger_id: str = typer.Option(..., "--trigger-id"),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        result = request_json(
            base_url,
            "POST",
            f"/api/triggers/{trigger_id}:rotate-token",
            {},
            None,
            30.0,
        )
        typer.echo(json.dumps(result, ensure_ascii=False))

    @triggers_app.command("events")
    def triggers_events(
        trigger_id: str = typer.Option(..., "--trigger-id"),
        limit: int = typer.Option(50, "--limit"),
        cursor_event_id: str | None = typer.Option(None, "--cursor-event-id"),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        path = f"/api/triggers/{trigger_id}/events?limit={limit}"
        if cursor_event_id is not None:
            path = f"{path}&cursor_event_id={cursor_event_id}"
        result = request_json(base_url, "GET", path, None, None, 30.0)
        typer.echo(json.dumps(result, ensure_ascii=False))

    @triggers_app.command("event")
    def triggers_event(
        event_id: str = typer.Option(..., "--event-id"),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        result = request_json(
            base_url, "GET", f"/api/triggers/events/{event_id}", None, None, 30.0
        )
        typer.echo(json.dumps(result, ensure_ascii=False))

    @triggers_app.command("ingest")
    def triggers_ingest(
        source_type: str = typer.Option(..., "--source-type"),
        payload_json: str = typer.Option(..., "--payload-json"),
        trigger_id: str | None = typer.Option(None, "--trigger-id"),
        trigger_name: str | None = typer.Option(None, "--trigger-name"),
        event_key: str | None = typer.Option(None, "--event-key"),
        occurred_at: str | None = typer.Option(None, "--occurred-at"),
        metadata_json: str = typer.Option("{}", "--metadata-json"),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        if trigger_id is None and trigger_name is None:
            raise typer.BadParameter(
                "Either --trigger-id or --trigger-name must be provided"
            )
        source_type_normalized = _validate_source_type(source_type)
        payload = _parse_json_object_option(payload_json, option_name="--payload-json")
        metadata = _parse_json_object_option(
            metadata_json, option_name="--metadata-json"
        )
        metadata_str = _object_to_string_map(metadata, "--metadata-json")

        request_payload: dict[str, object] = {
            "source_type": source_type_normalized,
            "payload": payload,
            "metadata": metadata_str,
        }
        if trigger_id is not None:
            request_payload["trigger_id"] = trigger_id
        if trigger_name is not None:
            request_payload["trigger_name"] = trigger_name
        if event_key is not None:
            request_payload["event_key"] = event_key
        if occurred_at is not None:
            request_payload["occurred_at"] = occurred_at

        auto_start_if_needed(base_url, autostart)
        result = request_json(
            base_url, "POST", "/api/triggers/ingest", request_payload, None, 30.0
        )
        typer.echo(json.dumps(result, ensure_ascii=False))

    @triggers_app.command("webhook")
    def triggers_webhook(
        public_token: str = typer.Option(..., "--public-token"),
        body_json: str = typer.Option(..., "--body-json"),
        header: list[str] = typer.Option(
            [],
            "--header",
            "-H",
            help="HTTP header in KEY:VALUE format. Repeat option for multiple headers.",
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        body = _parse_json_object_option(body_json, option_name="--body-json")
        extra_headers = _parse_header_options(header)

        auto_start_if_needed(base_url, autostart)
        result = request_json(
            base_url,
            "POST",
            f"/api/triggers/webhooks/{public_token}",
            body,
            extra_headers,
            30.0,
        )
        typer.echo(json.dumps(result, ensure_ascii=False))

    return triggers_app


def _validate_source_type(source_type: str) -> str:
    source_type_normalized = source_type.lower().strip()
    if source_type_normalized in DEFAULT_ALLOWED_SOURCE_TYPES:
        return source_type_normalized
    raise typer.BadParameter(
        "source-type must be one of: schedule, webhook, im, rss, custom"
    )


def _parse_json_object_option(option_value: str, option_name: str) -> dict[str, object]:
    try:
        parsed = json.loads(option_value)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{option_name} must be valid JSON") from exc
    if isinstance(parsed, dict):
        return {str(key): value for key, value in parsed.items()}
    raise typer.BadParameter(f"{option_name} must be a JSON object")


def _parse_json_list_option(option_value: str, option_name: str) -> list[object]:
    try:
        parsed = json.loads(option_value)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{option_name} must be valid JSON") from exc
    if isinstance(parsed, list):
        return [item for item in parsed]
    raise typer.BadParameter(f"{option_name} must be a JSON array")


def _object_to_string_map(
    payload: dict[str, object], option_name: str
) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            result[key] = value
            continue
        raise typer.BadParameter(f"{option_name} values must all be strings")
    return result


def _parse_header_options(header_items: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in header_items:
        if ":" not in item:
            raise typer.BadParameter("Each --header must follow KEY:VALUE format")
        key, value = item.split(":", 1)
        normalized_key = key.strip()
        normalized_value = value.strip()
        if not normalized_key:
            raise typer.BadParameter("Header key cannot be empty")
        headers[normalized_key] = normalized_value
    return headers
