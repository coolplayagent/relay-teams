# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from relay_teams.gateway.acp_stdio import AcpGatewayServer, AcpStdioRuntime
    from relay_teams.gateway.gateway_models import (
        GatewayChannelType,
        GatewayMcpConnectionRecord,
        GatewayMcpConnectionStatus,
        GatewayMcpServerSpec,
        GatewaySessionRecord,
    )
    from relay_teams.gateway.gateway_session_repository import GatewaySessionRepository
    from relay_teams.gateway.gateway_session_service import GatewaySessionService
    from relay_teams.gateway.session_ingress_service import (
        GatewaySessionBusyError,
        GatewaySessionIngressBusyPolicy,
        GatewaySessionIngressRequest,
        GatewaySessionIngressResult,
        GatewaySessionIngressService,
        GatewaySessionIngressStatus,
    )

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
    "GatewaySessionBusyError",
    "GatewaySessionIngressBusyPolicy",
    "GatewaySessionIngressRequest",
    "GatewaySessionIngressResult",
    "GatewaySessionIngressService",
    "GatewaySessionIngressStatus",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "AcpGatewayServer": ("relay_teams.gateway.acp_stdio", "AcpGatewayServer"),
    "AcpStdioRuntime": ("relay_teams.gateway.acp_stdio", "AcpStdioRuntime"),
    "GatewayChannelType": (
        "relay_teams.gateway.gateway_models",
        "GatewayChannelType",
    ),
    "GatewayMcpConnectionRecord": (
        "relay_teams.gateway.gateway_models",
        "GatewayMcpConnectionRecord",
    ),
    "GatewayMcpConnectionStatus": (
        "relay_teams.gateway.gateway_models",
        "GatewayMcpConnectionStatus",
    ),
    "GatewayMcpServerSpec": (
        "relay_teams.gateway.gateway_models",
        "GatewayMcpServerSpec",
    ),
    "GatewaySessionRecord": (
        "relay_teams.gateway.gateway_models",
        "GatewaySessionRecord",
    ),
    "GatewaySessionRepository": (
        "relay_teams.gateway.gateway_session_repository",
        "GatewaySessionRepository",
    ),
    "GatewaySessionService": (
        "relay_teams.gateway.gateway_session_service",
        "GatewaySessionService",
    ),
    "GatewaySessionBusyError": (
        "relay_teams.gateway.session_ingress_service",
        "GatewaySessionBusyError",
    ),
    "GatewaySessionIngressBusyPolicy": (
        "relay_teams.gateway.session_ingress_service",
        "GatewaySessionIngressBusyPolicy",
    ),
    "GatewaySessionIngressRequest": (
        "relay_teams.gateway.session_ingress_service",
        "GatewaySessionIngressRequest",
    ),
    "GatewaySessionIngressResult": (
        "relay_teams.gateway.session_ingress_service",
        "GatewaySessionIngressResult",
    ),
    "GatewaySessionIngressService": (
        "relay_teams.gateway.session_ingress_service",
        "GatewaySessionIngressService",
    ),
    "GatewaySessionIngressStatus": (
        "relay_teams.gateway.session_ingress_service",
        "GatewaySessionIngressStatus",
    ),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
