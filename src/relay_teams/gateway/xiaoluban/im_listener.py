# -*- coding: utf-8 -*-
from __future__ import annotations

import ipaddress
import logging
import os
import socket
import threading
import time
from typing import Protocol
from urllib.parse import quote

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException

from relay_teams.gateway.xiaoluban.models import XiaolubanInboundMessage
from relay_teams.logger import get_logger, log_event

LOGGER = get_logger(__name__)

DEFAULT_XIAOLUBAN_IM_LISTENER_HOST = "0.0.0.0"
DEFAULT_XIAOLUBAN_IM_LISTENER_PORT = 9009
XIAOLUBAN_IM_LISTENER_HOST_ENV = "RELAY_TEAMS_XIAOLUBAN_IM_LISTENER_HOST"
XIAOLUBAN_IM_LISTENER_PORT_ENV = "RELAY_TEAMS_XIAOLUBAN_IM_LISTENER_PORT"
XIAOLUBAN_IM_PUBLIC_HOST_ENV = "RELAY_TEAMS_XIAOLUBAN_IM_PUBLIC_HOST"


class XiaolubanImInboundHandler(Protocol):
    def handle_im_inbound(
        self,
        *,
        account_id: str,
        message: XiaolubanInboundMessage,
    ) -> None:  # pragma: no cover
        pass

    def get_im_callback_auth_token(self, account_id: str) -> str:
        raise NotImplementedError  # pragma: no cover


class XiaolubanImListenerService:
    def __init__(
        self,
        *,
        service: XiaolubanImInboundHandler,
        host: str | None = None,
        port: int | None = None,
        public_host: str | None = None,
    ) -> None:
        self._service = service
        self._host = _normalize_host(
            host
            or os.environ.get(XIAOLUBAN_IM_LISTENER_HOST_ENV)
            or DEFAULT_XIAOLUBAN_IM_LISTENER_HOST
        )
        self._port = port or _listener_port_from_env()
        self._public_host = _normalize_optional_host(
            public_host or os.environ.get(XIAOLUBAN_IM_PUBLIC_HOST_ENV)
        )
        self._app = self._build_app()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def app(self) -> FastAPI:
        return self._app

    def start(self) -> None:
        with self._lock:
            if self.is_running():
                return
            if not _can_bind(self._host, self._port):
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="gateway.xiaoluban.im_listener.port_unavailable",
                    message="Xiaoluban IM listener port is unavailable",
                    payload={"host": self._host, "port": self._port},
                )
                return
            config = uvicorn.Config(
                self._app,
                host=self._host,
                port=self._port,
                log_level="warning",
                access_log=False,
            )
            self._server = uvicorn.Server(config)
            self._thread = threading.Thread(
                target=self._run_server,
                name="xiaoluban-im-listener",
                daemon=True,
            )
            self._thread.start()
        time.sleep(0.2)
        if self.is_running():
            log_event(
                LOGGER,
                logging.INFO,
                event="gateway.xiaoluban.im_listener.started",
                message="Xiaoluban IM listener started",
                payload={"port": self._port},
            )

    def stop(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            if server is None or thread is None:
                return
            server.should_exit = True
        thread.join(timeout=5.0)
        with self._lock:
            if not thread.is_alive():
                self._server = None
                self._thread = None

    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def callback_url(self, *, account_id: str) -> str:
        host = self._callback_host()
        if host is None:
            raise RuntimeError("xiaoluban_im_listener_host_unavailable")
        return (
            "http://"
            + _format_host_for_url(host)
            + ":"
            + str(self._port)
            + "/"
            + quote(account_id, safe="")
        )

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        @app.post("/{account_id}")
        async def inbound_account(
            account_id: str,
            req: XiaolubanInboundMessage,
            background_tasks: BackgroundTasks,
        ) -> dict[str, str]:
            # NOTE: Xiaoluban IM forwarding URLs have a platform-enforced length
            # limit so callback auth tokens cannot be embedded in the URL.
            # This handler intentionally accepts callbacks by account_id routing
            # only; inbound forgery risk is mitigated by the URL-based namespace.
            try:
                _ = self._service.get_im_callback_auth_token(account_id)
            except KeyError as exc:
                raise HTTPException(
                    status_code=404,
                    detail="xiaoluban_im_account_not_found",
                ) from exc
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=409,
                    detail="xiaoluban_im_callback_auth_unavailable",
                ) from exc
            self._enqueue_inbound(
                account_id=account_id,
                req=req,
                background_tasks=background_tasks,
            )
            return {"message": "Forwarding received"}

        return app

    def _enqueue_inbound(
        self,
        *,
        account_id: str,
        req: XiaolubanInboundMessage,
        background_tasks: BackgroundTasks,
    ) -> None:
        log_event(
            LOGGER,
            logging.INFO,
            event="gateway.xiaoluban.im_listener.inbound_received",
            message=(
                "Received Xiaoluban IM callback: "
                f"account_id={account_id} "
                f"sender={req.sender} "
                f"receiver={req.receiver} "
                f"session_id={req.session_id} "
                f"content={_preview_text(req.content)}"
            ),
            payload={
                "account_id": account_id,
                "sender": req.sender,
                "receiver": req.receiver,
                "session_id": req.session_id,
                "content_preview": _preview_text(req.content),
            },
        )
        background_tasks.add_task(
            self._service.handle_im_inbound,
            account_id=account_id,
            message=req,
        )

    def _run_server(self) -> None:
        server = self._server
        if server is None:
            return
        try:
            server.run()
        finally:
            log_event(
                LOGGER,
                logging.INFO,
                event="gateway.xiaoluban.im_listener.stopped",
                message="Xiaoluban IM listener stopped",
                payload={"port": self._port},
            )

    def _callback_host(self) -> str | None:
        if self._public_host is not None:
            return self._public_host
        if _is_unspecified_address(self._host):
            return resolve_xiaoluban_im_callback_host()
        return self._host


