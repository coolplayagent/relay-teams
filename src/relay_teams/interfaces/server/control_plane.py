# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import threading
import time
from typing import ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.interfaces.server.runtime_identity import SERVER_VERSION

CONTROL_PLANE_HOST_ENV = "RELAY_TEAMS_CONTROL_PLANE_HOST"
CONTROL_PLANE_PORT_ENV = "RELAY_TEAMS_CONTROL_PLANE_PORT"
CONTROL_PLANE_URL_ENV = "RELAY_TEAMS_CONTROL_PLANE_URL"
CONTROL_PLANE_MAIN_URL_ENV = "RELAY_TEAMS_CONTROL_PLANE_MAIN_URL"
CONTROL_PLANE_STARTED_AT_ENV = "RELAY_TEAMS_CONTROL_PLANE_STARTED_AT"


class ControlPlaneLivePayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    status: str = "alive"
    version: str = SERVER_VERSION
    pid: int
    uptime_seconds: float
    main_base_url: str


class ControlPlaneDiscoveryPayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    enabled: bool
    live_url: str | None = None
    host: str | None = None
    port: int | None = None
    main_base_url: str | None = None


class ControlPlaneServerConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    host: str
    port: int = Field(ge=1, le=65535)
    main_base_url: str
    started_at: float

    @property
    def live_url(self) -> str:
        return f"{_format_base_url(self.host, self.port)}/live"


class ControlPlaneServerHandle:
    def __init__(
        self,
        *,
        server: ThreadingHTTPServer,
        thread: threading.Thread,
        config: ControlPlaneServerConfig,
    ) -> None:
        self._server = server
        self._thread = thread
        self.config = config

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


class _ControlPlaneHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _ControlPlaneHttpServerV6(_ControlPlaneHttpServer):
    address_family = socket.AF_INET6


def start_control_plane_server(
    config: ControlPlaneServerConfig,
) -> ControlPlaneServerHandle:
    handler = _build_handler(config)
    server_type = (
        _ControlPlaneHttpServerV6
        if _is_ipv6_host(config.host)
        else _ControlPlaneHttpServer
    )
    server = server_type((_bind_host(config.host), config.port), handler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="agent-teams-control-plane",
        daemon=True,
    )
    thread.start()
    return ControlPlaneServerHandle(server=server, thread=thread, config=config)


def allocate_control_plane_config(
    *,
    host: str,
    port: int,
    main_base_url: str,
) -> ControlPlaneServerConfig:
    control_host = _control_host_for_bind(host)
    configured_port = _env_int(os.environ, CONTROL_PLANE_PORT_ENV)
    control_port = (
        configured_port
        if configured_port is not None
        else _find_available_port(control_host, preferred_port=port + 1)
    )
    return ControlPlaneServerConfig(
        host=control_host,
        port=control_port,
        main_base_url=main_base_url.rstrip("/"),
        started_at=time.time(),
    )


def publish_control_plane_env(config: ControlPlaneServerConfig) -> None:
    os.environ[CONTROL_PLANE_HOST_ENV] = config.host
    os.environ[CONTROL_PLANE_PORT_ENV] = str(config.port)
    os.environ[CONTROL_PLANE_URL_ENV] = config.live_url
    os.environ[CONTROL_PLANE_MAIN_URL_ENV] = config.main_base_url
    os.environ[CONTROL_PLANE_STARTED_AT_ENV] = str(config.started_at)


def clear_control_plane_env() -> None:
    for key in (
        CONTROL_PLANE_HOST_ENV,
        CONTROL_PLANE_PORT_ENV,
        CONTROL_PLANE_URL_ENV,
        CONTROL_PLANE_MAIN_URL_ENV,
        CONTROL_PLANE_STARTED_AT_ENV,
    ):
        os.environ.pop(key, None)


def control_plane_discovery_from_env() -> ControlPlaneDiscoveryPayload:
    live_url = os.environ.get(CONTROL_PLANE_URL_ENV, "").strip()
    host = os.environ.get(CONTROL_PLANE_HOST_ENV, "").strip()
    port = _env_int(os.environ, CONTROL_PLANE_PORT_ENV)
    main_base_url = os.environ.get(CONTROL_PLANE_MAIN_URL_ENV, "").strip()
    enabled = bool(live_url and host and port is not None)
    return ControlPlaneDiscoveryPayload(
        enabled=enabled,
        live_url=live_url if live_url else None,
        host=host if host else None,
        port=port,
        main_base_url=main_base_url if main_base_url else None,
    )


def build_local_live_payload() -> ControlPlaneLivePayload:
    started_at = _env_float(os.environ, CONTROL_PLANE_STARTED_AT_ENV) or time.time()
    main_base_url = os.environ.get(CONTROL_PLANE_MAIN_URL_ENV, "").strip()
    return ControlPlaneLivePayload(
        status="alive",
        version=SERVER_VERSION,
        pid=os.getpid(),
        uptime_seconds=max(0.0, time.time() - started_at),
        main_base_url=main_base_url,
    )


def _build_handler(
    config: ControlPlaneServerConfig,
) -> type[BaseHTTPRequestHandler]:
    class ControlPlaneRequestHandler(BaseHTTPRequestHandler):
        server_version = "AgentTeamsControlPlane/1.0"

        def do_OPTIONS(self) -> None:
            self._send_empty(204)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path not in {"/live", "/api/system/live"}:
                self._send_json({"detail": "Not found"}, status_code=404)
                return
            payload = ControlPlaneLivePayload(
                pid=os.getpid(),
                uptime_seconds=max(0.0, time.time() - config.started_at),
                main_base_url=config.main_base_url,
            )
            self._send_json(payload.model_dump(mode="json"))

        def log_message(self, format: str, *args: object) -> None:
            _ = (format, args)
            return

        def _send_empty(self, status_code: int) -> None:
            self.send_response(status_code)
            self._send_headers(content_length=0)
            self.end_headers()

        def _send_json(
            self,
            payload: Mapping[str, object],
            *,
            status_code: int = 200,
        ) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status_code)
            self._send_headers(content_length=len(body))
            self.end_headers()
            self.wfile.write(body)

        def _send_headers(self, *, content_length: int) -> None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(content_length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Accept, Content-Type")

    return ControlPlaneRequestHandler


def _find_available_port(host: str, *, preferred_port: int) -> int:
    start_port = max(1, min(65535, preferred_port))
    for port in range(start_port, min(65535, start_port + 50) + 1):
        if _can_bind(host, port):
            return port
    raise RuntimeError("No control-plane port is available near the API port")


def _can_bind(host: str, port: int) -> bool:
    bind_host = _bind_host(host)
    family = socket.AF_INET6 if _is_ipv6_host(bind_host) else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((bind_host, port))
    except OSError:
        return False
    return True


def _control_host_for_bind(host: str) -> str:
    return host


def _bind_host(host: str) -> str:
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def _is_ipv6_host(host: str) -> bool:
    return ":" in _bind_host(host)


def _format_base_url(host: str, port: int) -> str:
    url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{url_host}:{port}"


def _env_int(env: Mapping[str, str], key: str) -> int | None:
    raw_value = env.get(key)
    if raw_value is None:
        return None
    try:
        parsed = int(raw_value)
    except ValueError:
        return None
    if parsed < 1 or parsed > 65535:
        return None
    return parsed


def _env_float(env: Mapping[str, str], key: str) -> float | None:
    raw_value = env.get(key)
    if raw_value is None:
        return None
    try:
        return float(raw_value)
    except ValueError:
        return None
