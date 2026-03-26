# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.gateway.acp_stdio import AcpGatewayServer, AcpStdioRuntime
    from agent_teams.gateway.gateway_models import (
        GatewayChannelType,
        GatewayMcpConnectionRecord,
        GatewayMcpConnectionStatus,
        GatewayMcpServerSpec,
        GatewaySessionRecord,
    )
    from agent_teams.gateway.gateway_session_repository import GatewaySessionRepository
    from agent_teams.gateway.gateway_session_service import GatewaySessionService

__all__ = [
    "AcpGatewayServer",
    "AcpStdioRuntime",
    "GatewayChannelType",
    "GatewayMcpConnectionRecord",
    "GatewayMcpConnectionStatus",
    "GatewayMcpServerSpec",
    "GatewaySessionRecord",
    "GatewaySessionRepository",
    "GatewaySessionService",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "AcpGatewayServer": ("agent_teams.gateway.acp_stdio", "AcpGatewayServer"),
    "AcpStdioRuntime": ("agent_teams.gateway.acp_stdio", "AcpStdioRuntime"),
    "GatewayChannelType": (
        "agent_teams.gateway.gateway_models",
        "GatewayChannelType",
    ),
    "GatewayMcpConnectionRecord": (
        "agent_teams.gateway.gateway_models",
        "GatewayMcpConnectionRecord",
    ),
    "GatewayMcpConnectionStatus": (
        "agent_teams.gateway.gateway_models",
        "GatewayMcpConnectionStatus",
    ),
    "GatewayMcpServerSpec": (
        "agent_teams.gateway.gateway_models",
        "GatewayMcpServerSpec",
    ),
    "GatewaySessionRecord": (
        "agent_teams.gateway.gateway_models",
        "GatewaySessionRecord",
    ),
    "GatewaySessionRepository": (
        "agent_teams.gateway.gateway_session_repository",
        "GatewaySessionRepository",
    ),
    "GatewaySessionService": (
        "agent_teams.gateway.gateway_session_service",
        "GatewaySessionService",
    ),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
