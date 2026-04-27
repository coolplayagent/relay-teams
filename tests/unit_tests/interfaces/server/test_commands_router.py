# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.commands import CommandRegistry
from relay_teams.interfaces.server.deps import (
    get_command_registry,
    get_workspace_service,
)
from relay_teams.interfaces.server.routers import commands
from relay_teams.workspace import WorkspaceRecord, build_local_workspace_mount


class _FakeWorkspaceService:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.second_workspace_root = workspace_root.parent / "workspace-2"

    def get_workspace(self, workspace_id: str) -> WorkspaceRecord:
        if workspace_id == "workspace-1":
            root = self.workspace_root
        elif workspace_id == "workspace-2":
            root = self.second_workspace_root
        else:
            raise KeyError(workspace_id)
        return WorkspaceRecord(
            workspace_id=workspace_id,
            default_mount_name="default",
            mounts=(
                build_local_workspace_mount(
                    mount_name="default",
                    root_path=root,
                ),
            ),
        )

    def list_workspaces(self) -> tuple[WorkspaceRecord, ...]:
        return (
            self.get_workspace("workspace-1"),
            self.get_workspace("workspace-2"),
        )


def _client(
    *,
    app_config_dir: Path,
    workspace_root: Path,
) -> TestClient:
    app = FastAPI()
    app.include_router(commands.router, prefix="/api")
    app.dependency_overrides[get_command_registry] = lambda: CommandRegistry(
        app_config_dir=app_config_dir
    )
    app.dependency_overrides[get_workspace_service] = lambda: _FakeWorkspaceService(
        workspace_root
    )
    return TestClient(app)


