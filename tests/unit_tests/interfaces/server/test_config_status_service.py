# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import JsonValue

from relay_teams.gateway.acp_mcp_relay import AcpMcpRelay, GatewayAwareMcpRegistry
from relay_teams.gateway.gateway_models import GatewayMcpServerSpec
from relay_teams.interfaces.server.config_status_service import ConfigStatusService
from relay_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.sessions.runs.runtime_config import (
    ModelConfigStatus,
    RuntimeConfig,
    RuntimePaths,
)
from relay_teams.skills.skill_models import SkillScope, SkillSummaryEntry
from relay_teams.skills.skill_registry import SkillRegistry


class _FakeSkillRegistry:
    def list_skill_summaries(self) -> tuple[SkillSummaryEntry, ...]:
        return (
            SkillSummaryEntry(
                ref="builtin:diff",
                name="diff",
                description="Inspect changes between files.",
                scope=SkillScope.BUILTIN,
            ),
        )


def test_get_config_status_only_exposes_app_scoped_mcp_servers() -> None:
    relay = AcpMcpRelay()
    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="mcp-server-context7",
                name="mcp-server-context7",
                transport="stdio",
                config={
                    "command": "npx",
                    "args": ["-y", "@upstash/context7-mcp"],
                },
            ),
        ),
    )
    registry = GatewayAwareMcpRegistry(
        base_registry=McpRegistry(
            (
                McpServerSpec(
                    name="filesystem",
                    config={"mcpServers": {"filesystem": {"command": "npx"}}},
                    server_config={"command": "npx", "args": ["-y", "filesystem"]},
                    source=McpConfigScope.APP,
                ),
            )
        ),
        relay=relay,
    )
    service = ConfigStatusService(
        get_runtime=lambda: RuntimeConfig(
            paths=_build_runtime_paths(),
            llm_profiles={},
            model_status=ModelConfigStatus(
                loaded=True,
                profiles=("default",),
                error=None,
            ),
        ),
        get_mcp_registry=lambda: cast(McpRegistry, registry),
        get_skill_registry=lambda: cast(SkillRegistry, _FakeSkillRegistry()),
        get_proxy_status=lambda: {"enabled": False},
    )

    with relay.session_scope("gws_123"):
        status = service.get_config_status()

    assert status["mcp"] == {
        "loaded": True,
        "servers": ["filesystem"],
    }
    assert status["skills"] == {
        "loaded": True,
        "skills": [
            {
                "ref": "builtin:diff",
                "name": "diff",
                "description": "Inspect changes between files.",
                "scope": "builtin",
            }
        ],
    }
    assert status["proxy"] == {"enabled": False}


def test_get_config_status_keeps_empty_mcp_list_when_only_session_servers_exist() -> (
    None
):
    relay = AcpMcpRelay()
    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="mcp-server-context7",
                name="mcp-server-context7",
                transport="stdio",
                config={
                    "command": "npx",
                    "args": ["-y", "@upstash/context7-mcp"],
                },
            ),
        ),
    )
    service = ConfigStatusService(
        get_runtime=lambda: RuntimeConfig(
            paths=_build_runtime_paths(),
            llm_profiles={},
            model_status=ModelConfigStatus(
                loaded=False,
                profiles=(),
                error="missing",
            ),
        ),
        get_mcp_registry=lambda: cast(
            McpRegistry,
            GatewayAwareMcpRegistry(
                base_registry=McpRegistry(()),
                relay=relay,
            ),
        ),
        get_skill_registry=lambda: cast(SkillRegistry, _FakeSkillRegistry()),
        get_proxy_status=lambda: cast(dict[str, JsonValue], {}),
    )

    with relay.session_scope("gws_123"):
        status = service.get_config_status()

    assert status["mcp"] == {
        "loaded": True,
        "servers": [],
    }


def _build_runtime_paths() -> RuntimePaths:
    return RuntimePaths(
        config_dir=Path("/tmp/config"),
        env_file=Path("/tmp/config/.env"),
        db_path=Path("/tmp/config/relay_teams.db"),
        roles_dir=Path("/tmp/config/roles"),
    )
