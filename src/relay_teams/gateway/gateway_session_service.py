# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import JsonValue

from relay_teams.gateway.gateway_model_profile_override import (
    GatewayModelProfileOverride,
)
from relay_teams.gateway.gateway_models import (
    GatewayChannelType,
    GatewayMcpConnectionRecord,
    GatewayMcpConnectionStatus,
    GatewayMcpServerSpec,
    GatewaySessionRecord,
)
from relay_teams.gateway.gateway_session_model_profile_store import (
    GatewaySessionModelProfileStore,
)
from relay_teams.gateway.gateway_session_repository import GatewaySessionRepository
from relay_teams.sessions.session_service import SessionService
from relay_teams.sessions.session_models import SessionMode
from relay_teams.workspace import WorkspaceService


class GatewaySessionService:
    def __init__(
        self,
        *,
        repository: GatewaySessionRepository,
        session_service: SessionService,
        workspace_service: WorkspaceService | None = None,
        session_model_profile_store: GatewaySessionModelProfileStore | None = None,
        default_normal_root_role_id: str | None = None,
    ) -> None:
        self._repository = repository
        self._session_service = session_service
        self._workspace_service = workspace_service
        self._session_model_profile_store = (
            session_model_profile_store or GatewaySessionModelProfileStore()
        )
        self._default_normal_root_role_id = default_normal_root_role_id

    def create_session(
        self,
        *,
        channel_type: GatewayChannelType,
        cwd: str | None,
        capabilities: dict[str, JsonValue],
        session_mcp_servers: tuple[GatewayMcpServerSpec, ...] = (),
        model_profile_override: GatewayModelProfileOverride | None = None,
        external_session_id: str | None = None,
        peer_user_id: str | None = None,
        peer_chat_id: str | None = None,
    ) -> GatewaySessionRecord:
        now = self._utcnow()
        gateway_session_id = f"gws_{uuid4().hex[:12]}"
        resolved_external_session_id = external_session_id or gateway_session_id
        workspace_id, resolved_cwd = self._resolve_workspace_binding(
            cwd,
            fallback_workspace_id="default",
        )
        internal_session = self._session_service.create_session(
            workspace_id=workspace_id,
            normal_root_role_id=self._default_normal_root_role_id,
        )
        record = GatewaySessionRecord(
            gateway_session_id=gateway_session_id,
            channel_type=channel_type,
            external_session_id=resolved_external_session_id,
            internal_session_id=internal_session.session_id,
            peer_user_id=peer_user_id,
            peer_chat_id=peer_chat_id,
            cwd=resolved_cwd,
            capabilities=capabilities,
            channel_state=(
                {"acp_model_profile_override": model_profile_override.to_public_state()}
                if model_profile_override is not None
                else {}
            ),
            session_mcp_servers=session_mcp_servers,
            created_at=now,
            updated_at=now,
        )
        created = self._repository.create(record)
        if model_profile_override is not None:
            self._session_model_profile_store.set(
                created.internal_session_id,
                model_profile_override.to_model_endpoint_config(),
            )
        return created

    def resolve_or_create_session(
        self,
        *,
        channel_type: GatewayChannelType,
        external_session_id: str,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
        session_mode: SessionMode | None = None,
        normal_root_role_id: str | None = None,
        orchestration_preset_id: str | None = None,
        cwd: str | None = None,
        capabilities: dict[str, JsonValue] | None = None,
        channel_state: dict[str, JsonValue] | None = None,
        peer_user_id: str | None = None,
        peer_chat_id: str | None = None,
    ) -> GatewaySessionRecord:
        existing = self._repository.get_by_external(
            channel_type=channel_type,
            external_session_id=external_session_id,
        )
        now = self._utcnow()
        normalized_capabilities = capabilities or {}
        normalized_channel_state = channel_state or {}
        if existing is None:
            internal_session = self._session_service.create_session(
                workspace_id=workspace_id,
                metadata=metadata,
                session_mode=session_mode,
                normal_root_role_id=normal_root_role_id,
                orchestration_preset_id=orchestration_preset_id,
            )
            record = GatewaySessionRecord(
                gateway_session_id=f"gws_{uuid4().hex[:12]}",
                channel_type=channel_type,
                external_session_id=external_session_id,
                internal_session_id=internal_session.session_id,
                peer_user_id=peer_user_id,
                peer_chat_id=peer_chat_id,
                cwd=cwd,
                capabilities=normalized_capabilities,
                channel_state=normalized_channel_state,
                created_at=now,
                updated_at=now,
            )
            return self._repository.create(record)

        updated = existing.model_copy(
            update={
                "peer_user_id": peer_user_id or existing.peer_user_id,
                "peer_chat_id": peer_chat_id or existing.peer_chat_id,
                "cwd": cwd if cwd is not None else existing.cwd,
                "capabilities": normalized_capabilities or existing.capabilities,
                "channel_state": {
                    **existing.channel_state,
                    **normalized_channel_state,
                },
                "updated_at": now,
            }
        )
        return self._repository.update(updated)

    def rebind_session_cwd(
        self,
        gateway_session_id: str,
        *,
        cwd: str,
    ) -> GatewaySessionRecord:
        existing = self._repository.get(gateway_session_id)
        current_session = self._session_service.get_session(
            existing.internal_session_id
        )
        workspace_id, resolved_cwd = self._resolve_workspace_binding(
            cwd,
            fallback_workspace_id=current_session.workspace_id,
        )
        if workspace_id != current_session.workspace_id:
            _ = self._session_service.rebind_session_workspace(
                existing.internal_session_id,
                workspace_id=workspace_id,
            )
        updated = existing.model_copy(
            update={
                "cwd": resolved_cwd,
                "updated_at": self._utcnow(),
            }
        )
        return self._repository.update(updated)

    def get_session(self, gateway_session_id: str) -> GatewaySessionRecord:
        return self._repository.get(gateway_session_id)

    def get_by_internal_session_id(
        self,
        internal_session_id: str,
    ) -> GatewaySessionRecord | None:
        return self._repository.get_by_internal_session_id(internal_session_id)

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

    def set_session_model_profile_override(
        self,
        gateway_session_id: str,
        model_profile_override: GatewayModelProfileOverride | None,
    ) -> GatewaySessionRecord:
        existing = self._repository.get(gateway_session_id)
        channel_state = dict(existing.channel_state)
        if model_profile_override is None:
            channel_state.pop("acp_model_profile_override", None)
            self._session_model_profile_store.delete(existing.internal_session_id)
        else:
            channel_state["acp_model_profile_override"] = (
                model_profile_override.to_public_state()
            )
            self._session_model_profile_store.set(
                existing.internal_session_id,
                model_profile_override.to_model_endpoint_config(),
            )
        updated = existing.model_copy(
            update={
                "channel_state": channel_state,
                "updated_at": self._utcnow(),
            }
        )
        return self._repository.update(updated)

    def update_channel_state(
        self,
        gateway_session_id: str,
        *,
        channel_state: dict[str, JsonValue],
        peer_user_id: str | None = None,
        peer_chat_id: str | None = None,
    ) -> GatewaySessionRecord:
        existing = self._repository.get(gateway_session_id)
        updated = existing.model_copy(
            update={
                "channel_state": {**existing.channel_state, **channel_state},
                "peer_user_id": peer_user_id or existing.peer_user_id,
                "peer_chat_id": peer_chat_id or existing.peer_chat_id,
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

    def _resolve_workspace_binding(
        self,
        cwd: str | None,
        *,
        fallback_workspace_id: str,
    ) -> tuple[str, str | None]:
        if cwd is None:
            return fallback_workspace_id, None
        if self._workspace_service is None:
            return fallback_workspace_id, cwd
        workspace = self._workspace_service.create_workspace_for_root(
            root_path=Path(cwd).expanduser()
        )
        return workspace.workspace_id, str(workspace.root_path)

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(tz=timezone.utc)