def resolve_xiaoluban_im_callback_host() -> str | None:
    candidate = _resolve_default_route_ipv4()
    if candidate is not None:
        return candidate
    candidate = _resolve_default_route_ipv6()
    if candidate is not None:
        return candidate
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            addresses = socket.getaddrinfo(socket.gethostname(), None, family)
        except OSError:
            continue
        for address in addresses:
            host = str(address[4][0]).strip()
            if host and not _is_local_or_unspecified_hostname(host):
                return host
    return None


def _listener_port_from_env() -> int:
    raw_port = os.environ.get(XIAOLUBAN_IM_LISTENER_PORT_ENV)
    if raw_port is None:
        return DEFAULT_XIAOLUBAN_IM_LISTENER_PORT
    try:
        port = int(raw_port)
    except ValueError:
        return DEFAULT_XIAOLUBAN_IM_LISTENER_PORT
    if port <= 0 or port > 65535:
        return DEFAULT_XIAOLUBAN_IM_LISTENER_PORT
    return port


def _resolve_default_route_ipv4() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            host = str(probe.getsockname()[0]).strip()
    except OSError:
        return None
    if not host or _is_local_or_unspecified_hostname(host):
        return None
    return host


def _resolve_default_route_ipv6() -> str | None:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as probe:
            probe.connect(("2001:4860:4860::8888", 1))
            host = str(probe.getsockname()[0]).strip()
    except OSError:
        return None
    if not host or _is_local_or_unspecified_hostname(host):
        return None
    return host


def _can_bind(host: str, port: int) -> bool:
    try:
        info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        family = info[0][0]
    except (OSError, IndexError):
        return False
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, port))
    except OSError:
        return False
    return True


def _is_local_or_unspecified_hostname(hostname: str) -> bool:
    normalized = hostname.strip().lower()
    if normalized in {"localhost", "testserver", "0.0.0.0", "::", ""}:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _normalize_host(host: str) -> str:
    normalized = host.strip()
    return normalized or DEFAULT_XIAOLUBAN_IM_LISTENER_HOST


def _normalize_optional_host(host: str | None) -> str | None:
    if host is None:
        return None
    normalized = host.strip()
    return normalized or None


def _format_host_for_url(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if address.version == 6:
        return f"[{host}]"
    return host


def _is_unspecified_address(hostname: str) -> bool:
    normalized = hostname.strip().lower()
    if normalized in {"0.0.0.0", "::", ""}:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return address.is_unspecified


def _preview_text(text: str, *, limit: int = 120) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."
