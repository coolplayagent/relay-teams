from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import pytest

from relay_teams.interfaces.server.deps import get_session_service
from relay_teams.interfaces.server.routers import sessions
from relay_teams.providers import AgentTokenSummary, RunTokenUsage, SessionTokenUsage
from relay_teams.roles import SystemRolesUnavailableError
from relay_teams.sessions.session_models import (
    SessionCreateMetadata,
    SessionMetadataPatch,
    SessionMode,
    SessionRecord,
)


class _FakeSessionService:
    def __init__(self) -> None:
        self.created_calls: list[tuple[str | None, str, dict[str, str] | None]] = []
        self.updated_calls: list[tuple[str, SessionMetadataPatch]] = []
        self.topology_update_calls: list[tuple[str, str, str | None, str | None]] = []
        self.delete_subagent_calls: list[tuple[str, str]] = []
        self.reflection_refresh_calls: list[tuple[str, str]] = []
        self.reflection_update_calls: list[tuple[str, str, str]] = []
        self.reflection_delete_calls: list[tuple[str, str]] = []
        self.create_session_error: Exception | None = None
        self.raise_missing = False
        self.raise_missing = False
        self.deleted_calls: list[tuple[str, bool, bool]] = []
        self.delete_error: Exception | None = None
        self.raise_missing_list_agents = False
        self.raise_missing_list_subagents = False
        self.delete_subagent_error: Exception | None = None

    def create_session(
        self,
        *,
        session_id: str | None = None,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
    ) -> SessionRecord:
        if self.create_session_error is not None:
            raise self.create_session_error
        self.created_calls.append((session_id, workspace_id, metadata))
        return SessionRecord(
            session_id=session_id or "session-created",
            workspace_id=workspace_id,
            metadata={} if metadata is None else dict(metadata),
        )

    def update_session(self, session_id: str, patch: SessionMetadataPatch) -> None:
        if self.raise_missing:
            raise KeyError(session_id)
        self.updated_calls.append((session_id, patch))

    def list_sessions(self) -> tuple[SessionRecord, ...]:  # pragma: no cover
        raise AssertionError("not used")

    def list_normal_mode_subagents(
        self, session_id: str
    ) -> tuple[dict[str, object], ...]:
        if self.raise_missing_list_subagents:
            raise KeyError(session_id)
        return (
            {
                "instance_id": "inst-subagent-1",
                "role_id": "Explorer",
                "run_id": "subagent_run_123",
                "title": "Explore issue",
                "status": "completed",
                "run_status": "running",
                "run_phase": "running",
                "last_event_id": 12,
                "checkpoint_event_id": 8,
                "stream_connected": True,
                "conversation_id": "conv_session_1_explorer_inst_subagent_1",
            },
        )

    def list_agents_in_session(self, session_id: str) -> tuple[dict[str, object], ...]:
        if self.raise_missing_list_agents:
            raise KeyError(session_id)
        return (
            {
                "instance_id": "inst-coordinator-1",
                "role_id": "Coordinator",
                "run_id": "run_123",
                "status": "completed",
                "conversation_id": "conv_session_1_coordinator_inst_coordinator_1",
            },
        )

    def get_session(self, session_id: str) -> SessionRecord:  # pragma: no cover
        raise AssertionError(f"not used: {session_id}")

    def delete_session(
        self,
        session_id: str,
        *,
        force: bool = False,
        cascade: bool = False,
    ) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted_calls.append((session_id, force, cascade))

    def delete_normal_mode_subagent(
        self,
        session_id: str,
        instance_id: str,
    ) -> None:
        if self.delete_subagent_error is not None:
            raise self.delete_subagent_error
        self.delete_subagent_calls.append((session_id, instance_id))

    def update_session_topology(
        self,
        session_id: str,
        *,
        session_mode: SessionMode,
        normal_root_role_id: str | None,
        orchestration_preset_id: str | None,
    ) -> SessionRecord:
        self.topology_update_calls.append(
            (
                session_id,
                session_mode.value,
                normal_root_role_id,
                orchestration_preset_id,
            )
        )
        return SessionRecord(
            session_id=session_id,
            workspace_id="workspace-1",
            session_mode=session_mode,
            normal_root_role_id=normal_root_role_id,
            orchestration_preset_id=orchestration_preset_id,
        )

    def get_token_usage_by_session(self, session_id: str) -> SessionTokenUsage:
        return SessionTokenUsage(
            session_id=session_id,
            total_input_tokens=120,
            total_cached_input_tokens=48,
            total_output_tokens=30,
            total_reasoning_output_tokens=9,
            total_tokens=150,
            total_requests=3,
            total_tool_calls=1,
            by_role={
                "coordinator": AgentTokenSummary(
                    instance_id="",
                    role_id="coordinator",
                    input_tokens=120,
                    cached_input_tokens=48,
                    output_tokens=30,
                    reasoning_output_tokens=9,
                    total_tokens=150,
                    requests=3,
                    tool_calls=1,
                )
            },
        )

    def get_token_usage_by_run(self, run_id: str) -> RunTokenUsage:
        return RunTokenUsage(
            run_id=run_id,
            total_input_tokens=44,
            total_cached_input_tokens=12,
            total_output_tokens=10,
            total_reasoning_output_tokens=4,
            total_tokens=54,
            total_requests=2,
            total_tool_calls=0,
            by_agent=[
                AgentTokenSummary(
                    instance_id="inst-1",
                    role_id="coordinator",
                    input_tokens=44,
                    cached_input_tokens=12,
                    output_tokens=10,
                    reasoning_output_tokens=4,
                    total_tokens=54,
                    requests=2,
                    tool_calls=0,
                )
            ],
        )

    def get_agent_reflection(
        self, session_id: str, instance_id: str
    ) -> dict[str, object]:
        return {
            "session_id": session_id,
            "instance_id": instance_id,
            "role_id": "writer",
            "summary": "Use concise drafts.",
            "updated_at": "2026-03-13T00:01:30Z",
            "source": "stored",
        }

    async def refresh_subagent_reflection(
        self, session_id: str, instance_id: str
    ) -> dict[str, object]:
        self.reflection_refresh_calls.append((session_id, instance_id))
        return {
            "session_id": session_id,
            "instance_id": instance_id,
            "role_id": "writer",
            "summary": "Use concise drafts.",
            "updated_at": "2026-03-13T00:02:00Z",
            "source": "manual",
        }

    def update_agent_reflection(
        self,
        session_id: str,
        instance_id: str,
        *,
        summary: str,
    ) -> dict[str, object]:
        self.reflection_update_calls.append((session_id, instance_id, summary))
        return {
            "session_id": session_id,
            "instance_id": instance_id,
            "role_id": "writer",
            "summary": summary,
            "updated_at": "2026-03-13T00:03:00Z",
            "source": "manual_edit",
        }

    def delete_agent_reflection(
        self, session_id: str, instance_id: str
    ) -> dict[str, object]:
        self.reflection_delete_calls.append((session_id, instance_id))
        return {
            "session_id": session_id,
            "instance_id": instance_id,
            "role_id": "writer",
            "summary": "",
            "updated_at": None,
            "source": "manual_delete",
        }


