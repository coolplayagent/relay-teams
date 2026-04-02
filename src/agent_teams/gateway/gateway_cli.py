# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import sys

from pydantic import JsonValue
import typer

from agent_teams.builtin import ensure_app_config_bootstrap
from agent_teams.env.runtime_env import sync_app_env_to_process_env
from agent_teams.gateway.acp_mcp_relay import AcpMcpRelay, GatewayAwareMcpRegistry
from agent_teams.gateway.acp_stdio import AcpGatewayServer, AcpStdioRuntime
from agent_teams.gateway.gateway_models import GatewaySessionRecord
from agent_teams.gateway.gateway_session_repository import GatewaySessionRepository
from agent_teams.gateway.gateway_session_model_profile_store import (
    GatewaySessionModelProfileStore,
)
from agent_teams.gateway.gateway_session_service import GatewaySessionService
from agent_teams.interfaces.server.container import ServerContainer
from agent_teams.logger import configure_logging
from agent_teams.paths import get_app_config_dir

type RequestJsonCallable = Callable[
    [str, str, str, dict[str, object] | None],
    dict[str, object] | list[object],
]
type AutoStartCallable = Callable[[str, bool], None]


def build_gateway_app(
    *,
    request_json: RequestJsonCallable | None = None,
    auto_start_if_needed: AutoStartCallable | None = None,
    default_base_url: str = "http://127.0.0.1:8000",
) -> typer.Typer:
    gateway_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
    acp_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
    feishu_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
    wechat_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

    @acp_app.command("stdio")
    def gateway_acp_stdio(
        role: str | None = typer.Option(
            None,
            "--role",
            help=(
                "Select the normal mode root role for ACP-created sessions. "
                "If omitted, the default MainAgent is used."
            ),
        ),
    ) -> None:
        runtime = _build_acp_stdio_runtime(role_id=role)
        asyncio.run(runtime.serve_forever())

    if request_json is not None and auto_start_if_needed is not None:

        @feishu_app.command("list")
        def feishu_list(
            base_url: str = typer.Option(default_base_url, "--base-url"),
            autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        ) -> None:
            auto_start_if_needed(base_url, autostart)
            result = request_json(base_url, "GET", "/api/gateway/feishu/accounts", None)
            typer.echo(json.dumps(result, ensure_ascii=False))

        @feishu_app.command("create")
        def feishu_create(
            payload_json: str = typer.Option(..., "--payload-json"),
            base_url: str = typer.Option(default_base_url, "--base-url"),
            autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        ) -> None:
            payload = _parse_json_object_option(
                payload_json,
                option_name="--payload-json",
            )
            auto_start_if_needed(base_url, autostart)
            result = request_json(
                base_url,
                "POST",
                "/api/gateway/feishu/accounts",
                payload,
            )
            typer.echo(json.dumps(result, ensure_ascii=False))

        @feishu_app.command("update")
        def feishu_update(
            account_id: str = typer.Option(..., "--account-id"),
            payload_json: str = typer.Option(..., "--payload-json"),
            base_url: str = typer.Option(default_base_url, "--base-url"),
            autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        ) -> None:
            payload = _parse_json_object_option(
                payload_json,
                option_name="--payload-json",
            )
            auto_start_if_needed(base_url, autostart)
            result = request_json(
                base_url,
                "PATCH",
                f"/api/gateway/feishu/accounts/{account_id}",
                payload,
            )
            typer.echo(json.dumps(result, ensure_ascii=False))

        @feishu_app.command("enable")
        def feishu_enable(
            account_id: str = typer.Option(..., "--account-id"),
            base_url: str = typer.Option(default_base_url, "--base-url"),
            autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        ) -> None:
            auto_start_if_needed(base_url, autostart)
            result = request_json(
                base_url,
                "POST",
                f"/api/gateway/feishu/accounts/{account_id}:enable",
                {},
            )
            typer.echo(json.dumps(result, ensure_ascii=False))

        @feishu_app.command("disable")
        def feishu_disable(
            account_id: str = typer.Option(..., "--account-id"),
            base_url: str = typer.Option(default_base_url, "--base-url"),
            autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        ) -> None:
            auto_start_if_needed(base_url, autostart)
            result = request_json(
                base_url,
                "POST",
                f"/api/gateway/feishu/accounts/{account_id}:disable",
                {},
            )
            typer.echo(json.dumps(result, ensure_ascii=False))

        @feishu_app.command("delete")
        def feishu_delete(
            account_id: str = typer.Option(..., "--account-id"),
            base_url: str = typer.Option(default_base_url, "--base-url"),
            autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        ) -> None:
            auto_start_if_needed(base_url, autostart)
            result = request_json(
                base_url,
                "DELETE",
                f"/api/gateway/feishu/accounts/{account_id}",
                None,
            )
            typer.echo(json.dumps(result, ensure_ascii=False))

        @feishu_app.command("reload")
        def feishu_reload(
            base_url: str = typer.Option(default_base_url, "--base-url"),
            autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        ) -> None:
            auto_start_if_needed(base_url, autostart)
            result = request_json(
                base_url,
                "POST",
                "/api/gateway/feishu/reload",
                {},
            )
            typer.echo(json.dumps(result, ensure_ascii=False))

        @wechat_app.command("list")
        def wechat_list(
            base_url: str = typer.Option(default_base_url, "--base-url"),
            autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        ) -> None:
            auto_start_if_needed(base_url, autostart)
            result = request_json(base_url, "GET", "/api/gateway/wechat/accounts", None)
            typer.echo(json.dumps(result, ensure_ascii=False))

        @wechat_app.command("connect")
        def wechat_connect(
            base_url_override: str | None = typer.Option(None, "--wechat-base-url"),
            route_tag: str | None = typer.Option(None, "--route-tag"),
            bot_type: str = typer.Option("3", "--bot-type"),
            base_url: str = typer.Option(default_base_url, "--base-url"),
            autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        ) -> None:
            payload: dict[str, object] = {"bot_type": bot_type}
            if base_url_override is not None:
                payload["base_url"] = base_url_override
            if route_tag is not None:
                payload["route_tag"] = route_tag
            auto_start_if_needed(base_url, autostart)
            result = request_json(
                base_url,
                "POST",
                "/api/gateway/wechat/login/start",
                payload,
            )
            typer.echo(json.dumps(result, ensure_ascii=False))

        @wechat_app.command("wait")
        def wechat_wait(
            session_key: str = typer.Option(..., "--session-key"),
            timeout_ms: int = typer.Option(480000, "--timeout-ms"),
            base_url: str = typer.Option(default_base_url, "--base-url"),
            autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        ) -> None:
            auto_start_if_needed(base_url, autostart)
            result = request_json(
                base_url,
                "POST",
                "/api/gateway/wechat/login/wait",
                {"session_key": session_key, "timeout_ms": timeout_ms},
            )
            typer.echo(json.dumps(result, ensure_ascii=False))

        @wechat_app.command("update")
        def wechat_update(
            account_id: str = typer.Option(..., "--account-id"),
            payload_json: str = typer.Option(..., "--payload-json"),
            base_url: str = typer.Option(default_base_url, "--base-url"),
            autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        ) -> None:
            payload = _parse_json_object_option(
                payload_json,
                option_name="--payload-json",
            )
            auto_start_if_needed(base_url, autostart)
            result = request_json(
                base_url,
                "PATCH",
                f"/api/gateway/wechat/accounts/{account_id}",
                payload,
            )
            typer.echo(json.dumps(result, ensure_ascii=False))

        @wechat_app.command("enable")
        def wechat_enable(
            account_id: str = typer.Option(..., "--account-id"),
            base_url: str = typer.Option(default_base_url, "--base-url"),
            autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        ) -> None:
            auto_start_if_needed(base_url, autostart)
            result = request_json(
                base_url,
                "POST",
                f"/api/gateway/wechat/accounts/{account_id}:enable",
                {},
            )
            typer.echo(json.dumps(result, ensure_ascii=False))

        @wechat_app.command("disable")
        def wechat_disable(
            account_id: str = typer.Option(..., "--account-id"),
            base_url: str = typer.Option(default_base_url, "--base-url"),
            autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        ) -> None:
            auto_start_if_needed(base_url, autostart)
            result = request_json(
                base_url,
                "POST",
                f"/api/gateway/wechat/accounts/{account_id}:disable",
                {},
            )
            typer.echo(json.dumps(result, ensure_ascii=False))

        @wechat_app.command("delete")
        def wechat_delete(
            account_id: str = typer.Option(..., "--account-id"),
            base_url: str = typer.Option(default_base_url, "--base-url"),
            autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        ) -> None:
            auto_start_if_needed(base_url, autostart)
            result = request_json(
                base_url,
                "DELETE",
                f"/api/gateway/wechat/accounts/{account_id}",
                None,
            )
            typer.echo(json.dumps(result, ensure_ascii=False))

        @wechat_app.command("reload")
        def wechat_reload(
            base_url: str = typer.Option(default_base_url, "--base-url"),
            autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        ) -> None:
            auto_start_if_needed(base_url, autostart)
            result = request_json(
                base_url,
                "POST",
                "/api/gateway/wechat/reload",
                {},
            )
            typer.echo(json.dumps(result, ensure_ascii=False))

    gateway_app.add_typer(acp_app, name="acp")
    gateway_app.add_typer(feishu_app, name="feishu")
    gateway_app.add_typer(wechat_app, name="wechat")
    return gateway_app


