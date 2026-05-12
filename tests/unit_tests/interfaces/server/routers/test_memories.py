# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from relay_teams.interfaces.server.routers.memories import router
from relay_teams.memory.models import (
    MemoryContent,
    MemoryConsolidationResult,
    MemoryEntry,
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryEvolutionDraft,
    MemoryEvolutionDraftQueryResult,
    MemoryEvolutionStatus,
    MemoryEvolutionTarget,
    MemoryQueryResult,
    MemoryScope,
    MemorySearchResult,
    MemorySourceKind,
    MemoryTier,
)
from relay_teams.memory.service import MemoryBankService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(**overrides: object) -> MemoryEntry:
    now = datetime.now(tz=timezone.utc)
    entry = MemoryEntry(
        id="mem-test001",
        tier=MemoryTier.PERSISTENT,
        scope=MemoryScope.WORKSPACE,
        workspace_id="ws-1",
        kind=MemoryEntryKind.FACT,
        content=MemoryContent(title="Test fact", body="Body here"),
        source=MemorySourceKind.MANUAL,
        created_at=now,
        updated_at=now,
    )
    if overrides:
        entry = entry.model_copy(update=overrides)
    return entry


_ENTRY = _make_entry()


def _build_client(service: MemoryBankService) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[router.routes[0].depend()] = lambda: service  # type: ignore[union-attr]
    return TestClient(app)


def _make_draft(**overrides: object) -> MemoryEvolutionDraft:
    now = datetime.now(tz=timezone.utc)
    draft = MemoryEvolutionDraft(
        draft_id="mem-evo-test001",
        workspace_id="ws-1",
        source_memory_ids=("mem-test001",),
        target=MemoryEvolutionTarget.SOP_SKILL,
        status=MemoryEvolutionStatus.DRAFT,
        skill_id="test-sop",
        runtime_name="test-sop",
        description="Test SOP",
        instructions="# test-sop\n\n## Purpose\nTest",
        created_at=now,
        updated_at=now,
    )
    if overrides:
        draft = draft.model_copy(update=overrides)
    return draft


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


class TestRouteRegistration:
    def test_router_has_fourteen_routes(self) -> None:
        routes = [r for r in router.routes if isinstance(r, APIRoute)]
        assert len(routes) == 14

    def test_router_paths_match_spec(self) -> None:
        paths = {r.path for r in router.routes if isinstance(r, APIRoute)}
        wid = "/workspaces/{workspace_id}"
        assert "/memories" in paths
        assert "/memories/search" in paths
        assert f"{wid}/memories" in paths
        assert f"{wid}/memories/evolutions" in paths
        assert f"{wid}/memories/evolutions/{{draft_id}}" in paths
        assert f"{wid}/memories/evolutions/{{draft_id}}:apply" in paths
        assert f"{wid}/memories/evolutions/{{draft_id}}:reject" in paths
        assert f"{wid}/memories/{{memory_id}}" in paths
        assert f"{wid}/memories/consolidate" in paths
        assert f"{wid}/memories/search" in paths

    def test_list_memories_methods(self) -> None:
        route_map: dict[str, set[str]] = {}
        for r in router.routes:
            if isinstance(r, APIRoute):
                route_map.setdefault(r.path, set()).update(r.methods)
        wid = "/workspaces/{workspace_id}"
        assert "GET" in route_map.get("/memories", set())
        assert "POST" in route_map.get("/memories/search", set())
        assert "GET" in route_map.get(f"{wid}/memories", set())
        assert "POST" in route_map.get(f"{wid}/memories", set())
        assert "GET" in route_map.get(f"{wid}/memories/evolutions", set())
        assert "POST" in route_map.get(f"{wid}/memories/evolutions", set())
        assert "GET" in route_map.get(f"{wid}/memories/evolutions/{{draft_id}}", set())
        assert "POST" in route_map.get(
            f"{wid}/memories/evolutions/{{draft_id}}:apply", set()
        )
        assert "POST" in route_map.get(
            f"{wid}/memories/evolutions/{{draft_id}}:reject", set()
        )
        assert "GET" in route_map.get(f"{wid}/memories/{{memory_id}}", set())
        assert "PUT" in route_map.get(f"{wid}/memories/{{memory_id}}", set())
        assert "DELETE" in route_map.get(f"{wid}/memories/{{memory_id}}", set())
        assert "POST" in route_map.get(f"{wid}/memories/consolidate", set())
        assert "POST" in route_map.get(f"{wid}/memories/search", set())


