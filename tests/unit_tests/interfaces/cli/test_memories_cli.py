# -*- coding: utf-8 -*-
from __future__ import annotations

import json

import pytest
from typer import Typer
from typer.testing import CliRunner

from relay_teams.interfaces.cli import app as cli_app
from relay_teams.interfaces.cli.memories_cli import (
    MemoriesOutputFormat,
    _render_entry_detail,
    _render_evolution_table,
    _render_memories_table,
    _render_search_table,
    _require_object_response,
    build_memories_app,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _CapturingAutoStart:
    """Records calls to auto_start_if_needed without launching a server."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, bool, bool]] = []

    def __call__(
        self, base_url: str, autostart: bool, daemon: bool, force: bool
    ) -> None:
        self.calls.append((base_url, autostart, daemon, force))


class _FakeRequestJson:
    """Returns canned responses based on HTTP method and path."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, object]] = []

    def __call__(
        self,
        base_url: str,
        method: str,
        path: str,
        body: dict[str, object] | None,
    ) -> dict[str, object]:
        self.calls.append((base_url, method, path, body))
        normalized_path = path.split("?", maxsplit=1)[0]
        if "/memories/evolutions" in normalized_path:
            if method == "GET" and normalized_path.endswith("/evolutions"):
                return {
                    "items": [
                        {
                            "draft_id": "mem-evo-001",
                            "status": "draft",
                            "target": "sop_skill",
                            "runtime_name": "review-loop-sop",
                            "skill_id": "review-loop-sop",
                        }
                    ],
                    "total_count": 1,
                    "offset": 0,
                    "limit": 20,
                }
            status = "applied" if path.endswith(":apply") else "draft"
            if path.endswith(":reject"):
                status = "rejected"
            return {
                "draft_id": "mem-evo-001",
                "workspace_id": "ws-1",
                "source_memory_ids": ["mem-001"],
                "target": "sop_skill",
                "status": status,
                "skill_id": "review-loop-sop",
                "runtime_name": "review-loop-sop",
                "description": "Review loop SOP",
                "instructions": "# review-loop-sop",
                "applied_skill_ref": "review-loop-sop" if status == "applied" else None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        if method == "DELETE":
            return {"ok": True}
        if normalized_path.endswith("/consolidate"):
            return {
                "source_entry_count": 3,
                "consolidated_entry_count": 2,
                "superseded_entry_ids": ("mem-a", "mem-b"),
                "new_entry_ids": ("mem-c", "mem-d"),
            }
        if method == "POST" and normalized_path.endswith("/memories"):
            # Create endpoint returns a single entry
            return {
                "id": "mem-new01",
                "tier": "persistent",
                "scope": "workspace",
                "kind": "fact",
                "status": "active",
                "version": 1,
                "confidence_score": 1.0,
                "source": "manual",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "expires_at": None,
                "tags": [],
                "content": {"title": "Created", "body": "Created body"},
            }
        if (
            method == "GET"
            and "/memories/" in normalized_path
            and normalized_path.count("/") == 5
        ):
            # Single-entry GET
            return {
                "id": "mem-det01",
                "tier": "persistent",
                "scope": "workspace",
                "kind": "fact",
                "status": "active",
                "version": 1,
                "confidence_score": 0.95,
                "source": "manual",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "expires_at": None,
                "tags": ["python", "testing"],
                "content": {"title": "A fact", "body": "Some body text"},
            }
        # Default: list response
        return {
            "items": [
                {
                    "id": "mem-001",
                    "tier": "persistent",
                    "kind": "fact",
                    "content_title": "Pydantic models",
                    "confidence_score": 0.95,
                },
                {
                    "id": "mem-002",
                    "tier": "working",
                    "kind": "insight",
                    "content_title": "Working insight",
                    "confidence_score": 0.7,
                },
            ],
            "total_count": 2,
            "offset": 0,
        }


def _build_app(
    *,
    default_base_url: str = "http://localhost:8765",
) -> tuple[Typer, _FakeRequestJson, _CapturingAutoStart]:
    fake_req = _FakeRequestJson()
    fake_auto = _CapturingAutoStart()
    app = build_memories_app(
        request_json=fake_req,
        auto_start_if_needed=fake_auto,
        default_base_url=default_base_url,
    )
    return app, fake_req, fake_auto


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------


class TestCommandRegistration:
    def test_memories_help_lists_all_commands(self) -> None:
        result = runner.invoke(cli_app.app, ["memories", "--help"])
        assert result.exit_code == 0
        output = result.output.lower()
        for cmd in (
            "list",
            "get",
            "create",
            "delete",
            "search",
            "consolidate",
            "evolve",
        ):
            assert cmd in output

    def test_memories_registered_in_main_app(self) -> None:
        result = runner.invoke(cli_app.app, ["--help"])
        assert result.exit_code == 0
        assert "memories" in result.output.lower()


# ---------------------------------------------------------------------------
# list command
# ---------------------------------------------------------------------------


class TestListCommand:
    def test_list_table_output(self) -> None:
        app_obj, fake_req, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(app_obj, ["list", "--workspace-id", "ws-1"])
        assert r.exit_code == 0
        assert "mem-001" in r.output
        assert "mem-002" in r.output
        # Verify correct HTTP method and path
        assert fake_req.calls[0][1] == "GET"

    def test_list_json_output(self) -> None:
        app_obj, fake_req, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj, ["list", "--workspace-id", "ws-1", "--format", "json"]
        )
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["total_count"] == 2

    def test_list_passes_optional_filters(self) -> None:
        app_obj, fake_req, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj,
            [
                "list",
                "--workspace-id",
                "ws-1",
                "--tier",
                "persistent",
                "--scope",
                "workspace",
                "--role-id",
                "role-1",
            ],
        )
        assert r.exit_code == 0
        _, _, path, body = fake_req.calls[0]
        assert body is None
        assert "tier=persistent" in path
        assert "scope=workspace" in path
        assert "role_id=role-1" in path


