# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
import json
from urllib.parse import urlencode

import typer

type RequestJsonCallable = Callable[
    [str, str, str, dict[str, object] | None], dict[str, object] | list[object]
]
type AutoStartCallable = Callable[[str, bool, bool, bool], None]


class MemoriesOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


def build_memories_app(
    *,
    request_json: RequestJsonCallable,
    auto_start_if_needed: AutoStartCallable,
    default_base_url: str,
) -> typer.Typer:
    memories_app = typer.Typer(
        no_args_is_help=True,
        pretty_exceptions_enable=False,
        help="Memory Bank commands.",
    )
    skill_drafts_app = typer.Typer(
        no_args_is_help=True,
        pretty_exceptions_enable=False,
        help="Memory-derived skill draft commands.",
    )

    @memories_app.command("list")
    def list_memories(
        workspace_id: str = typer.Option(..., "--workspace-id"),
        tier: str | None = typer.Option(None, "--tier"),
        scope: str | None = typer.Option(None, "--scope"),
        role_id: str | None = typer.Option(None, "--role-id"),
        output_format: MemoriesOutputFormat = typer.Option(
            MemoriesOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        daemon: bool = typer.Option(
            False,
            "--daemon",
            "-d",
            help="Run the server as a background process when autostarting.",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            help="Force kill any existing server process before autostarting.",
        ),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        params: dict[str, object] = {}
        if tier is not None:
            params["tier"] = tier
        if scope is not None:
            params["scope"] = scope
        if role_id is not None:
            params["role_id"] = role_id
        path = _path_with_query(f"/api/workspaces/{workspace_id}/memories", params)
        payload = request_json(
            base_url,
            "GET",
            path,
            None,
        )
        response = _require_object_response(
            payload, f"/api/workspaces/{workspace_id}/memories"
        )
        if output_format == MemoriesOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(_render_memories_table(response))

    @memories_app.command("get")
    def get_memory(
        workspace_id: str = typer.Option(..., "--workspace-id"),
        memory_id: str = typer.Option(..., "--memory-id"),
        output_format: MemoriesOutputFormat = typer.Option(
            MemoriesOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        daemon: bool = typer.Option(
            False,
            "--daemon",
            "-d",
            help="Run the server as a background process when autostarting.",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            help="Force kill any existing server process before autostarting.",
        ),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        payload = request_json(
            base_url,
            "GET",
            f"/api/workspaces/{workspace_id}/memories/{memory_id}",
            None,
        )
        response = _require_object_response(
            payload, f"/api/workspaces/{workspace_id}/memories/{memory_id}"
        )
        if output_format == MemoriesOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(_render_entry_detail(response))

    @memories_app.command("create")
    def create_memory(
        workspace_id: str = typer.Option(..., "--workspace-id"),
        content: str = typer.Option(..., "--content"),
        title: str = typer.Option("", "--title"),
        tier: str = typer.Option("persistent", "--tier"),
        scope: str = typer.Option("workspace", "--scope"),
        kind: str = typer.Option("fact", "--kind"),
        tags: str | None = typer.Option(None, "--tags"),
        output_format: MemoriesOutputFormat = typer.Option(
            MemoriesOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        daemon: bool = typer.Option(
            False,
            "--daemon",
            "-d",
            help="Run the server as a background process when autostarting.",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            help="Force kill any existing server process before autostarting.",
        ),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        body: dict[str, object] = {
            "content": {"title": title or content[:80], "body": content},
            "tier": tier,
            "scope": scope,
            "kind": kind,
        }
        if tags is not None and tags.strip():
            body["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
        payload = request_json(
            base_url,
            "POST",
            f"/api/workspaces/{workspace_id}/memories",
            body,
        )
        response = _require_object_response(
            payload, f"/api/workspaces/{workspace_id}/memories"
        )
        if output_format == MemoriesOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(f"Created memory entry: {response.get('id', '(unknown)')}")

    @memories_app.command("delete")
    def delete_memory(
        workspace_id: str = typer.Option(..., "--workspace-id"),
        memory_id: str = typer.Option(..., "--memory-id"),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        daemon: bool = typer.Option(
            False,
            "--daemon",
            "-d",
            help="Run the server as a background process when autostarting.",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            help="Force kill any existing server process before autostarting.",
        ),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        request_json(
            base_url,
            "DELETE",
            f"/api/workspaces/{workspace_id}/memories/{memory_id}",
            None,
        )
        typer.echo(f"Deleted memory entry: {memory_id}")

    @memories_app.command("search")
    def search_memories(
        workspace_id: str = typer.Option(..., "--workspace-id"),
        query: str = typer.Option(..., "--query"),
        output_format: MemoriesOutputFormat = typer.Option(
            MemoriesOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        daemon: bool = typer.Option(
            False,
            "--daemon",
            "-d",
            help="Run the server as a background process when autostarting.",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            help="Force kill any existing server process before autostarting.",
        ),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        payload = request_json(
            base_url,
            "POST",
            f"/api/workspaces/{workspace_id}/memories/search",
            {"text_query": query},
        )
        response = _require_object_response(
            payload, f"/api/workspaces/{workspace_id}/memories/search"
        )
        if output_format == MemoriesOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(_render_search_table(response))

    @memories_app.command("consolidate")
    def consolidate_memories(
        workspace_id: str = typer.Option(..., "--workspace-id"),
        target_tier: str = typer.Option("medium_term", "--target-tier"),
        target_scope: str = typer.Option("workspace", "--target-scope"),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        daemon: bool = typer.Option(
            False,
            "--daemon",
            "-d",
            help="Run the server as a background process when autostarting.",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            help="Force kill any existing server process before autostarting.",
        ),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        payload = request_json(
            base_url,
            "POST",
            f"/api/workspaces/{workspace_id}/memories/consolidate",
            {
                "target_tier": target_tier,
                "target_scope": target_scope,
            },
        )
        response = _require_object_response(
            payload, f"/api/workspaces/{workspace_id}/memories/consolidate"
        )
        typer.echo(
            f"Consolidation complete: "
            f"{response.get('consolidated_entry_count', 0)} entries created, "
            f"{response.get('source_entry_count', 0)} source entries examined"
        )

    evolve_app = typer.Typer(
        no_args_is_help=True,
        pretty_exceptions_enable=False,
        help="Promote Memory Bank entries into reviewable capability drafts.",
    )

    @evolve_app.command("create")
    def create_evolution_draft(
        workspace_id: str = typer.Option(..., "--workspace-id"),
        memory_ids: list[str] = typer.Option(..., "--memory-id"),
        target: str = typer.Option("sop_skill", "--target"),
        skill_id: str = typer.Option(..., "--skill-id"),
        runtime_name: str = typer.Option(..., "--runtime-name"),
        description: str = typer.Option("", "--description"),
        objective: str = typer.Option("", "--objective"),
        output_format: MemoriesOutputFormat = typer.Option(
            MemoriesOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        daemon: bool = typer.Option(False, "--daemon", "-d"),
        force: bool = typer.Option(False, "--force"),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        body: dict[str, object] = {
            "workspace_id": workspace_id,
            "source_memory_ids": memory_ids,
            "target": target,
            "skill_id": skill_id,
            "runtime_name": runtime_name,
            "description": description,
            "objective": objective,
        }
        payload = request_json(
            base_url,
            "POST",
            f"/api/workspaces/{workspace_id}/memories/evolutions",
            body,
        )
        response = _require_object_response(
            payload, f"/api/workspaces/{workspace_id}/memories/evolutions"
        )
        if output_format == MemoriesOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(f"Created memory evolution draft: {response.get('draft_id', '-')}")

    @evolve_app.command("list")
    def list_evolution_drafts(
        workspace_id: str = typer.Option(..., "--workspace-id"),
        target: str | None = typer.Option(None, "--target"),
        status: str | None = typer.Option(None, "--status"),
        output_format: MemoriesOutputFormat = typer.Option(
            MemoriesOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        daemon: bool = typer.Option(False, "--daemon", "-d"),
        force: bool = typer.Option(False, "--force"),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        params: dict[str, object] = {}
        if target is not None:
            params["target"] = target
        if status is not None:
            params["status"] = status
        path = _path_with_query(
            f"/api/workspaces/{workspace_id}/memories/evolutions",
            params,
        )
        payload = request_json(base_url, "GET", path, None)
        response = _require_object_response(
            payload, f"/api/workspaces/{workspace_id}/memories/evolutions"
        )
        if output_format == MemoriesOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(_render_evolution_table(response))

    @evolve_app.command("apply")
    def apply_evolution_draft(
        workspace_id: str = typer.Option(..., "--workspace-id"),
        draft_id: str = typer.Option(..., "--draft-id"),
        output_format: MemoriesOutputFormat = typer.Option(
            MemoriesOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        daemon: bool = typer.Option(False, "--daemon", "-d"),
        force: bool = typer.Option(False, "--force"),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        payload = request_json(
            base_url,
            "POST",
            f"/api/workspaces/{workspace_id}/memories/evolutions/{draft_id}:apply",
            {},
        )
        response = _require_object_response(
            payload,
            f"/api/workspaces/{workspace_id}/memories/evolutions/{draft_id}:apply",
        )
        if output_format == MemoriesOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(
            "Applied memory evolution draft: "
            f"{response.get('draft_id', '-')} -> {response.get('applied_skill_ref', '-')}"
        )

    @evolve_app.command("reject")
    def reject_evolution_draft(
        workspace_id: str = typer.Option(..., "--workspace-id"),
        draft_id: str = typer.Option(..., "--draft-id"),
        reason: str = typer.Option("", "--reason"),
        output_format: MemoriesOutputFormat = typer.Option(
            MemoriesOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        daemon: bool = typer.Option(False, "--daemon", "-d"),
        force: bool = typer.Option(False, "--force"),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        payload = request_json(
            base_url,
            "POST",
            f"/api/workspaces/{workspace_id}/memories/evolutions/{draft_id}:reject",
            {"reason": reason},
        )
        response = _require_object_response(
            payload,
            f"/api/workspaces/{workspace_id}/memories/evolutions/{draft_id}:reject",
        )
        if output_format == MemoriesOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(f"Rejected memory evolution draft: {response.get('draft_id', '-')}")

    @skill_drafts_app.command("generate")
    def generate_skill_drafts(
        workspace_id: str | None = typer.Option(None, "--workspace-id"),
        cross_workspace: bool = typer.Option(
            False,
            "--cross-workspace/--workspace",
            help="Generate from cross-workspace memory instead of one workspace.",
        ),
        kind: str = typer.Option("auto", "--kind"),
        query: str = typer.Option("", "--query"),
        output_format: MemoriesOutputFormat = typer.Option(
            MemoriesOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        daemon: bool = typer.Option(False, "--daemon", "-d"),
        force: bool = typer.Option(False, "--force"),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        body: dict[str, object] = {
            "scope_kind": "cross_workspace" if cross_workspace else "workspace",
            "draft_kind": kind,
        }
        if workspace_id is not None and workspace_id.strip():
            if cross_workspace:
                body["workspace_ids"] = [workspace_id.strip()]
            else:
                body["workspace_id"] = workspace_id.strip()
        if query.strip():
            body["text_query"] = query.strip()
        payload = request_json(
            base_url,
            "POST",
            "/api/memories/skill-drafts:generate",
            body,
        )
        response = _require_object_response(
            payload, "/api/memories/skill-drafts:generate"
        )
        if output_format == MemoriesOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(_render_skill_drafts_table(response))

    @skill_drafts_app.command("list")
    def list_skill_drafts(
        workspace_id: str | None = typer.Option(None, "--workspace-id"),
        status: str | None = typer.Option(None, "--status"),
        output_format: MemoriesOutputFormat = typer.Option(
            MemoriesOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        daemon: bool = typer.Option(False, "--daemon", "-d"),
        force: bool = typer.Option(False, "--force"),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        params: dict[str, object] = {}
        if workspace_id is not None and workspace_id.strip():
            params["workspace_id"] = workspace_id.strip()
        if status is not None and status.strip():
            params["status"] = status.strip()
        path = _path_with_query("/api/memories/skill-drafts", params)
        payload = request_json(
            base_url,
            "GET",
            path,
            None,
        )
        response = _require_object_response(payload, "/api/memories/skill-drafts")
        if output_format == MemoriesOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(_render_skill_drafts_table(response))

    @skill_drafts_app.command("get")
    def get_skill_draft(
        draft_id: str = typer.Option(..., "--draft-id"),
        output_format: MemoriesOutputFormat = typer.Option(
            MemoriesOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        daemon: bool = typer.Option(False, "--daemon", "-d"),
        force: bool = typer.Option(False, "--force"),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        payload = request_json(
            base_url,
            "GET",
            f"/api/memories/skill-drafts/{draft_id}",
            None,
        )
        response = _require_object_response(
            payload, f"/api/memories/skill-drafts/{draft_id}"
        )
        if output_format == MemoriesOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(_render_skill_draft_detail(response))

    @skill_drafts_app.command("update")
    def update_skill_draft(
        draft_id: str = typer.Option(..., "--draft-id"),
        runtime_name: str | None = typer.Option(None, "--runtime-name"),
        description: str | None = typer.Option(None, "--description"),
        instructions: str | None = typer.Option(None, "--instructions"),
        status: str | None = typer.Option(None, "--status"),
        output_format: MemoriesOutputFormat = typer.Option(
            MemoriesOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        daemon: bool = typer.Option(False, "--daemon", "-d"),
        force: bool = typer.Option(False, "--force"),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        body: dict[str, object] = {}
        if runtime_name is not None:
            body["runtime_name"] = runtime_name
        if description is not None:
            body["description"] = description
        if instructions is not None:
            body["instructions"] = instructions
        if status is not None:
            body["status"] = status
        payload = request_json(
            base_url,
            "PUT",
            f"/api/memories/skill-drafts/{draft_id}",
            body,
        )
        response = _require_object_response(
            payload, f"/api/memories/skill-drafts/{draft_id}"
        )
        if output_format == MemoriesOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(_render_skill_draft_detail(response))

    @skill_drafts_app.command("validate")
    def validate_skill_draft(
        draft_id: str = typer.Option(..., "--draft-id"),
        output_format: MemoriesOutputFormat = typer.Option(
            MemoriesOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        daemon: bool = typer.Option(False, "--daemon", "-d"),
        force: bool = typer.Option(False, "--force"),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        payload = request_json(
            base_url,
            "POST",
            f"/api/memories/skill-drafts/{draft_id}:validate",
            None,
        )
        response = _require_object_response(
            payload, f"/api/memories/skill-drafts/{draft_id}:validate"
        )
        if output_format == MemoriesOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(_render_skill_draft_detail(response))

    @skill_drafts_app.command("apply")
    def apply_skill_draft(
        draft_id: str = typer.Option(..., "--draft-id"),
        output_format: MemoriesOutputFormat = typer.Option(
            MemoriesOutputFormat.TABLE,
            "--format",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
        daemon: bool = typer.Option(False, "--daemon", "-d"),
        force: bool = typer.Option(False, "--force"),
    ) -> None:
        auto_start_if_needed(base_url, autostart, daemon, force)
        payload = request_json(
            base_url,
            "POST",
            f"/api/memories/skill-drafts/{draft_id}:apply",
            None,
        )
        response = _require_object_response(
            payload, f"/api/memories/skill-drafts/{draft_id}:apply"
        )
        if output_format == MemoriesOutputFormat.JSON:
            typer.echo(json.dumps(response, ensure_ascii=False))
            return
        typer.echo(
            f"Applied skill draft: {response.get('ref', response.get('skill_id', '-'))}"
        )

    memories_app.add_typer(evolve_app, name="evolve")
    memories_app.add_typer(skill_drafts_app, name="skill-drafts")

    return memories_app


def _render_memories_table(payload: dict[str, object]) -> str:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return "No memory entries found."

    raw_total = payload.get("total_count", len(items))
    total_count = (
        int(str(raw_total)) if isinstance(raw_total, (int, float)) else len(items)
    )
    raw_offset = payload.get("offset", 0)
    offset_val = int(str(raw_offset)) if isinstance(raw_offset, (int, float)) else 0
    lines = [
        f"Total: {total_count}  (showing {offset_val + 1}-{offset_val + len(items)})",
        "",
        "ID".ljust(30)
        + "Tier".ljust(14)
        + "Kind".ljust(14)
        + "Title".ljust(40)
        + "Score",
        "-" * 100,
    ]
    for item in items:
        if not isinstance(item, dict):
            continue
        entry_id = str(item.get("id", ""))[:28]
        tier = str(item.get("tier", ""))[:12]
        kind = str(item.get("kind", ""))[:12]
        title = str(item.get("content_title", ""))[:38]
        score = f"{item.get('confidence_score', 0.0):.2f}"
        lines.append(
            entry_id.ljust(30)
            + tier.ljust(14)
            + kind.ljust(14)
            + title.ljust(40)
            + score
        )
    return "\n".join(lines)


def _path_with_query(path: str, params: dict[str, object]) -> str:
    if not params:
        return path
    return f"{path}?{urlencode(params)}"


def _render_entry_detail(payload: dict[str, object]) -> str:
    content = payload.get("content")
    lines = [
        f"ID            : {payload.get('id', '-')}",
        f"Tier          : {payload.get('tier', '-')}",
        f"Scope         : {payload.get('scope', '-')}",
        f"Kind          : {payload.get('kind', '-')}",
        f"Status        : {payload.get('status', '-')}",
        f"Version       : {payload.get('version', '-')}",
        f"Score         : {payload.get('confidence_score', '-')}",
        f"Source        : {payload.get('source', '-')}",
        f"Created       : {payload.get('created_at', '-')}",
        f"Updated       : {payload.get('updated_at', '-')}",
        f"Expires       : {payload.get('expires_at', '-')}",
    ]
    tags = payload.get("tags")
    if isinstance(tags, (list, tuple)):
        lines.append(f"Tags          : {', '.join(str(t) for t in tags)}")
    if isinstance(content, dict):
        lines.append(f"Title         : {content.get('title', '-')}")
        lines.append(f"Body          : {content.get('body', '-')}")
        if content.get("context"):
            lines.append(f"Context       : {content['context']}")
        if content.get("outcome"):
            lines.append(f"Outcome       : {content['outcome']}")
    return "\n".join(lines)


def _render_search_table(payload: dict[str, object]) -> str:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return "No results found."

    total = payload.get("total_count", len(items))
    lines = [
        f"Found {total} result(s)",
        "",
        "Rank  Score    Title",
        "-" * 80,
    ]
    for item in items:
        if not isinstance(item, dict):
            continue
        entry = item.get("entry")
        if not isinstance(entry, dict):
            continue
        rank = str(item.get("rank", ""))
        score = f"{item.get('score', 0.0):.4f}"
        title = str(entry.get("content_title", ""))[:60]
        lines.append(f"{rank:<6}{score:<9}{title}")
        snippet = item.get("snippet")
        if isinstance(snippet, str) and snippet:
            lines.append(f"       {snippet[:100]}")
    return "\n".join(lines)


def _render_evolution_table(payload: dict[str, object]) -> str:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return "No memory evolution drafts found."

    raw_total = payload.get("total_count", len(items))
    total_count = (
        int(str(raw_total)) if isinstance(raw_total, (int, float)) else len(items)
    )
    lines = [
        f"Total: {total_count}",
        "",
        "Draft ID".ljust(34)
        + "Status".ljust(12)
        + "Target".ljust(12)
        + "Runtime".ljust(26)
        + "Skill",
        "-" * 100,
    ]
    for item in items:
        if not isinstance(item, dict):
            continue
        draft_id = str(item.get("draft_id", ""))[:32]
        status = str(item.get("status", ""))[:10]
        target = str(item.get("target", ""))[:10]
        runtime_name = str(item.get("runtime_name", ""))[:24]
        skill_id = str(item.get("skill_id", ""))[:24]
        lines.append(
            draft_id.ljust(34)
            + status.ljust(12)
            + target.ljust(12)
            + runtime_name.ljust(26)
            + skill_id
        )
    return "\n".join(lines)


def _render_skill_drafts_table(payload: dict[str, object]) -> str:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        error_message = payload.get("error_message")
        if isinstance(error_message, str) and error_message:
            return f"No skill drafts. {error_message}"
        return "No skill drafts found."
    lines = [
        "ID".ljust(30)
        + "Status".ljust(13)
        + "Kind".ljust(12)
        + "Name".ljust(34)
        + "Sources",
        "-" * 100,
    ]
    for item in items:
        if not isinstance(item, dict):
            continue
        lines.append(
            str(item.get("id", ""))[:28].ljust(30)
            + str(item.get("status", ""))[:11].ljust(13)
            + str(item.get("draft_kind", ""))[:10].ljust(12)
            + str(item.get("runtime_name", ""))[:32].ljust(34)
            + str(item.get("source_memory_count", 0))
        )
    return "\n".join(lines)


def _render_skill_draft_detail(payload: dict[str, object]) -> str:
    lines = [
        f"ID            : {payload.get('id', '-')}",
        f"Status        : {payload.get('status', '-')}",
        f"Kind          : {payload.get('draft_kind', '-')}",
        f"Runtime Name  : {payload.get('runtime_name', '-')}",
        f"Description   : {payload.get('description', '-')}",
        f"Applied Ref   : {payload.get('applied_ref', '-')}",
        f"Updated       : {payload.get('updated_at', '-')}",
        "",
        "Instructions:",
        str(payload.get("instructions", "")),
    ]
    messages = payload.get("validation_messages")
    if isinstance(messages, list) and messages:
        lines.extend(("", "Validation:"))
        for message in messages:
            if not isinstance(message, dict):
                continue
            lines.append(
                f"- {message.get('severity', '-')}: "
                f"{message.get('code', '-')} - {message.get('message', '-')}"
            )
    return "\n".join(lines)


def _require_object_response(
    payload: dict[str, object] | list[object],
    path: str,
) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    raise RuntimeError(f"Expected JSON object from {path}")