def _build_acp_stdio_runtime(*, role_id: str | None = None) -> AcpStdioRuntime:
    config_dir = get_app_config_dir()
    ensure_app_config_bootstrap(config_dir)
    sync_app_env_to_process_env(config_dir / ".env")
    configure_logging(config_dir=config_dir, console_enabled_override=False)
    session_model_profile_store = GatewaySessionModelProfileStore()
    container = ServerContainer(
        config_dir=config_dir,
        session_model_profile_lookup=session_model_profile_store.get,
    )
    default_normal_root_role_id = _resolve_acp_stdio_role_id(
        container=container,
        role_id=role_id,
    )
    metric_recorder = getattr(container, "metric_recorder", None)
    gateway_session_repository = GatewaySessionRepository(
        container.runtime.paths.db_path
    )
    gateway_session_service = GatewaySessionService(
        repository=gateway_session_repository,
        session_service=container.session_service,
        workspace_service=container.workspace_service,
        session_model_profile_store=session_model_profile_store,
        default_normal_root_role_id=default_normal_root_role_id,
    )

    def lookup_gateway_session(
        gateway_session_id: str,
    ) -> GatewaySessionRecord | None:
        try:
            return gateway_session_service.get_session(gateway_session_id)
        except KeyError:
            return None

    mcp_relay = AcpMcpRelay(
        metric_recorder=metric_recorder,
        gateway_session_lookup=lookup_gateway_session,
    )
    gateway_mcp_registry = GatewayAwareMcpRegistry(
        base_registry=container.mcp_registry,
        relay=mcp_relay,
    )
    container.mcp_registry = gateway_mcp_registry
    container.mcp_service.replace_registry(gateway_mcp_registry)
    container._refresh_coordinator_runtime()
    server = AcpGatewayServer(
        gateway_session_service=gateway_session_service,
        session_service=container.session_service,
        run_service=container.run_service,
        media_asset_service=container.media_asset_service,
        notify=_noop_notify,
        mcp_relay=mcp_relay,
        session_ingress_service=container.session_ingress_service,
        metric_recorder=metric_recorder,
    )
    runtime = AcpStdioRuntime(
        server=server,
        input_stream=sys.stdin.buffer,
        output_stream=sys.stdout.buffer,
    )
    server.set_notify(runtime.send_message)
    return runtime


def _resolve_acp_stdio_role_id(
    *,
    container: ServerContainer,
    role_id: str | None,
) -> str | None:
    normalized_role_id = str(role_id or "").strip() or None
    if normalized_role_id is None:
        return None
    try:
        return container.role_registry.resolve_normal_mode_role_id(normalized_role_id)
    except ValueError as exc:
        available_role_ids = ", ".join(
            role.role_id for role in container.role_registry.list_normal_mode_roles()
        )
        raise typer.BadParameter(
            f"Invalid --role '{normalized_role_id}'. "
            f"Available normal mode roles: {available_role_ids}.",
        ) from exc


async def _noop_notify(_message: dict[str, JsonValue]) -> None:
    return None


def _parse_json_object_option(
    payload_json: str,
    *,
    option_name: str,
) -> dict[str, object]:
    try:
        parsed = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{option_name} must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter(f"{option_name} must be a JSON object")
    return {str(key): value for key, value in parsed.items()}


gateway_app = build_gateway_app()