# ---------------------------------------------------------------------------
# Endpoint integration with mocked service
# ---------------------------------------------------------------------------


class _FakeMemoryBankService:
    """Lightweight fake that records calls without touching a DB."""

    def __init__(self) -> None:
        self.list_entries_async: AsyncMock = AsyncMock(
            return_value=MemoryQueryResult(items=(), total_count=0, offset=0, limit=20)
        )
        self.create_entry_async: AsyncMock = AsyncMock(return_value=_ENTRY)
        self.get_entry_async: AsyncMock = AsyncMock(return_value=_ENTRY)
        self.update_entry_async: AsyncMock = AsyncMock(return_value=_ENTRY)
        self.delete_entry_async: AsyncMock = AsyncMock(return_value=True)
        self.consolidate_async: AsyncMock = AsyncMock(
            return_value=MemoryConsolidationResult(
                source_entry_count=1,
                consolidated_entry_count=1,
                superseded_entry_ids=("mem-src",),
                new_entry_ids=("mem-new",),
            )
        )
        self.search_async: AsyncMock = AsyncMock(
            return_value=MemorySearchResult(items=(), total_count=0)
        )
        self.search_global_async: AsyncMock = AsyncMock(
            return_value=MemorySearchResult(items=(), total_count=0)
        )


class _FakeMemoryEvolutionService:
    def __init__(self) -> None:
        draft = _make_draft()
        self.create_draft_async: AsyncMock = AsyncMock(return_value=draft)
        self.list_drafts_async: AsyncMock = AsyncMock(
            return_value=MemoryEvolutionDraftQueryResult(
                items=(draft,),
                total_count=1,
                offset=0,
                limit=20,
            )
        )
        self.get_draft_async: AsyncMock = AsyncMock(return_value=draft)
        self.apply_draft_async: AsyncMock = AsyncMock(
            return_value=draft.model_copy(
                update={
                    "status": MemoryEvolutionStatus.APPLIED,
                    "applied_skill_ref": "test-sop",
                }
            )
        )
        self.reject_draft_async: AsyncMock = AsyncMock(
            return_value=draft.model_copy(
                update={"status": MemoryEvolutionStatus.REJECTED}
            )
        )


def _client() -> tuple[TestClient, _FakeMemoryBankService]:
    client, svc, _ = _client_with_evolution()
    return client, svc


def _client_with_evolution() -> tuple[
    TestClient,
    _FakeMemoryBankService,
    _FakeMemoryEvolutionService,
]:
    svc = _FakeMemoryBankService()
    evolution_svc = _FakeMemoryEvolutionService()
    app = FastAPI()
    app.include_router(router, prefix="/api")

    # Override the DI dependency
    from relay_teams.interfaces.server.deps import (
        get_memory_bank_service,
        get_memory_evolution_service,
    )

    app.dependency_overrides[get_memory_bank_service] = lambda: svc
    app.dependency_overrides[get_memory_evolution_service] = lambda: evolution_svc
    return TestClient(app), svc, evolution_svc


# ---------------------------------------------------------------------------
# GET /memories  (global list)
# ---------------------------------------------------------------------------


