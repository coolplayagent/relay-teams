# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import JsonValue

from agent_teams.gateway.gateway_models import (
    GatewayChannelType,
    GatewayMcpConnectionRecord,
    GatewayMcpConnectionStatus,
    GatewayMcpServerSpec,
    GatewaySessionRecord,
)
from agent_teams.gateway.gateway_session_repository import GatewaySessionRepository
from agent_teams.sessions import SessionService


class GatewaySessionService:
    def __init__(
        self,
        *,
        repository: GatewaySessionRepository,
        session_service: SessionService,
    ) -> None:
        self._repository = repository
        self._session_service = session_service

    def create_session(
        self,
        *,
        channel_type: GatewayChannelType,
        cwd: str | None,
        capabilities: dict[str, JsonValue],
        session_mcp_servers: tuple[GatewayMcpServerSpec, ...] = (),
        external_session_id: str | None = None,
        peer_user_id: str | None = None,
        peer_chat_id: str | None = None,
    ) -> GatewaySessionRecord:
        now = self._utcnow()
        gateway_session_id = f"gws_{uuid4().hex[:12]}"
        resolved_external_session_id = external_session_id or gateway_session_id
        internal_session = self._session_service.create_session(workspace_id="default")
        record = GatewaySessionRecord(
            gateway_session_id=gateway_session_id,
            channel_type=channel_type,
            external_session_id=resolved_external_session_id,
            internal_session_id=internal_session.session_id,
            peer_user_id=peer_user_id,
            peer_chat_id=peer_chat_id,
            cwd=cwd,
            capabilities=capabilities,
            session_mcp_servers=session_mcp_servers,
            created_at=now,
            updated_at=now,
        )
        return self._repository.create(record)

    def get_session(self, gateway_session_id: str) -> GatewaySessionRecord:
        return self._repository.get(gateway_session_id)

    def bind_active_run(
        self,
        gateway_session_id: str,
        run_id: str | None,
    ) -> GatewaySessionRecord:
        existing = self._repository.get(gateway_session_id)
        updated = existing.model_copy(
            update={
                "active_run_id": run_id,
                "updated_at": self._utcnow(),
            }
        )
        return self._repository.update(updated)

    def set_session_mcp_servers(
        self,
        gateway_session_id: str,
        session_mcp_servers: tuple[GatewayMcpServerSpec, ...],
    ) -> GatewaySessionRecord:
        existing = self._repository.get(gateway_session_id)
        updated = existing.model_copy(
            update={
                "session_mcp_servers": session_mcp_servers,
                "updated_at": self._utcnow(),
            }
        )
        return self._repository.update(updated)

    def open_mcp_connection(
        self,
        *,
        gateway_session_id: str,
        server_id: str,
    ) -> GatewayMcpConnectionRecord:
        existing = self._repository.get(gateway_session_id)
        now = self._utcnow()
        connection = GatewayMcpConnectionRecord(
            connection_id=f"mcpconn_{uuid4().hex[:12]}",
            server_id=server_id,
            status=GatewayMcpConnectionStatus.OPEN,
            created_at=now,
            updated_at=now,
        )
        updated = existing.model_copy(
            update={
                "mcp_connections": existing.mcp_connections + (connection,),
                "updated_at": now,
            }
        )
        _ = self._repository.update(updated)
        return connection

    def close_mcp_connection(
        self,
        *,
        gateway_session_id: str,
        connection_id: str,
    ) -> GatewaySessionRecord:
        existing = self._repository.get(gateway_session_id)
        now = self._utcnow()
        next_connections: list[GatewayMcpConnectionRecord] = []
        found = False
        for entry in existing.mcp_connections:
            if entry.connection_id != connection_id:
                next_connections.append(entry)
                continue
            found = True
            next_connections.append(
                entry.model_copy(
                    update={
                        "status": GatewayMcpConnectionStatus.CLOSED,
                        "updated_at": now,
                    }
                )
            )
        if not found:
            raise KeyError(f"Unknown connection_id: {connection_id}")
        updated = existing.model_copy(
            update={
                "mcp_connections": tuple(next_connections),
                "updated_at": now,
            }
        )
        return self._repository.update(updated)

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(tz=timezone.utc)
