from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from relay_teams.gateway.gateway_models import GatewayChannelType, GatewaySessionRecord
from relay_teams.gateway.gateway_model_profile_override import (
    GatewayModelProfileOverride,
)
from relay_teams.gateway.gateway_session_model_profile_store import (
    GatewaySessionModelProfileStore,
)
from relay_teams.gateway.gateway_session_repository import GatewaySessionRepository
from relay_teams.gateway.gateway_session_service import GatewaySessionService
from relay_teams.sessions.session_models import SessionRecord
from relay_teams.sessions.session_service import SessionService


def test_resolve_or_create_session_rebinds_deleted_internal_session() -> None:
    repository = _FakeGatewaySessionRepository()
    session_service = _FakeSessionService()
    service = GatewaySessionService(
        repository=cast(GatewaySessionRepository, repository),
        session_service=cast(SessionService, session_service),
    )

    first = service.resolve_or_create_session(
        channel_type=GatewayChannelType.XIAOLUBAN,
        external_session_id="xiaoluban:account:welink-session",
        workspace_id="workspace-1",
        metadata={"source_provider": "xiaoluban"},
        channel_state={"receiver": "uid"},
    )
    session_service.delete(first.internal_session_id)

    second = service.resolve_or_create_session(
        channel_type=GatewayChannelType.XIAOLUBAN,
        external_session_id="xiaoluban:account:welink-session",
        workspace_id="workspace-1",
        metadata={"source_provider": "xiaoluban"},
        channel_state={"sender": "uid"},
    )

    assert second.gateway_session_id == first.gateway_session_id
    assert second.internal_session_id != first.internal_session_id
    assert second.internal_session_id in session_service.sessions
    assert second.active_run_id is None
    assert second.channel_state == {"receiver": "uid", "sender": "uid"}


def test_resolve_or_create_session_preserves_model_profile_override_on_rebind() -> None:
    repository = _FakeGatewaySessionRepository()
    session_service = _FakeSessionService()
    profile_store = GatewaySessionModelProfileStore()
    service = GatewaySessionService(
        repository=cast(GatewaySessionRepository, repository),
        session_service=cast(SessionService, session_service),
        session_model_profile_store=profile_store,
    )
    override = GatewayModelProfileOverride(
        model="gpt-4.1",
        base_url="https://example.test/v1",
        api_key="secret",
    )

    first = service.create_session(
        channel_type=GatewayChannelType.XIAOLUBAN,
        cwd=None,
        capabilities={},
        model_profile_override=override,
    )
    previous_profile = profile_store.get(first.internal_session_id)
    assert previous_profile is not None
    session_service.delete(first.internal_session_id)

    second = service.resolve_or_create_session(
        channel_type=GatewayChannelType.XIAOLUBAN,
        external_session_id=first.external_session_id,
        workspace_id="workspace-1",
        metadata={"source_provider": "xiaoluban"},
        channel_state={"sender": "uid"},
    )

    assert profile_store.get(first.internal_session_id) is None
    assert profile_store.get(second.internal_session_id) == previous_profile


class _FakeGatewaySessionRepository:
    def __init__(self) -> None:
        self.records: dict[str, GatewaySessionRecord] = {}

    def get_by_external(
        self,
        *,
        channel_type: GatewayChannelType,
        external_session_id: str,
    ) -> GatewaySessionRecord | None:
        for record in self.records.values():
            if (
                record.channel_type == channel_type
                and record.external_session_id == external_session_id
            ):
                return record
        return None

    def create(self, record: GatewaySessionRecord) -> GatewaySessionRecord:
        self.records[record.gateway_session_id] = record
        return record

    def update(self, record: GatewaySessionRecord) -> GatewaySessionRecord:
        self.records[record.gateway_session_id] = record
        return record


class _FakeSessionService:
    def __init__(self) -> None:
        self.sessions: dict[str, SessionRecord] = {}
        self._counter = 0

    def create_session(
        self,
        *,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
        session_mode: object | None = None,
        normal_root_role_id: str | None = None,
        orchestration_preset_id: str | None = None,
    ) -> SessionRecord:
        _ = (metadata, session_mode, normal_root_role_id, orchestration_preset_id)
        self._counter += 1
        session = SessionRecord(
            session_id=f"session-{self._counter}",
            workspace_id=workspace_id,
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
        )
        self.sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> SessionRecord:
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"Unknown session_id: {session_id}") from exc

    def delete(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)
