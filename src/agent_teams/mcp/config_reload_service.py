# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable

from agent_teams.logger import get_logger
from agent_teams.mcp.mcp_config_manager import McpConfigManager
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.trace import trace_span

LOGGER = get_logger(__name__)


class McpConfigReloadService:
    def __init__(
        self,
        *,
        mcp_config_manager: McpConfigManager,
        role_registry: RoleRegistry,
        on_mcp_reloaded: Callable[[McpRegistry], None],
    ) -> None:
        self._mcp_config_manager: McpConfigManager = mcp_config_manager
        self._role_registry: RoleRegistry = role_registry
        self._on_mcp_reloaded: Callable[[McpRegistry], None] = on_mcp_reloaded

    def reload_mcp_config(self) -> None:
        with trace_span(
            LOGGER,
            component="mcp.config",
            operation="reload",
        ):
            mcp_registry = self._mcp_config_manager.load_registry()
            for role in self._role_registry.list_roles():
                mcp_registry.validate_known(role.mcp_servers)
            self._on_mcp_reloaded(mcp_registry)
