# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import sys

from pydantic import JsonValue
import typer

from agent_teams.builtin import ensure_app_config_bootstrap
from agent_teams.env.runtime_env import sync_app_env_to_process_env
from agent_teams.gateway.acp_stdio import AcpGatewayServer, AcpStdioRuntime
from agent_teams.gateway.gateway_session_repository import GatewaySessionRepository
from agent_teams.gateway.gateway_session_service import GatewaySessionService
from agent_teams.interfaces.server.container import ServerContainer
from agent_teams.paths import get_app_config_dir


def build_gateway_app() -> typer.Typer:
    gateway_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
    acp_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

    @acp_app.command("stdio")
    def gateway_acp_stdio() -> None:
        runtime = _build_acp_stdio_runtime()
        asyncio.run(runtime.serve_forever())

    gateway_app.add_typer(acp_app, name="acp")
    return gateway_app


def _build_acp_stdio_runtime() -> AcpStdioRuntime:
    config_dir = get_app_config_dir()
    ensure_app_config_bootstrap(config_dir)
    sync_app_env_to_process_env(config_dir / ".env")
    container = ServerContainer(config_dir=config_dir)
    gateway_session_repository = GatewaySessionRepository(
        container.runtime.paths.db_path
    )
    gateway_session_service = GatewaySessionService(
        repository=gateway_session_repository,
        session_service=container.session_service,
    )
    server = AcpGatewayServer(
        gateway_session_service=gateway_session_service,
        session_service=container.session_service,
        run_service=container.run_service,
        notify=_noop_notify,
    )
    runtime = AcpStdioRuntime(
        server=server,
        input_stream=sys.stdin.buffer,
        output_stream=sys.stdout.buffer,
    )
    server.set_notify(runtime.send_message)
    return runtime


async def _noop_notify(_message: dict[str, JsonValue]) -> None:
    return None


gateway_app = build_gateway_app()
