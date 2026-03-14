# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.mcp.registry import McpRegistry
from agent_teams.roles import (
    RoleDocumentDraft,
    RoleRegistry,
    default_memory_profile,
)
from agent_teams.roles.settings_service import RoleSettingsService
from agent_teams.skills.registry import SkillRegistry
from agent_teams.tools.registry import build_default_registry


def test_save_role_document_renames_role_file_and_reloads_registry(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    _write_role(
        roles_dir / "writer.md",
        role_id="writer",
        name="Writer",
        description="Drafts user-facing content.",
        version="1.0.0",
        tools=("dispatch_task",),
        system_prompt="Write clearly.",
    )
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    captured_registry: list[RoleRegistry] = []
    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=_create_builtin_roles_dir(tmp_path),
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: SkillRegistry.from_skill_dirs(
            app_skills_dir=skills_dir
        ),
        on_roles_reloaded=lambda registry: captured_registry.append(registry),
    )

    saved = service.save_role_document(
        "writer_v2",
        draft=RoleDocumentDraft(
            source_role_id="writer",
            role_id="writer_v2",
            name="Writer V2",
            description="Drafts user-facing content with more detail.",
            version="2.0.0",
            tools=("dispatch_task",),
            mcp_servers=(),
            skills=(),
            model_profile="default",
            memory_profile=default_memory_profile(),
            system_prompt="Write with more detail.",
        ),
    )

    assert saved.role_id == "writer_v2"
    assert saved.file_name == "writer_v2.md"
    assert not (roles_dir / "writer.md").exists()
    assert (roles_dir / "writer_v2.md").exists()
    assert captured_registry[-1].get("writer_v2").name == "Writer V2"


def test_validate_role_document_rejects_unknown_tools(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=_create_builtin_roles_dir(tmp_path),
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: SkillRegistry.from_skill_dirs(
            app_skills_dir=skills_dir
        ),
        on_roles_reloaded=lambda registry: None,
    )

    try:
        service.validate_role_document(
            RoleDocumentDraft(
                role_id="broken",
                name="Broken",
                description="Broken role.",
                version="1.0.0",
                tools=("missing_tool",),
                mcp_servers=(),
                skills=(),
                model_profile="default",
                memory_profile=default_memory_profile(),
                system_prompt="This should fail.",
            )
        )
    except ValueError as exc:
        assert "Unknown tools" in str(exc)
    else:
        raise AssertionError("Expected unknown tool validation to fail")


def test_get_role_document_returns_rendered_markdown_content(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    path = roles_dir / "reviewer.md"
    _write_role(
        path,
        role_id="reviewer",
        name="Reviewer",
        description="Reviews delivered work.",
        version="1.1.0",
        tools=("dispatch_task",),
        system_prompt="Review carefully.",
    )
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=_create_builtin_roles_dir(tmp_path),
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: SkillRegistry.from_skill_dirs(
            app_skills_dir=skills_dir
        ),
        on_roles_reloaded=lambda registry: None,
    )

    record = service.get_role_document("reviewer")

    assert record.file_name == "reviewer.md"
    assert record.role_id == "reviewer"
    assert "role_id: reviewer" in record.content
    assert "Review carefully." in record.content


def test_save_role_document_creates_new_role_file(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    captured_registry: list[RoleRegistry] = []
    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=_create_builtin_roles_dir(tmp_path),
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: SkillRegistry.from_skill_dirs(
            app_skills_dir=skills_dir
        ),
        on_roles_reloaded=lambda registry: captured_registry.append(registry),
    )

    saved = service.save_role_document(
        "new_role",
        draft=RoleDocumentDraft(
            role_id="new_role",
            name="New Role",
            description="Starts from a blank role.",
            version="1.0.0",
            tools=("dispatch_task",),
            mcp_servers=(),
            skills=(),
            model_profile="default",
            memory_profile=default_memory_profile(),
            system_prompt="Start from a blank role.",
        ),
    )

    assert saved.role_id == "new_role"
    assert saved.file_name == "new_role.md"
    assert (roles_dir / "new_role.md").exists()
    assert captured_registry[-1].get("new_role").name == "New Role"


def _write_role(
    path: Path,
    *,
    role_id: str,
    name: str,
    description: str,
    version: str,
    tools: tuple[str, ...],
    system_prompt: str,
) -> None:
    path.write_text(
        "---\n"
        f"role_id: {role_id}\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "model_profile: default\n"
        f"version: {version}\n"
        "tools:\n"
        + "".join(f"  - {tool}\n" for tool in tools)
        + "---\n\n"
        + system_prompt
        + "\n",
        encoding="utf-8",
    )


def _create_builtin_roles_dir(tmp_path: Path) -> Path:
    builtin_roles_dir = tmp_path / "builtin_roles"
    builtin_roles_dir.mkdir()
    return builtin_roles_dir