def _create_client(fake_service: _FakeSessionService) -> TestClient:
    app = FastAPI()
    app.include_router(sessions.router, prefix="/api")
    app.dependency_overrides[get_session_service] = lambda: fake_service
    return TestClient(app)


def test_update_session_route_accepts_metadata_payload() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.patch(
        "/api/sessions/session-1",
        json={"title": "Renamed Session", "custom_metadata": {"label": "visible-name"}},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert fake_service.updated_calls == [
        (
            "session-1",
            SessionMetadataPatch(
                title="Renamed Session", custom_metadata={"label": "visible-name"}
            ),
        )
    ]


@pytest.mark.timeout(5)
def test_create_session_route_returns_created_session() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/sessions",
        json={"session_id": "session-1", "workspace_id": "default"},
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == "session-1"
    assert fake_service.created_calls == [("session-1", "default", None)]


def test_create_session_route_accepts_explicit_metadata_payload() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/sessions",
        json={
            "session_id": "session-1",
            "workspace_id": "default",
            "metadata": {
                "title": "Customer Support",
                "source_label": "Group Chat",
                "custom_metadata": {"project": "demo"},
            },
        },
    )

    assert response.status_code == 200
    assert fake_service.created_calls == [
        (
            "session-1",
            "default",
            SessionCreateMetadata(
                title="Customer Support",
                source_label="Group Chat",
                custom_metadata={"project": "demo"},
            ).to_metadata_dict(),
        )
    ]


