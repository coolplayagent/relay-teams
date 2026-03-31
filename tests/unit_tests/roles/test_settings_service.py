# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.computer import ExecutionSurface
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.roles import (
    RoleDocumentDraft,
    RoleConfigSource,
    RoleRegistry,
    default_memory_profile,
)
from agent_teams.roles.settings_service import RoleSettingsService
from agent_teams.skills.skill_registry import SkillRegistry
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
        get_external_agent_service=None,
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
            execution_surface=ExecutionSurface.HYBRID,
            memory_profile=default_memory_profile(),
            system_prompt="Write with more detail.",
        ),
    )

    assert saved.role_id == "writer_v2"
    assert saved.file_name == "writer_v2.md"
    assert saved.execution_surface == ExecutionSurface.HYBRID
    assert not (roles_dir / "writer.md").exists()
    assert (roles_dir / "writer_v2.md").exists()
    assert "execution_surface: hybrid" in saved.content
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
        get_external_agent_service=None,
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
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
    )

    record = service.get_role_document("reviewer")

    assert record.file_name == "reviewer.md"
    assert record.role_id == "reviewer"
    assert record.execution_surface == ExecutionSurface.API
    assert "role_id: reviewer" in record.content
    assert "Review carefully." in record.content


def test_get_role_document_canonicalizes_unique_skill_names(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    _write_role(
        roles_dir / "reviewer.md",
        role_id="reviewer",
        name="Reviewer",
        description="Reviews delivered work.",
        version="1.1.0",
        tools=("dispatch_task",),
        skills=("time",),
        system_prompt="Review carefully.",
    )
    app_skills_dir = tmp_path / "skills"
    app_skills_dir.mkdir()
    (app_skills_dir / "time").mkdir()
    (app_skills_dir / "time" / "SKILL.md").write_text(
        "---\nname: time\ndescription: timezone helper\n---\nUse UTC.\n",
        encoding="utf-8",
    )
    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=_create_builtin_roles_dir(tmp_path),
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: SkillRegistry.from_skill_dirs(
            app_skills_dir=app_skills_dir
        ),
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
    )

    record = service.get_role_document("reviewer")

    assert record.skills == ("app:time",)


