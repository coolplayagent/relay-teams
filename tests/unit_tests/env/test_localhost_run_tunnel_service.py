# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.env.localhost_run_tunnel_service import (
    LocalhostRunTunnelService,
    LocalhostRunTunnelStartRequest,
    parse_localhost_run_event_line,
)


def test_parse_localhost_run_json_event_extracts_public_url() -> None:
    event = parse_localhost_run_event_line(
        '{"connection_id":"abc","event":"tcpip-forward","message":"demo-tunnel.lhr.life tunneled with tls termination, https://demo-tunnel.lhr.life","address":"demo-tunnel.lhr.life","status":"success"}'
    )

    assert event is not None
    assert event.connection_id == "abc"
    assert event.event == "tcpip-forward"
    assert event.address == "demo-tunnel.lhr.life"
    assert event.public_url == "https://demo-tunnel.lhr.life"


def test_parse_localhost_run_plain_text_extracts_public_url() -> None:
    event = parse_localhost_run_event_line(
        "demo-tunnel.lhr.life tunneled with tls termination, https://demo-tunnel.lhr.life"
    )

    assert event is not None
    assert event.address == "demo-tunnel.lhr.life"
    assert event.public_url == "https://demo-tunnel.lhr.life"


def test_parse_localhost_run_banner_doc_link_does_not_become_public_url() -> None:
    event = parse_localhost_run_event_line("https://localhost.run/docs/")

    assert event is not None
    assert event.public_url is None
    assert event.address is None


def test_localhost_run_tunnel_service_requires_ssh_binary() -> None:
    service = LocalhostRunTunnelService(ssh_path_lookup=lambda _name: None)

    with pytest.raises(RuntimeError, match="ssh is not installed"):
        service.start(LocalhostRunTunnelStartRequest())