def test_create_session_route_accepts_legacy_flat_metadata_payload() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/sessions",
        json={
            "session_id": "session-1",
            "workspace_id": "default",
            "metadata": {
                "title": "Customer Support",
                "project": "demo",
                "channel": "feishu",
            },
        },
    )

    assert response.status_code == 200
    assert fake_service.created_calls == [
        (
            "session-1",
            "default",
            {
                "title": "Customer Support",
                "title_source": "manual",
                "project": "demo",
                "channel": "feishu",
            },
        )
    ]


def test_create_session_route_ignores_reserved_keys_in_legacy_flat_metadata_payload() -> (
    None
):
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/sessions",
        json={
            "session_id": "session-1",
            "workspace_id": "default",
            "metadata": {
                "title": "Customer Support",
                "project": "demo",
                "source_provider": "feishu",
                "feishu_chat_id": "chat-1",
            },
        },
    )

    assert response.status_code == 200
    assert fake_service.created_calls == [
        (
            "session-1",
            "default",
            {
                "title": "Customer Support",
                "title_source": "manual",
                "project": "demo",
            },
        )
    ]


def test_create_session_route_rejects_reserved_custom_metadata_key() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/sessions",
        json={
            "workspace_id": "default",
            "metadata": {"custom_metadata": {"source_label": "bad"}},
        },
    )

    assert response.status_code == 422
    assert fake_service.created_calls == []


def test_create_session_route_rejects_title_source_without_title() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/sessions",
        json={
            "workspace_id": "default",
            "metadata": {"title_source": "manual"},
        },
    )

    assert response.status_code == 422
    assert fake_service.created_calls == []


def test_create_session_route_rejects_none_like_session_id() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/sessions",
        json={"session_id": "None", "workspace_id": "default"},
    )

    assert response.status_code == 422
    assert fake_service.created_calls == []


def test_create_session_route_returns_503_when_system_roles_are_missing() -> None:
    fake_service = _FakeSessionService()
    fake_service.create_session_error = SystemRolesUnavailableError(
        "Required system roles are unavailable: main_agent: missing"
    )
    client = _create_client(fake_service)

    response = client.post("/api/sessions", json={"workspace_id": "default"})

    assert response.status_code == 503
    assert "Required system roles are unavailable" in response.json()["detail"]