# ---------------------------------------------------------------------------
# get command
# ---------------------------------------------------------------------------


class TestGetCommand:
    def test_get_table_output(self) -> None:
        app_obj, _, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj,
            ["get", "--workspace-id", "ws-1", "--memory-id", "mem-det01"],
        )
        assert r.exit_code == 0
        assert "mem-det01" in r.output
        assert "persistent" in r.output

    def test_get_json_output(self) -> None:
        app_obj, _, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj,
            [
                "get",
                "--workspace-id",
                "ws-1",
                "--memory-id",
                "mem-det01",
                "--format",
                "json",
            ],
        )
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["id"] == "mem-det01"


# ---------------------------------------------------------------------------
# create command
# ---------------------------------------------------------------------------


class TestCreateCommand:
    def test_create_table_output(self) -> None:
        app_obj, fake_req, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj,
            [
                "create",
                "--workspace-id",
                "ws-1",
                "--content",
                "Important fact about X",
            ],
        )
        assert r.exit_code == 0
        assert fake_req.calls[0][1] == "POST"
        assert "Created memory entry" in r.output

    def test_create_json_output(self) -> None:
        app_obj, _, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj,
            [
                "create",
                "--workspace-id",
                "ws-1",
                "--content",
                "Fact X",
                "--format",
                "json",
            ],
        )
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert "id" in data

    def test_create_with_tags(self) -> None:
        app_obj, fake_req, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj,
            [
                "create",
                "--workspace-id",
                "ws-1",
                "--content",
                "Tagged content",
                "--tags",
                "python, testing",
            ],
        )
        assert r.exit_code == 0
        _, _, _, body = fake_req.calls[0]
        assert isinstance(body, dict)
        assert body.get("tags") == ["python", "testing"]


# ---------------------------------------------------------------------------
# delete command
# ---------------------------------------------------------------------------


class TestDeleteCommand:
    def test_delete_output(self) -> None:
        app_obj, fake_req, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj,
            [
                "delete",
                "--workspace-id",
                "ws-1",
                "--memory-id",
                "mem-target",
            ],
        )
        assert r.exit_code == 0
        assert "Deleted memory entry: mem-target" in r.output
        assert fake_req.calls[0][1] == "DELETE"


# ---------------------------------------------------------------------------
# search command
# ---------------------------------------------------------------------------