class TestGlobalListMemories:
    def test_list_all_returns_200(self) -> None:
        client, svc = _client()
        response = client.get("/api/memories")
        assert response.status_code == 200
        svc.list_entries_async.assert_awaited_once()

    def test_list_all_passes_optional_workspace_and_filters(self) -> None:
        client, svc = _client()
        response = client.get(
            "/api/memories",
            params={
                "workspace_id": "ws-1",
                "scope": "role",
                "role_id": "writer",
                "status": "active",
                "tags": "legacy,role-memory",
            },
        )
        assert response.status_code == 200
        call_req = svc.list_entries_async.call_args[0][0]
        assert call_req.workspace_id == "ws-1"
        assert call_req.scope == MemoryScope.ROLE
        assert call_req.role_id == "writer"
        assert call_req.status == MemoryEntryStatus.ACTIVE
        assert call_req.tags == ("legacy", "role-memory")


# ---------------------------------------------------------------------------
# POST /memories/search  (global search)
# ---------------------------------------------------------------------------


class TestGlobalSearchMemories:
    def test_search_all_returns_200(self) -> None:
        client, svc = _client()
        response = client.post(
            "/api/memories/search",
            json={"text_query": "pydantic", "limit": 5},
        )
        assert response.status_code == 200
        svc.search_global_async.assert_awaited_once()

    def test_search_all_accepts_optional_workspace(self) -> None:
        client, svc = _client()
        response = client.post(
            "/api/memories/search",
            json={"workspace_id": "ws-1", "text_query": "pydantic"},
        )
        assert response.status_code == 200
        call_req = svc.search_global_async.call_args[0][0]
        assert call_req.workspace_id == "ws-1"
        assert call_req.text_query == "pydantic"


# ---------------------------------------------------------------------------
# GET /workspaces/{workspace_id}/memories  (list)
# ---------------------------------------------------------------------------