def test_update_session_route_returns_not_found_for_missing_session() -> None:
    fake_service = _FakeSessionService()
    fake_service.raise_missing = True
    client = _create_client(fake_service)

    response = client.patch(
        "/api/sessions/missing-session",
        json={"title": "Renamed Session", "custom_metadata": {"label": "visible-name"}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}


def test_update_session_route_accepts_legacy_flat_metadata_snapshot() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.patch(
        "/api/sessions/session-1",
        json={
            "title": "Renamed Session",
            "title_source": "manual",
            "source_label": "Feishu",
            "source_icon": "message",
            "source_provider": "feishu",
            "feishu_chat_id": "chat-1",
            "project": "demo",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert fake_service.updated_calls == [
        (
            "session-1",
            SessionMetadataPatch(
                title="Renamed Session",
                title_source="manual",
                source_label="Feishu",
                source_icon="message",
                custom_metadata={"project": "demo"},
            ),
        )
    ]


def test_update_session_route_accepts_legacy_wrapped_metadata_snapshot() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.patch(
        "/api/sessions/session-1",
        json={
            "metadata": {
                "title": "Renamed Session",
                "source_provider": "feishu",
                "project": "demo",
            }
        },
    )

    assert response.status_code == 200
    assert fake_service.updated_calls == [
        (
            "session-1",
            SessionMetadataPatch(
                title="Renamed Session",
                custom_metadata={"project": "demo"},
            ),
        )
    ]


def test_update_session_route_clears_title_for_legacy_snapshot_without_title() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.patch(
        "/api/sessions/session-1",
        json={
            "title_source": "manual",
            "source_provider": "feishu",
            "feishu_chat_id": "chat-1",
            "project": "demo",
        },
    )

    assert response.status_code == 200
    assert fake_service.updated_calls == [
        (
            "session-1",
            SessionMetadataPatch(
                title=None,
                custom_metadata={"project": "demo"},
            ),
        )
    ]


def test_update_session_route_rejects_reserved_custom_metadata_key() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.patch(
        "/api/sessions/session-1",
        json={"custom_metadata": {"source_label": "bad"}},
    )

    assert response.status_code == 422
    assert fake_service.updated_calls == []


def test_list_session_subagents_route_returns_projected_subagents() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.get("/api/sessions/session-1/subagents")

    assert response.status_code == 200
    assert response.json() == [
        {
            "instance_id": "inst-subagent-1",
            "role_id": "Explorer",
            "run_id": "subagent_run_123",
            "title": "Explore issue",
            "status": "completed",
            "run_status": "running",
            "run_phase": "running",
            "last_event_id": 12,
            "checkpoint_event_id": 8,
            "stream_connected": True,
            "conversation_id": "conv_session_1_explorer_inst_subagent_1",
        }
    ]


def test_delete_session_subagent_route_returns_ok() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.delete("/api/sessions/session-1/subagents/inst-subagent-1")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert fake_service.delete_subagent_calls == [("session-1", "inst-subagent-1")]


def test_delete_session_subagent_route_returns_not_found() -> None:
    fake_service = _FakeSessionService()
    fake_service.delete_subagent_error = KeyError("missing")
    client = _create_client(fake_service)

    response = client.delete("/api/sessions/session-1/subagents/inst-missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "Subagent not found"}


def test_delete_session_subagent_route_returns_conflict_for_running_subagent() -> None:
    fake_service = _FakeSessionService()
    fake_service.delete_subagent_error = RuntimeError(
        "Cannot delete a running subagent"
    )
    client = _create_client(fake_service)

    response = client.delete("/api/sessions/session-1/subagents/inst-running")

    assert response.status_code == 409
    assert response.json() == {"detail": "Cannot delete a running subagent"}


def test_list_session_agents_route_returns_not_found_for_missing_session() -> None:
    fake_service = _FakeSessionService()
    fake_service.raise_missing_list_agents = True
    client = _create_client(fake_service)

    response = client.get("/api/sessions/missing-session/agents")

    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}


def test_list_session_subagents_route_returns_not_found_for_missing_session() -> None:
    fake_service = _FakeSessionService()
    fake_service.raise_missing_list_subagents = True
    client = _create_client(fake_service)

    response = client.get("/api/sessions/missing-session/subagents")

    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}


def test_update_session_route_rejects_none_like_path_identifier() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.patch(
        "/api/sessions/None",
        json={"title": "Renamed Session", "custom_metadata": {"label": "visible-name"}},
    )

    assert response.status_code == 422
    assert fake_service.updated_calls == []


def test_update_session_topology_route_returns_updated_session() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.patch(
        "/api/sessions/session-1/topology",
        json={
            "session_mode": "orchestration",
            "orchestration_preset_id": "default",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_mode"] == "orchestration"
    assert payload["orchestration_preset_id"] == "default"
    assert fake_service.topology_update_calls == [
        ("session-1", "orchestration", None, "default")
    ]


def test_update_session_topology_route_accepts_normal_root_role() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.patch(
        "/api/sessions/session-1/topology",
        json={
            "session_mode": "normal",
            "normal_root_role_id": "Crafter",
            "orchestration_preset_id": None,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_mode"] == "normal"
    assert payload["normal_root_role_id"] == "Crafter"
    assert fake_service.topology_update_calls == [
        ("session-1", "normal", "Crafter", None)
    ]


def test_get_session_token_usage_route_returns_extended_totals() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.get("/api/sessions/session-1/token-usage")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "session-1",
        "total_input_tokens": 120,
        "total_cached_input_tokens": 48,
        "total_output_tokens": 30,
        "total_reasoning_output_tokens": 9,
        "total_tokens": 150,
        "total_requests": 3,
        "total_tool_calls": 1,
        "by_role": {
            "coordinator": {
                "role_id": "coordinator",
                "input_tokens": 120,
                "cached_input_tokens": 48,
                "output_tokens": 30,
                "reasoning_output_tokens": 9,
                "total_tokens": 150,
                "requests": 3,
                "tool_calls": 1,
            }
        },
    }


def test_get_run_token_usage_route_returns_extended_totals() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.get("/api/sessions/session-1/runs/run-1/token-usage")

    assert response.status_code == 200
    assert response.json() == {
        "run_id": "run-1",
        "total_input_tokens": 44,
        "total_cached_input_tokens": 12,
        "total_output_tokens": 10,
        "total_reasoning_output_tokens": 4,
        "total_tokens": 54,
        "total_requests": 2,
        "total_tool_calls": 0,
        "by_agent": [
            {
                "instance_id": "inst-1",
                "role_id": "coordinator",
                "input_tokens": 44,
                "cached_input_tokens": 12,
                "output_tokens": 10,
                "reasoning_output_tokens": 4,
                "total_tokens": 54,
                "requests": 2,
                "tool_calls": 0,
            }
        ],
    }


def test_get_agent_reflection_route_returns_projection() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.get("/api/sessions/session-1/agents/inst-1/reflection")

    assert response.status_code == 200
    assert response.json()["instance_id"] == "inst-1"
    assert response.json()["source"] == "stored"


def test_refresh_agent_reflection_route_returns_projection() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.post("/api/sessions/session-1/agents/inst-1/reflection:refresh")

    assert response.status_code == 200
    assert response.json()["source"] == "manual"
    assert fake_service.reflection_refresh_calls == [("session-1", "inst-1")]


def test_update_agent_reflection_route_returns_projection() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.patch(
        "/api/sessions/session-1/agents/inst-1/reflection",
        json={"summary": "Keep implementation notes concise."},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "manual_edit"
    assert fake_service.reflection_update_calls == [
        ("session-1", "inst-1", "Keep implementation notes concise.")
    ]


def test_delete_agent_reflection_route_returns_projection() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.delete("/api/sessions/session-1/agents/inst-1/reflection")

    assert response.status_code == 200
    assert response.json()["source"] == "manual_delete"
    assert fake_service.reflection_delete_calls == [("session-1", "inst-1")]


def test_delete_session_route_forwards_force_and_cascade() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.request(
        "DELETE",
        "/api/sessions/session-1",
        json={"force": True, "cascade": True},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert fake_service.deleted_calls == [("session-1", True, True)]


def test_delete_session_route_returns_conflict_for_missing_cascade() -> None:
    fake_service = _FakeSessionService()
    fake_service.delete_error = RuntimeError(
        "Cannot delete session without cascade while related session data exists"
    )
    client = _create_client(fake_service)

    response = client.request("DELETE", "/api/sessions/session-1")

    assert response.status_code == 409
    assert "without cascade" in response.json()["detail"]


def test_delete_session_route_returns_not_found() -> None:
    fake_service = _FakeSessionService()
    fake_service.delete_error = KeyError("session-1")
    client = _create_client(fake_service)

    response = client.request("DELETE", "/api/sessions/session-1")

    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}