class TestSearchCommand:
    def test_search_table_output(self) -> None:
        app_obj, fake_req, fake_auto = _build_app()
        # Override request_json to return search-like results
        search_response: dict[str, object] = {
            "items": [
                {
                    "entry": {
                        "id": "mem-001",
                        "content_title": "Pydantic fact",
                    },
                    "rank": 1,
                    "score": 0.99,
                    "snippet": "A snippet about pydantic",
                }
            ],
            "total_count": 1,
        }

        def _search_req(
            base_url: str,
            method: str,
            path: str,
            body: dict[str, object] | None,
        ) -> dict[str, object]:
            return search_response

        app_s = build_memories_app(
            request_json=_search_req,
            auto_start_if_needed=fake_auto,
            default_base_url="http://localhost:8765",
        )
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_s,
            ["search", "--workspace-id", "ws-1", "--query", "pydantic"],
        )
        assert r.exit_code == 0
        assert "Found 1 result" in r.output

    def test_search_json_output(self) -> None:
        search_response: dict[str, object] = {
            "items": [],
            "total_count": 0,
        }

        def _search_req(
            base_url: str,
            method: str,
            path: str,
            body: dict[str, object] | None,
        ) -> dict[str, object]:
            return search_response

        app_s = build_memories_app(
            request_json=_search_req,
            auto_start_if_needed=_CapturingAutoStart(),
            default_base_url="http://localhost:8765",
        )
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_s,
            [
                "search",
                "--workspace-id",
                "ws-1",
                "--query",
                "test",
                "--format",
                "json",
            ],
        )
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["total_count"] == 0


# ---------------------------------------------------------------------------
# consolidate command
# ---------------------------------------------------------------------------


class TestConsolidateCommand:
    def test_consolidate_output(self) -> None:
        app_obj, fake_req, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj,
            [
                "consolidate",
                "--workspace-id",
                "ws-1",
                "--target-tier",
                "medium_term",
                "--target-scope",
                "session",
            ],
        )
        assert r.exit_code == 0
        assert "Consolidation complete" in r.output
        assert "2 entries created" in r.output
        assert "3 source entries examined" in r.output
        assert fake_req.calls[0][1] == "POST"


# ---------------------------------------------------------------------------
# evolve command
# ---------------------------------------------------------------------------


class TestEvolveCommand:
    def test_evolve_create_output(self) -> None:
        app_obj, fake_req, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj,
            [
                "evolve",
                "create",
                "--workspace-id",
                "ws-1",
                "--memory-id",
                "mem-001",
                "--skill-id",
                "review-loop-sop",
                "--runtime-name",
                "review-loop-sop",
            ],
        )
        assert r.exit_code == 0
        assert "Created memory evolution draft" in r.output
        _, method, path, body = fake_req.calls[0]
        assert method == "POST"
        assert path == "/api/workspaces/ws-1/memories/evolutions"
        assert isinstance(body, dict)
        assert body["source_memory_ids"] == ["mem-001"]

    def test_evolve_create_json_output(self) -> None:
        app_obj, _, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj,
            [
                "evolve",
                "create",
                "--workspace-id",
                "ws-1",
                "--memory-id",
                "mem-001",
                "--skill-id",
                "review-loop-sop",
                "--runtime-name",
                "review-loop-sop",
                "--format",
                "json",
            ],
        )

        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["draft_id"] == "mem-evo-001"

    def test_evolve_list_table_output_with_filters(self) -> None:
        app_obj, fake_req, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj,
            [
                "evolve",
                "list",
                "--workspace-id",
                "ws-1",
                "--target",
                "sop_skill",
                "--status",
                "draft",
            ],
        )

        assert r.exit_code == 0
        assert "mem-evo-001" in r.output
        _, method, path, body = fake_req.calls[0]
        assert method == "GET"
        assert path.startswith("/api/workspaces/ws-1/memories/evolutions?")
        assert body is None
        assert "target=sop_skill" in path
        assert "status=draft" in path

    def test_evolve_list_json_output(self) -> None:
        app_obj, _, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj,
            ["evolve", "list", "--workspace-id", "ws-1", "--format", "json"],
        )
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["total_count"] == 1

    def test_evolve_apply_output(self) -> None:
        app_obj, fake_req, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj,
            [
                "evolve",
                "apply",
                "--workspace-id",
                "ws-1",
                "--draft-id",
                "mem-evo-001",
            ],
        )
        assert r.exit_code == 0
        assert "Applied memory evolution draft" in r.output
        assert fake_req.calls[0][2].endswith("mem-evo-001:apply")

    def test_evolve_apply_json_output(self) -> None:
        app_obj, _, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj,
            [
                "evolve",
                "apply",
                "--workspace-id",
                "ws-1",
                "--draft-id",
                "mem-evo-001",
                "--format",
                "json",
            ],
        )

        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["status"] == "applied"
        assert data["applied_skill_ref"] == "review-loop-sop"

    def test_evolve_reject_output(self) -> None:
        app_obj, fake_req, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj,
            [
                "evolve",
                "reject",
                "--workspace-id",
                "ws-1",
                "--draft-id",
                "mem-evo-001",
                "--reason",
                "duplicate",
            ],
        )
        assert r.exit_code == 0
        assert "Rejected memory evolution draft" in r.output
        assert fake_req.calls[0][2].endswith("mem-evo-001:reject")

    def test_evolve_reject_json_output(self) -> None:
        app_obj, _, _ = _build_app()
        from typer.testing import CliRunner as _CR

        r = _CR().invoke(
            app_obj,
            [
                "evolve",
                "reject",
                "--workspace-id",
                "ws-1",
                "--draft-id",
                "mem-evo-001",
                "--format",
                "json",
            ],
        )

        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["status"] == "rejected"


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------