class TestListMemories:
    def test_list_returns_200(self) -> None:
        client, svc = _client()
        response = client.get("/api/workspaces/ws-1/memories")
        assert response.status_code == 200
        svc.list_entries_async.assert_awaited_once()

    def test_list_passes_query_params(self) -> None:
        client, svc = _client()
        response = client.get(
            "/api/workspaces/ws-1/memories",
            params={"tier": "persistent", "limit": 5, "offset": 0},
        )
        assert response.status_code == 200
        call_args = svc.list_entries_async.call_args[0][0]
        assert call_args.tier == MemoryTier.PERSISTENT
        assert call_args.limit == 5

    def test_list_invalid_tier_returns_422(self) -> None:
        client, _ = _client()
        response = client.get(
            "/api/workspaces/ws-1/memories",
            params={"tier": "invalid_tier"},
        )
        assert response.status_code == 422

    def test_list_invalid_limit_returns_422(self) -> None:
        client, _ = _client()
        response = client.get(
            "/api/workspaces/ws-1/memories",
            params={"limit": 0},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /workspaces/{workspace_id}/memories  (create)
# ---------------------------------------------------------------------------


class TestCreateMemory:
    def test_create_returns_201(self) -> None:
        client, svc = _client()
        response = client.post(
            "/api/workspaces/ws-1/memories",
            json={
                "tier": "persistent",
                "scope": "workspace",
                "workspace_id": "ws-1",
                "kind": "fact",
                "content": {"title": "Title", "body": "Body text"},
            },
        )
        assert response.status_code == 201
        svc.create_entry_async.assert_awaited_once()

    def test_create_patches_workspace_id(self) -> None:
        client, svc = _client()
        response = client.post(
            "/api/workspaces/ws-override/memories",
            json={
                "tier": "persistent",
                "scope": "workspace",
                "workspace_id": "ws-original",
                "kind": "fact",
                "content": {"title": "T", "body": "B"},
            },
        )
        assert response.status_code == 201
        call_req = svc.create_entry_async.call_args[0][0]
        assert call_req.workspace_id == "ws-override"

    def test_create_missing_body_returns_422(self) -> None:
        client, _ = _client()
        response = client.post("/api/workspaces/ws-1/memories", json={})
        assert response.status_code == 422

    def test_create_empty_content_title_returns_422(self) -> None:
        client, _ = _client()
        response = client.post(
            "/api/workspaces/ws-1/memories",
            json={
                "tier": "persistent",
                "scope": "workspace",
                "workspace_id": "ws-1",
                "kind": "fact",
                "content": {"title": "", "body": "Body text"},
            },
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Memory evolution draft endpoints
# ---------------------------------------------------------------------------


class TestMemoryEvolutionDrafts:
    def test_create_evolution_draft_returns_201(self) -> None:
        client, _, evolution_svc = _client_with_evolution()
        response = client.post(
            "/api/workspaces/ws-1/memories/evolutions",
            json={
                "workspace_id": "ws-original",
                "source_memory_ids": ["mem-test001"],
                "target": "sop_skill",
                "skill_id": "test-sop",
                "runtime_name": "test-sop",
            },
        )
        assert response.status_code == 201
        evolution_svc.create_draft_async.assert_awaited_once()
        call_req = evolution_svc.create_draft_async.call_args[0][0]
        assert call_req.workspace_id == "ws-1"

    def test_create_evolution_draft_returns_400_for_service_error(self) -> None:
        client, _, evolution_svc = _client_with_evolution()
        evolution_svc.create_draft_async = AsyncMock(
            side_effect=ValueError("Unknown source memory entry")
        )

        response = client.post(
            "/api/workspaces/ws-1/memories/evolutions",
            json={
                "workspace_id": "ws-original",
                "source_memory_ids": ["mem-missing"],
                "target": "sop_skill",
                "skill_id": "test-sop",
                "runtime_name": "test-sop",
            },
        )

        assert response.status_code == 400
        assert "Unknown source memory entry" in response.json()["detail"]

    def test_list_evolution_drafts_returns_200(self) -> None:
        client, _, evolution_svc = _client_with_evolution()
        response = client.get(
            "/api/workspaces/ws-1/memories/evolutions",
            params={"status": "draft", "target": "sop_skill"},
        )
        assert response.status_code == 200
        evolution_svc.list_drafts_async.assert_awaited_once()
        call_req = evolution_svc.list_drafts_async.call_args[0][0]
        assert call_req.status == MemoryEvolutionStatus.DRAFT
        assert call_req.target == MemoryEvolutionTarget.SOP_SKILL

    def test_get_evolution_draft_returns_200(self) -> None:
        client, _, evolution_svc = _client_with_evolution()
        response = client.get(
            "/api/workspaces/ws-1/memories/evolutions/mem-evo-test001"
        )
        assert response.status_code == 200
        evolution_svc.get_draft_async.assert_awaited_once_with(
            "ws-1", "mem-evo-test001"
        )

    def test_get_evolution_draft_returns_404_when_missing(self) -> None:
        client, _, evolution_svc = _client_with_evolution()
        evolution_svc.get_draft_async = AsyncMock(return_value=None)

        response = client.get(
            "/api/workspaces/ws-1/memories/evolutions/mem-evo-missing"
        )

        assert response.status_code == 404

    def test_apply_evolution_draft_returns_200(self) -> None:
        client, _, evolution_svc = _client_with_evolution()
        response = client.post(
            "/api/workspaces/ws-1/memories/evolutions/mem-evo-test001:apply",
            json={},
        )
        assert response.status_code == 200
        evolution_svc.apply_draft_async.assert_awaited_once()
        assert response.json()["status"] == "applied"

    def test_apply_evolution_draft_accepts_empty_body(self) -> None:
        client, _, evolution_svc = _client_with_evolution()

        response = client.post(
            "/api/workspaces/ws-1/memories/evolutions/mem-evo-test001:apply"
        )

        assert response.status_code == 200
        call_req = evolution_svc.apply_draft_async.call_args[0][2]
        assert call_req.skill_id is None

    def test_apply_evolution_draft_returns_409_for_service_error(self) -> None:
        client, _, evolution_svc = _client_with_evolution()
        evolution_svc.apply_draft_async = AsyncMock(
            side_effect=ValueError("not applicable")
        )

        response = client.post(
            "/api/workspaces/ws-1/memories/evolutions/mem-evo-test001:apply",
            json={},
        )

        assert response.status_code == 409
        assert "not applicable" in response.json()["detail"]

    def test_apply_evolution_draft_returns_404_when_missing(self) -> None:
        client, _, evolution_svc = _client_with_evolution()
        evolution_svc.apply_draft_async = AsyncMock(return_value=None)

        response = client.post(
            "/api/workspaces/ws-1/memories/evolutions/mem-evo-missing:apply",
            json={},
        )

        assert response.status_code == 404

    def test_reject_evolution_draft_returns_200(self) -> None:
        client, _, evolution_svc = _client_with_evolution()
        response = client.post(
            "/api/workspaces/ws-1/memories/evolutions/mem-evo-test001:reject",
            json={"reason": "duplicate"},
        )
        assert response.status_code == 200
        evolution_svc.reject_draft_async.assert_awaited_once()
        assert response.json()["status"] == "rejected"

    def test_reject_evolution_draft_accepts_empty_body(self) -> None:
        client, _, evolution_svc = _client_with_evolution()

        response = client.post(
            "/api/workspaces/ws-1/memories/evolutions/mem-evo-test001:reject"
        )

        assert response.status_code == 200
        call_req = evolution_svc.reject_draft_async.call_args[0][2]
        assert call_req.reason == ""

    def test_reject_evolution_draft_returns_409_for_service_error(self) -> None:
        client, _, evolution_svc = _client_with_evolution()
        evolution_svc.reject_draft_async = AsyncMock(
            side_effect=ValueError("not rejectable")
        )

        response = client.post(
            "/api/workspaces/ws-1/memories/evolutions/mem-evo-test001:reject",
            json={},
        )

        assert response.status_code == 409
        assert "not rejectable" in response.json()["detail"]

    def test_reject_evolution_draft_returns_404_when_missing(self) -> None:
        client, _, evolution_svc = _client_with_evolution()
        evolution_svc.reject_draft_async = AsyncMock(return_value=None)

        response = client.post(
            "/api/workspaces/ws-1/memories/evolutions/mem-evo-missing:reject",
            json={},
        )

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /workspaces/{workspace_id}/memories/{memory_id}  (get)
# ---------------------------------------------------------------------------


class TestGetMemory:
    def test_get_returns_200(self) -> None:
        client, svc = _client()
        response = client.get("/api/workspaces/ws-1/memories/mem-test001")
        assert response.status_code == 200
        svc.get_entry_async.assert_awaited_once()

    def test_get_nonexistent_returns_404(self) -> None:
        client, svc = _client()
        svc.get_entry_async = AsyncMock(return_value=None)
        response = client.get("/api/workspaces/ws-1/memories/mem-nope")
        assert response.status_code == 404

    def test_get_wrong_workspace_returns_404(self) -> None:
        client, svc = _client()
        svc.get_entry_async = AsyncMock(
            return_value=_make_entry(workspace_id="ws-other")
        )
        response = client.get("/api/workspaces/ws-1/memories/mem-test001")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# PUT /workspaces/{workspace_id}/memories/{memory_id}  (update)
# ---------------------------------------------------------------------------


class TestUpdateMemory:
    def test_update_returns_200(self) -> None:
        client, svc = _client()
        response = client.put(
            "/api/workspaces/ws-1/memories/mem-test001",
            json={"content": {"title": "Updated", "body": "New body"}},
        )
        assert response.status_code == 200
        svc.update_entry_async.assert_awaited_once()

    def test_update_nonexistent_returns_404(self) -> None:
        client, svc = _client()
        svc.get_entry_async = AsyncMock(return_value=None)
        response = client.put(
            "/api/workspaces/ws-1/memories/mem-nope",
            json={"content": {"title": "X", "body": "Y"}},
        )
        assert response.status_code == 404

    def test_update_wrong_workspace_returns_404(self) -> None:
        client, svc = _client()
        svc.get_entry_async = AsyncMock(
            return_value=_make_entry(workspace_id="ws-other")
        )
        response = client.put(
            "/api/workspaces/ws-1/memories/mem-test001",
            json={"content": {"title": "X", "body": "Y"}},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /workspaces/{workspace_id}/memories/{memory_id}
# ---------------------------------------------------------------------------


class TestDeleteMemory:
    def test_delete_returns_204(self) -> None:
        client, svc = _client()
        response = client.delete("/api/workspaces/ws-1/memories/mem-test001")
        assert response.status_code == 204
        svc.delete_entry_async.assert_awaited_once()

    def test_delete_nonexistent_returns_404(self) -> None:
        client, svc = _client()
        svc.get_entry_async = AsyncMock(return_value=None)
        response = client.delete("/api/workspaces/ws-1/memories/mem-nope")
        assert response.status_code == 404

    def test_delete_wrong_workspace_returns_404(self) -> None:
        client, svc = _client()
        svc.get_entry_async = AsyncMock(
            return_value=_make_entry(workspace_id="ws-other")
        )
        response = client.delete("/api/workspaces/ws-1/memories/mem-test001")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /workspaces/{workspace_id}/memories/consolidate
# ---------------------------------------------------------------------------


class TestConsolidateMemories:
    def test_consolidate_returns_200(self) -> None:
        client, svc = _client()
        response = client.post(
            "/api/workspaces/ws-1/memories/consolidate",
            json={
                "workspace_id": "ws-1",
                "target_tier": "medium_term",
                "target_scope": "session",
            },
        )
        assert response.status_code == 200
        svc.consolidate_async.assert_awaited_once()

    def test_consolidate_patches_workspace_id(self) -> None:
        client, svc = _client()
        response = client.post(
            "/api/workspaces/ws-override/memories/consolidate",
            json={
                "workspace_id": "ws-original",
                "target_tier": "persistent",
                "target_scope": "workspace",
            },
        )
        assert response.status_code == 200
        call_req = svc.consolidate_async.call_args[0][0]
        assert call_req.workspace_id == "ws-override"

    def test_consolidate_invalid_tier_returns_422(self) -> None:
        client, _ = _client()
        response = client.post(
            "/api/workspaces/ws-1/memories/consolidate",
            json={
                "workspace_id": "ws-1",
                "target_tier": "working",
                "target_scope": "workspace",
            },
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /workspaces/{workspace_id}/memories/search
# ---------------------------------------------------------------------------


class TestSearchMemories:
    def test_search_returns_200(self) -> None:
        client, svc = _client()
        response = client.post(
            "/api/workspaces/ws-1/memories/search",
            json={"workspace_id": "ws-1", "text_query": "pydantic"},
        )
        assert response.status_code == 200
        svc.search_async.assert_awaited_once()

    def test_search_patches_workspace_id(self) -> None:
        client, svc = _client()
        response = client.post(
            "/api/workspaces/ws-search/memories/search",
            json={"workspace_id": "ws-original", "text_query": "test"},
        )
        assert response.status_code == 200
        call_req = svc.search_async.call_args[0][0]
        assert call_req.workspace_id == "ws-search"

    def test_search_empty_query_returns_422(self) -> None:
        client, _ = _client()
        response = client.post(
            "/api/workspaces/ws-1/memories/search",
            json={"workspace_id": "ws-1", "text_query": ""},
        )
        assert response.status_code == 422

    def test_search_missing_body_returns_422(self) -> None:
        client, _ = _client()
        response = client.post("/api/workspaces/ws-1/memories/search", json={})
        assert response.status_code == 422
