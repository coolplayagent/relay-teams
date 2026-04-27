# -*- coding: utf-8 -*-
from __future__ import annotations

import socket
import time

import httpx
import pytest

from relay_teams.interfaces.server.control_plane import (
    CONTROL_PLANE_HOST_ENV,
    CONTROL_PLANE_MAIN_URL_ENV,
    CONTROL_PLANE_PORT_ENV,
    CONTROL_PLANE_STARTED_AT_ENV,
    CONTROL_PLANE_URL_ENV,
    ControlPlaneServerConfig,
    allocate_control_plane_config,
    clear_control_plane_env,
    control_plane_discovery_from_env,
    publish_control_plane_env,
    start_control_plane_server,
)


def test_control_plane_server_serves_lightweight_liveness() -> None:
    port = _free_port()
    config = ControlPlaneServerConfig(
        host="127.0.0.1",
        port=port,
        main_base_url="http://127.0.0.1:8000",
        started_at=time.time(),
    )
    handle = start_control_plane_server(config)
    try:
        response = httpx.get(
            config.live_url,
            headers={"Accept": "application/json"},
            trust_env=False,
        )
    finally:
        handle.stop()

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    payload = response.json()
    assert payload["status"] == "alive"
    assert payload["main_base_url"] == "http://127.0.0.1:8000"
    assert isinstance(payload["pid"], int)
    assert isinstance(payload["uptime_seconds"], float)


def test_control_plane_discovery_reads_published_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CONTROL_PLANE_HOST_ENV, raising=False)
    monkeypatch.delenv(CONTROL_PLANE_PORT_ENV, raising=False)
    monkeypatch.delenv(CONTROL_PLANE_URL_ENV, raising=False)
    monkeypatch.delenv(CONTROL_PLANE_MAIN_URL_ENV, raising=False)
    monkeypatch.delenv(CONTROL_PLANE_STARTED_AT_ENV, raising=False)
    config = ControlPlaneServerConfig(
        host="127.0.0.1",
        port=8011,
        main_base_url="http://127.0.0.1:8010",
        started_at=123.0,
    )

    publish_control_plane_env(config)
    payload = control_plane_discovery_from_env()
    clear_control_plane_env()

    assert payload.enabled is True
    assert payload.live_url == "http://127.0.0.1:8011/live"
    assert payload.host == "127.0.0.1"
    assert payload.port == 8011
    assert payload.main_base_url == "http://127.0.0.1:8010"
    assert control_plane_discovery_from_env().enabled is False


def test_allocate_control_plane_config_honors_explicit_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _free_port()
    monkeypatch.setenv(CONTROL_PLANE_PORT_ENV, str(port))

    config = allocate_control_plane_config(
        host="0.0.0.0",
        port=8000,
        main_base_url="http://0.0.0.0:8000",
    )

    assert config.host == "0.0.0.0"
    assert config.port == port
    assert config.live_url == f"http://0.0.0.0:{port}/live"
    assert config.main_base_url == "http://0.0.0.0:8000"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
