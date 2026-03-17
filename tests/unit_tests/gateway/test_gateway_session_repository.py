# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_teams.gateway.gateway_models import (
    GatewayChannelType,
    GatewayMcpServerSpec,
    GatewaySessionRecord,
)
from agent_teams.gateway.gateway_session_repository import GatewaySessionRepository


def test_gateway_session_repository_persists_mcp_state(tmp_path: Path) -> None:
    repository = GatewaySessionRepository(tmp_path / "gateway.db")
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    created = repository.create(
        GatewaySessionRecord(
            gateway_session_id="gws_123",
            channel_type=GatewayChannelType.ACP_STDIO,
            external_session_id="ext_123",
            internal_session_id="session_123",
            cwd=str(tmp_path),
            capabilities={"permissions": {"filesystem": True}},
            session_mcp_servers=(
                GatewayMcpServerSpec(
                    server_id="filesystem",
                    name="filesystem",
                    transport="acp",
                    config={"name": "filesystem", "transport": "acp"},
                ),
            ),
            created_at=now,
            updated_at=now,
        )
    )

    loaded = repository.get(created.gateway_session_id)

    assert loaded.gateway_session_id == "gws_123"
    assert loaded.channel_type == GatewayChannelType.ACP_STDIO
    assert loaded.cwd == str(tmp_path)
    assert loaded.capabilities == {"permissions": {"filesystem": True}}
    assert loaded.session_mcp_servers == (
        GatewayMcpServerSpec(
            server_id="filesystem",
            name="filesystem",
            transport="acp",
            config={"name": "filesystem", "transport": "acp"},
        ),
    )


def test_gateway_session_repository_updates_active_run(tmp_path: Path) -> None:
    repository = GatewaySessionRepository(tmp_path / "gateway.db")
    created = repository.create(
        GatewaySessionRecord(
            gateway_session_id="gws_456",
            channel_type=GatewayChannelType.ACP_STDIO,
            external_session_id="ext_456",
            internal_session_id="session_456",
        )
    )

    updated = created.model_copy(
        update={
            "active_run_id": "run_456",
            "updated_at": datetime(2025, 1, 2, tzinfo=timezone.utc),
        }
    )
    repository.update(updated)

    loaded = repository.get("gws_456")
    assert loaded.active_run_id == "run_456"