def test_commands_api_list_show_and_resolve(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    command_dir = workspace_root / ".claude" / "commands" / "opsx"
    command_dir.mkdir(parents=True)
    (command_dir / "propose.md").write_text(
        "---\n"
        "description: Propose a change\n"
        "argument-hint: command arguments\n"
        "---\n"
        "Propose $ARGUMENTS",
        encoding="utf-8",
    )
    client = _client(app_config_dir=app_config_dir, workspace_root=workspace_root)

    listed = client.get("/api/system/commands?workspace_id=workspace-1")
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "opsx:propose"

    shown = client.get("/api/system/commands/opsx:propose?workspace_id=workspace-1")
    assert shown.status_code == 200
    assert shown.json()["template"] == "Propose $ARGUMENTS"

    resolved = client.post(
        "/api/system/commands:resolve",
        json={
            "workspace_id": "workspace-1",
            "raw_text": "/opsx:propose add-login",
            "mode": "normal",
        },
    )
    assert resolved.status_code == 200
    payload = resolved.json()
    assert payload["matched"] is True
    assert payload["expanded_prompt"] == "Propose add-login"


def test_commands_api_unknown_and_mode_reject(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    command_dir = workspace_root / ".relay-teams" / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "review.md").write_text(
        "---\nallowed_modes: [normal]\n---\nReview",
        encoding="utf-8",
    )
    client = _client(app_config_dir=app_config_dir, workspace_root=workspace_root)

    missing = client.get("/api/system/commands/missing?workspace_id=workspace-1")
    assert missing.status_code == 404

    passthrough = client.post(
        "/api/system/commands:resolve",
        json={
            "workspace_id": "workspace-1",
            "raw_text": "/missing value",
            "mode": "normal",
        },
    )
    assert passthrough.status_code == 200
    assert passthrough.json()["matched"] is False

    rejected = client.post(
        "/api/system/commands:resolve",
        json={
            "workspace_id": "workspace-1",
            "raw_text": "/review",
            "mode": "orchestration",
        },
    )
    assert rejected.status_code == 400


def test_commands_api_catalog_lists_global_and_workspace_commands(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    app_command_dir = app_config_dir / "commands"
    app_command_dir.mkdir(parents=True)
    (app_command_dir / "global.md").write_text(
        "---\nname: global\n---\nGlobal",
        encoding="utf-8",
    )
    project_command_dir = workspace_root / ".claude" / "commands"
    project_command_dir.mkdir(parents=True)
    (project_command_dir / "review.md").write_text("Review", encoding="utf-8")

    client = _client(app_config_dir=app_config_dir, workspace_root=workspace_root)

    response = client.get("/api/system/commands:catalog")
    assert response.status_code == 200
    payload = response.json()
    assert payload["app_commands"][0]["name"] == "global"
    assert payload["app_commands"][0]["template"] == "Global"
    workspaces = {item["workspace_id"]: item for item in payload["workspaces"]}
    assert workspaces["workspace-1"]["can_create_commands"] is True
    assert workspaces["workspace-1"]["commands"][0]["name"] == "review"
    assert workspaces["workspace-1"]["commands"][0]["template"] == "Review"
    assert workspaces["workspace-2"]["commands"] == []


def test_commands_api_catalog_lists_only_effective_duplicate_commands(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    app_command_dir = app_config_dir / "commands"
    app_command_dir.mkdir(parents=True)
    (app_command_dir / "first.md").write_text(
        "---\nname: global\n---\nFirst global",
        encoding="utf-8",
    )
    (app_command_dir / "second.md").write_text(
        "---\nname: global\n---\nSecond global",
        encoding="utf-8",
    )
    codex_command_dir = workspace_root / ".codex" / "commands"
    relay_command_dir = workspace_root / ".relay-teams" / "commands"
    codex_command_dir.mkdir(parents=True)
    relay_command_dir.mkdir(parents=True)
    (codex_command_dir / "shared.md").write_text(
        "Codex shared",
        encoding="utf-8",
    )
    (relay_command_dir / "shared.md").write_text(
        "Relay Teams shared",
        encoding="utf-8",
    )
    client = _client(app_config_dir=app_config_dir, workspace_root=workspace_root)

    response = client.get("/api/system/commands:catalog")

    assert response.status_code == 200
    payload = response.json()
    assert [
        (command["name"], command["template"]) for command in payload["app_commands"]
    ] == [("global", "Second global")]
    workspaces = {item["workspace_id"]: item for item in payload["workspaces"]}
    assert [
        (command["name"], command["template"])
        for command in workspaces["workspace-1"]["commands"]
    ] == [("shared", "Relay Teams shared")]


def test_commands_api_create_global_and_project_commands(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    client = _client(app_config_dir=app_config_dir, workspace_root=workspace_root)

    global_response = client.post(
        "/api/system/commands",
        json={
            "scope": "global",
            "relative_path": "ops/global.md",
            "name": "ops:global",
            "description": "Global command",
            "argument_hint": "<arg>",
            "allowed_modes": ["normal"],
            "template": "Global $ARGUMENTS",
        },
    )
    assert global_response.status_code == 200
    assert (app_config_dir / "commands" / "ops" / "global.md").exists()
    assert global_response.json()["command"]["name"] == "ops:global"

    project_response = client.post(
        "/api/system/commands",
        json={
            "scope": "project",
            "workspace_id": "workspace-1",
            "source": "claude",
            "relative_path": "ops/propose.md",
            "name": "opsx:propose",
            "aliases": ["opsx/propose"],
            "description": "Project command",
            "argument_hint": "<change-id>",
            "allowed_modes": ["normal"],
            "template": "Propose {{args}}",
        },
    )
    assert project_response.status_code == 200
    assert (workspace_root / ".claude" / "commands" / "ops" / "propose.md").exists()
    assert project_response.json()["command"]["aliases"] == ["opsx/propose"]

    listed = client.get("/api/system/commands?workspace_id=workspace-1")
    assert listed.status_code == 200
    names = {item["name"] for item in listed.json()}
    assert names == {"ops:global", "opsx:propose"}


def test_commands_api_create_rejects_invalid_path_duplicate_and_workspace(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    client = _client(app_config_dir=app_config_dir, workspace_root=workspace_root)

    invalid_path = client.post(
        "/api/system/commands",
        json={
            "scope": "global",
            "relative_path": "../bad.md",
            "name": "bad",
            "template": "Bad",
        },
    )
    assert invalid_path.status_code == 400

    unknown_workspace = client.post(
        "/api/system/commands",
        json={
            "scope": "project",
            "workspace_id": "missing",
            "source": "claude",
            "relative_path": "bad.md",
            "name": "bad",
            "template": "Bad",
        },
    )
    assert unknown_workspace.status_code == 404

    created = client.post(
        "/api/system/commands",
        json={
            "scope": "global",
            "relative_path": "dup.md",
            "name": "dup",
            "template": "First",
        },
    )
    assert created.status_code == 200
    duplicate = client.post(
        "/api/system/commands",
        json={
            "scope": "global",
            "relative_path": "dup.md",
            "name": "dup2",
            "template": "Second",
        },
    )
    assert duplicate.status_code == 409


def test_commands_api_update_global_and_project_commands(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    app_command_dir = app_config_dir / "commands"
    app_command_dir.mkdir(parents=True)
    app_command_path = app_command_dir / "global.md"
    app_command_path.write_text("Global", encoding="utf-8")
    project_command_dir = workspace_root / ".relay-teams" / "commands"
    project_command_dir.mkdir(parents=True)
    project_command_path = project_command_dir / "review.md"
    project_command_path.write_text("Review", encoding="utf-8")
    client = _client(app_config_dir=app_config_dir, workspace_root=workspace_root)

    global_response = client.put(
        "/api/system/commands",
        json={
            "source_path": str(app_command_path),
            "name": "global:updated",
            "aliases": ["global/updated"],
            "description": "Updated global command",
            "argument_hint": "<topic>",
            "allowed_modes": ["normal", "orchestration"],
            "template": "Updated global {{args}}",
        },
    )
    assert global_response.status_code == 200
    global_payload = global_response.json()
    assert global_payload["workspace_id"] is None
    assert global_payload["command"]["name"] == "global:updated"
    assert global_payload["command"]["aliases"] == ["global/updated"]
    assert "Updated global {{args}}" in app_command_path.read_text(encoding="utf-8")
    assert "aliases:" in app_command_path.read_text(encoding="utf-8")

    project_response = client.put(
        "/api/system/commands",
        json={
            "source_path": str(project_command_path),
            "name": "review",
            "aliases": ["review/change"],
            "description": "Updated project command",
            "argument_hint": "<change>",
            "allowed_modes": ["normal"],
            "template": "Updated project {{args}}",
        },
    )
    assert project_response.status_code == 200
    project_payload = project_response.json()
    assert project_payload["workspace_id"] == "workspace-1"
    assert project_payload["command"]["aliases"] == ["review/change"]
    assert project_payload["command"]["template"] == "Updated project {{args}}"

    shown = client.get("/api/system/commands/review?workspace_id=workspace-1")
    assert shown.status_code == 200
    assert shown.json()["description"] == "Updated project command"


def test_commands_api_update_rejects_missing_and_unsupported_paths(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    client = _client(app_config_dir=app_config_dir, workspace_root=workspace_root)

    missing = client.put(
        "/api/system/commands",
        json={
            "source_path": str(app_config_dir / "commands" / "missing.md"),
            "name": "missing",
            "template": "Missing",
        },
    )
    assert missing.status_code == 404

    outside_path = workspace_root / "notes.md"
    outside_path.parent.mkdir(parents=True, exist_ok=True)
    outside_path.write_text("Notes", encoding="utf-8")
    outside = client.put(
        "/api/system/commands",
        json={
            "source_path": str(outside_path),
            "name": "notes",
            "template": "Notes",
        },
    )
    assert outside.status_code == 400

    text_path = app_config_dir / "commands" / "bad.txt"
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text("Bad", encoding="utf-8")
    bad_suffix = client.put(
        "/api/system/commands",
        json={
            "source_path": str(text_path),
            "name": "bad",
            "template": "Bad",
        },
    )
    assert bad_suffix.status_code == 400