def test_get_role_document_canonicalizes_legacy_tool_names(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    _write_role(
        roles_dir / "legacy.md",
        role_id="legacy",
        name="Legacy",
        description="Uses historical tool names.",
        version="1.0.0",
        tools=("write_tmp", "shell", "missing_tool"),
        system_prompt="Keep working.",
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
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
    )

    record = service.get_role_document("legacy")

    assert record.tools == ("write", "shell", "missing_tool")


def test_list_role_documents_tolerates_unknown_capabilities_in_persisted_roles(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    _write_role(
        roles_dir / "dirty.md",
        role_id="dirty",
        name="Dirty",
        description="Contains stale capability references.",
        version="1.0.0",
        tools=("missing_tool",),
        mcp_servers=("missing_mcp",),
        skills=("missing_skill",),
        system_prompt="Continue despite stale capabilities.",
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
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
    )

    summaries = service.list_role_documents()

    assert len(summaries) == 1
    assert summaries[0].role_id == "dirty"
    assert summaries[0].deletable is True


def test_list_role_documents_returns_empty_tuple_when_no_role_files_exist(
    tmp_path: Path,
) -> None:
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
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
    )

    assert service.list_role_documents() == ()


def test_list_role_documents_returns_app_roles_when_builtin_roles_are_missing(
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
    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=_create_builtin_roles_dir(tmp_path),
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: SkillRegistry.from_skill_dirs(
            app_skills_dir=skills_dir
        ),
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
    )

    summaries = service.list_role_documents()

    assert len(summaries) == 1
    assert summaries[0].role_id == "writer"
    assert summaries[0].source == RoleConfigSource.APP


def test_list_role_documents_marks_builtin_override_not_deletable(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    builtin_roles_dir = _create_builtin_roles_dir(tmp_path)
    _write_role(
        builtin_roles_dir / "Crafter.md",
        role_id="Crafter",
        name="Crafter",
        description="Builtin crafter role.",
        version="1.0.0",
        tools=("dispatch_task",),
        system_prompt="Craft changes.",
    )
    _write_role(
        roles_dir / "Crafter.md",
        role_id="Crafter",
        name="Crafter",
        description="App override for builtin crafter.",
        version="1.1.0",
        tools=("dispatch_task",),
        system_prompt="Craft app changes.",
    )
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=builtin_roles_dir,
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: SkillRegistry.from_skill_dirs(
            app_skills_dir=skills_dir
        ),
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
    )

    summaries = service.list_role_documents()

    assert len(summaries) == 1
    assert summaries[0].role_id == "Crafter"
    assert summaries[0].source.value == "app"
    assert summaries[0].deletable is False


def test_save_role_document_filters_unknown_capabilities_from_other_roles(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    _write_role(
        roles_dir / "dirty.md",
        role_id="dirty",
        name="Dirty",
        description="Contains stale capability references.",
        version="1.0.0",
        tools=("missing_tool",),
        mcp_servers=("missing_mcp",),
        skills=("missing_skill",),
        system_prompt="Continue despite stale capabilities.",
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
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: captured_registry.append(registry),
    )

    saved = service.save_role_document(
        "writer",
        draft=RoleDocumentDraft(
            role_id="writer",
            name="Writer",
            description="Drafts user-facing content.",
            version="1.0.0",
            tools=("dispatch_task",),
            mcp_servers=(),
            skills=(),
            model_profile="default",
            memory_profile=default_memory_profile(),
            system_prompt="Write clearly.",
        ),
    )

    assert saved.role_id == "writer"
    reloaded_dirty_role = captured_registry[-1].get("dirty")
    assert reloaded_dirty_role.tools == ()
    assert reloaded_dirty_role.mcp_servers == ()
    assert reloaded_dirty_role.skills == ()


def test_validate_all_roles_rejects_unknown_capabilities_in_persisted_roles(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    _write_role(
        roles_dir / "dirty.md",
        role_id="dirty",
        name="Dirty",
        description="Contains stale capability references.",
        version="1.0.0",
        tools=("missing_tool",),
        mcp_servers=("missing_mcp",),
        skills=("missing_skill",),
        system_prompt="Continue despite stale capabilities.",
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
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
    )

    try:
        service.validate_all_roles()
    except ValueError as exc:
        assert "Unknown tools" in str(exc)
    else:
        raise AssertionError("Expected strict persisted role validation to fail")


def test_validate_role_document_rejects_ambiguous_plain_skill_name(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    app_skills_dir = tmp_path / "skills"
    builtin_skills_dir = tmp_path / "builtin_skills"
    (app_skills_dir / "time").mkdir(parents=True)
    (builtin_skills_dir / "time").mkdir(parents=True)
    (app_skills_dir / "time" / "SKILL.md").write_text(
        "---\nname: time\ndescription: app timezone helper\n---\nUse app.\n",
        encoding="utf-8",
    )
    (builtin_skills_dir / "time" / "SKILL.md").write_text(
        "---\nname: time\ndescription: builtin timezone helper\n---\nUse builtin.\n",
        encoding="utf-8",
    )
    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=_create_builtin_roles_dir(tmp_path),
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: SkillRegistry.from_skill_dirs(
            app_skills_dir=app_skills_dir,
            builtin_skills_dir=builtin_skills_dir,
        ),
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
    )

    with pytest.raises(ValueError, match="Ambiguous skills require canonical refs"):
        service.validate_role_document(
            RoleDocumentDraft(
                role_id="writer",
                name="Writer",
                description="Drafts user-facing content.",
                version="1.0.0",
                tools=("dispatch_task",),
                mcp_servers=(),
                skills=("time",),
                model_profile="default",
                memory_profile=default_memory_profile(),
                system_prompt="Write clearly.",
            )
        )


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
        get_external_agent_service=None,
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


def test_save_role_document_allows_reserved_role_prompt_updates(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    builtin_roles_dir = _create_builtin_roles_dir(tmp_path)
    _write_role(
        builtin_roles_dir / "MainAgent.md",
        role_id="MainAgent",
        name="Main Agent",
        description="Handles normal-mode runs directly.",
        version="1.0.0",
        tools=("dispatch_task",),
        system_prompt="Handle the task directly.",
    )
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=builtin_roles_dir,
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: SkillRegistry.from_skill_dirs(
            app_skills_dir=skills_dir
        ),
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
    )

    saved = service.save_role_document(
        "MainAgent",
        draft=RoleDocumentDraft(
            source_role_id="MainAgent",
            role_id="MainAgent",
            name="Main Agent",
            description="Handles normal-mode runs directly.",
            version="1.0.0",
            tools=("dispatch_task",),
            mcp_servers=(),
            skills=(),
            model_profile="default",
            memory_profile=default_memory_profile(),
            system_prompt="Handle the task directly and verify the outcome before finishing.",
        ),
    )

    assert saved.role_id == "MainAgent"
    assert (
        saved.system_prompt
        == "Handle the task directly and verify the outcome before finishing."
    )
    assert (roles_dir / "MainAgent.md").exists()


def test_delete_role_document_removes_dirty_app_role_and_reloads_registry(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    builtin_roles_dir = _create_builtin_roles_dir(tmp_path)
    _write_role(
        builtin_roles_dir / "MainAgent.md",
        role_id="MainAgent",
        name="Main Agent",
        description="Handles normal-mode runs directly.",
        version="1.0.0",
        tools=("dispatch_task",),
        system_prompt="Handle the run directly.",
    )
    _write_role(
        roles_dir / "writer.md",
        role_id="writer",
        name="Writer",
        description="Dirty target role.",
        version="1.0.0",
        tools=("missing_tool",),
        mcp_servers=("missing_mcp",),
        skills=("missing_skill",),
        system_prompt="Delete this dirty role.",
    )
    _write_role(
        roles_dir / "dirty.md",
        role_id="dirty",
        name="Dirty",
        description="Dirty survivor role.",
        version="1.0.0",
        tools=("missing_tool",),
        mcp_servers=("missing_mcp",),
        skills=("missing_skill",),
        system_prompt="Keep this dirty role.",
    )
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    captured_registry: list[RoleRegistry] = []
    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=builtin_roles_dir,
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: SkillRegistry.from_skill_dirs(
            app_skills_dir=skills_dir
        ),
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: captured_registry.append(registry),
    )

    service.delete_role_document("writer")

    assert not (roles_dir / "writer.md").exists()
    with pytest.raises(KeyError):
        captured_registry[-1].get("writer")
    reloaded_dirty_role = captured_registry[-1].get("dirty")
    assert reloaded_dirty_role.tools == ()
    assert reloaded_dirty_role.mcp_servers == ()
    assert reloaded_dirty_role.skills == ()


def test_delete_role_document_rejects_builtin_role(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    builtin_roles_dir = _create_builtin_roles_dir(tmp_path)
    _write_role(
        builtin_roles_dir / "Crafter.md",
        role_id="Crafter",
        name="Crafter",
        description="Builtin crafter role.",
        version="1.0.0",
        tools=("dispatch_task",),
        system_prompt="Craft changes.",
    )
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=builtin_roles_dir,
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: SkillRegistry.from_skill_dirs(
            app_skills_dir=skills_dir
        ),
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
    )

    with pytest.raises(ValueError, match="Role cannot be deleted: Crafter"):
        service.delete_role_document("Crafter")


def test_delete_role_document_rejects_builtin_override(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    builtin_roles_dir = _create_builtin_roles_dir(tmp_path)
    _write_role(
        builtin_roles_dir / "Crafter.md",
        role_id="Crafter",
        name="Crafter",
        description="Builtin crafter role.",
        version="1.0.0",
        tools=("dispatch_task",),
        system_prompt="Craft changes.",
    )
    _write_role(
        roles_dir / "Crafter.md",
        role_id="Crafter",
        name="Crafter",
        description="App override for builtin crafter.",
        version="1.1.0",
        tools=("dispatch_task",),
        system_prompt="Craft app changes.",
    )
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=builtin_roles_dir,
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: SkillRegistry.from_skill_dirs(
            app_skills_dir=skills_dir
        ),
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
    )

    with pytest.raises(ValueError, match="Role cannot be deleted: Crafter"):
        service.delete_role_document("Crafter")


def _write_role(
    path: Path,
    *,
    role_id: str,
    name: str,
    description: str,
    version: str,
    tools: tuple[str, ...],
    mcp_servers: tuple[str, ...] = (),
    skills: tuple[str, ...] = (),
    system_prompt: str,
) -> None:
    lines = [
        "---\n",
        f"role_id: {role_id}\n",
        f"name: {name}\n",
        f"description: {description}\n",
        "model_profile: default\n",
        f"version: {version}\n",
        "tools:\n",
        *[f"  - {tool}\n" for tool in tools],
    ]
    if mcp_servers:
        lines.extend(
            [
                "mcp_servers:\n",
                *[f"  - {server}\n" for server in mcp_servers],
            ]
        )
    if skills:
        lines.extend(
            [
                "skills:\n",
                *[f"  - {skill}\n" for skill in skills],
            ]
        )
    lines.extend(["---\n\n", system_prompt, "\n"])
    path.write_text("".join(lines), encoding="utf-8")


def _create_builtin_roles_dir(tmp_path: Path) -> Path:
    builtin_roles_dir = tmp_path / "builtin_roles"
    builtin_roles_dir.mkdir()
    return builtin_roles_dir