class TestRenderMemoriesTable:
    def test_empty_items(self) -> None:
        result = _render_memories_table({"items": [], "total_count": 0})
        assert "No memory entries found" in result

    def test_with_items(self) -> None:
        payload: dict[str, object] = {
            "items": [
                {
                    "id": "mem-001",
                    "tier": "persistent",
                    "kind": "fact",
                    "content_title": "Test fact",
                    "confidence_score": 0.95,
                }
            ],
            "total_count": 1,
            "offset": 0,
        }
        result = _render_memories_table(payload)
        assert "mem-001" in result
        assert "persistent" in result
        assert "Test fact" in result
        assert "Total: 1" in result


class TestRenderEntryDetail:
    def test_full_detail(self) -> None:
        payload: dict[str, object] = {
            "id": "mem-001",
            "tier": "persistent",
            "scope": "workspace",
            "kind": "fact",
            "status": "active",
            "version": 1,
            "confidence_score": 0.95,
            "source": "manual",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "expires_at": None,
            "tags": ["tag1", "tag2"],
            "content": {
                "title": "My title",
                "body": "My body",
            },
        }
        result = _render_entry_detail(payload)
        assert "mem-001" in result
        assert "persistent" in result
        assert "tag1, tag2" in result
        assert "My title" in result
        assert "My body" in result

    def test_detail_with_context_and_outcome(self) -> None:
        payload: dict[str, object] = {
            "id": "mem-002",
            "tier": "working",
            "scope": "session",
            "kind": "insight",
            "status": "active",
            "version": 1,
            "confidence_score": 0.8,
            "source": "task_result",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "expires_at": None,
            "tags": [],
            "content": {
                "title": "Title",
                "body": "Body",
                "context": "Some context",
                "outcome": "Some outcome",
            },
        }
        result = _render_entry_detail(payload)
        assert "Some context" in result
        assert "Some outcome" in result


class TestRenderSearchTable:
    def test_empty_results(self) -> None:
        result = _render_search_table({"items": [], "total_count": 0})
        assert "No results found" in result

    def test_with_results(self) -> None:
        payload: dict[str, object] = {
            "items": [
                {
                    "entry": {
                        "id": "mem-001",
                        "content_title": "Fact about X",
                    },
                    "rank": 1,
                    "score": 0.99,
                    "snippet": "A snippet of text",
                }
            ],
            "total_count": 1,
        }
        result = _render_search_table(payload)
        assert "Fact about X" in result
        assert "A snippet of text" in result
        assert "Found 1 result" in result


class TestRenderEvolutionTable:
    def test_empty_drafts(self) -> None:
        result = _render_evolution_table({"items": [], "total_count": 0})
        assert "No memory evolution drafts found" in result

    def test_with_drafts(self) -> None:
        payload: dict[str, object] = {
            "items": [
                {
                    "draft_id": "mem-evo-001",
                    "status": "draft",
                    "target": "sop_skill",
                    "runtime_name": "review-loop-sop",
                    "skill_id": "review-loop-sop",
                },
                "unexpected",
            ],
            "total_count": 1,
        }
        result = _render_evolution_table(payload)
        assert "Total: 1" in result
        assert "mem-evo-001" in result
        assert "review-loop-sop" in result


class TestRequireObjectResponse:
    def test_accepts_dict(self) -> None:
        result = _require_object_response({"key": "value"}, "/test")
        assert result == {"key": "value"}

    def test_rejects_list(self) -> None:
        with pytest.raises(RuntimeError, match="Expected JSON object"):
            _require_object_response([1, 2, 3], "/test")


class TestMemoriesOutputFormat:
    def test_format_enum_values(self) -> None:
        assert MemoriesOutputFormat.TABLE.value == "table"
        assert MemoriesOutputFormat.JSON.value == "json"
