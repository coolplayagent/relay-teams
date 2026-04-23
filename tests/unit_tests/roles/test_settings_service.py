# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.computer import ExecutionSurface
from relay_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.roles import (
    RoleDocumentDraft,
    RoleConfigSource,
    RoleMode,
    RoleRegistry,
    default_memory_profile,
)
from relay_teams.roles.settings_service import RoleSettingsService
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.tools.registry.defaults import build_default_registry


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
        tools=("orch_dispatch_task",),
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
            tools=("orch_dispatch_task",),
            mcp_servers=(),
            skills=(),
            model_profile="default",
            execution_surface=ExecutionSurface.HYBRID,
            mode=RoleMode.SUBAGENT,
            memory_profile=default_memory_profile(),
            system_prompt="Write with more detail.",
        ),
    )

    assert saved.role_id == "writer_v2"
    assert saved.file_name == "writer_v2.md"
    assert saved.execution_surface == ExecutionSurface.HYBRID
    assert saved.mode == RoleMode.SUBAGENT
    assert not (roles_dir / "writer.md").exists()
    assert (roles_dir / "writer_v2.md").exists()
    assert "execution_surface: hybrid" in saved.content
    assert "mode: subagent" in saved.content
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
        tools=("orch_dispatch_task",),
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
    assert record.mode == RoleMode.PRIMARY
    assert "role_id: reviewer" in record.content
    assert "Review carefully." in record.content


def test_list_role_documents_preserves_role_mode(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    _write_role(
        roles_dir / "reviewer.md",
        role_id="reviewer",
        name="Reviewer",
        description="Reviews delivered work.",
        version="1.1.0",
        tools=("orch_dispatch_task",),
        mode=RoleMode.SUBAGENT,
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

    summaries = service.list_role_documents()

    assert len(summaries) == 1
    assert summaries[0].role_id == "reviewer"
    assert summaries[0].mode == RoleMode.SUBAGENT


def test_get_role_document_canonicalizes_unique_skill_names(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    _write_role(
        roles_dir / "reviewer.md",
        role_id="reviewer",
        name="Reviewer",
        description="Reviews delivered work.",
        version="1.1.0",
        tools=("orch_dispatch_task",),
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

    assert record.skills == ("time",)


def test_get_role_document_preserves_unknown_tool_names_without_aliases(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    _write_role(
        roles_dir / "legacy.md",
        role_id="legacy",
        name="Legacy",
        description="Uses historical tool names.",
        version="1.0.0",
        tools=("deprecated_writer", "shell", "missing_tool"),
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

    assert record.tools == (
        "deprecated_writer",
        "shell",
        "missing_tool",
        "office_read_markdown",
        "todo_write",
        "todo_read",
    )


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
        tools=("orch_dispatch_task",),
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
        tools=("orch_dispatch_task",),
        system_prompt="Craft changes.",
    )
    _write_role(
        roles_dir / "Crafter.md",
        role_id="Crafter",
        name="Crafter",
        description="App override for builtin crafter.",
        version="1.1.0",
        tools=("orch_dispatch_task",),
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
            tools=("orch_dispatch_task",),
            mcp_servers=(),
            skills=(),
            model_profile="default",
            memory_profile=default_memory_profile(),
            system_prompt="Write clearly.",
        ),
    )

    assert saved.role_id == "writer"
    reloaded_dirty_role = captured_registry[-1].get("dirty")
    assert reloaded_dirty_role.tools == (
        "office_read_markdown",
        "todo_write",
        "todo_read",
    )
    assert reloaded_dirty_role.mcp_servers == ()
    assert reloaded_dirty_role.skills == ()


def test_save_role_document_preserves_persisted_wildcards_when_filtering_dirty_roles(
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
        mcp_servers=("*", "missing_mcp"),
        skills=("*", "missing_skill"),
        system_prompt="Continue despite stale capabilities.",
    )
    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC.",
    )
    mcp_registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "npx"}}},
                server_config={"command": "npx"},
                source=McpConfigScope.APP,
            ),
        )
    )
    captured_registry: list[RoleRegistry] = []
    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=_create_builtin_roles_dir(tmp_path),
        get_tool_registry=build_default_registry,
        get_mcp_registry=lambda: mcp_registry,
        get_skill_registry=lambda: SkillRegistry.from_skill_dirs(
            app_skills_dir=skills_dir
        ),
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: captured_registry.append(registry),
    )

    service.save_role_document(
        "writer",
        draft=RoleDocumentDraft(
            role_id="writer",
            name="Writer",
            description="Drafts user-facing content.",
            version="1.0.0",
            tools=("orch_dispatch_task",),
            mcp_servers=(),
            skills=(),
            model_profile="default",
            memory_profile=default_memory_profile(),
            system_prompt="Write clearly.",
        ),
    )

    reloaded_dirty_role = captured_registry[-1].get("dirty")
    assert reloaded_dirty_role.tools == (
        "office_read_markdown",
        "todo_write",
        "todo_read",
    )
    assert reloaded_dirty_role.mcp_servers == ("*",)
    assert reloaded_dirty_role.skills == ("*",)


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


def test_validate_role_document_uses_user_override_for_duplicate_skill_name(
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

    result = service.validate_role_document(
        RoleDocumentDraft(
            role_id="writer",
            name="Writer",
            description="Drafts user-facing content.",
            version="1.0.0",
            tools=("orch_dispatch_task",),
            mcp_servers=(),
            skills=("time",),
            model_profile="default",
            memory_profile=default_memory_profile(),
            system_prompt="Write clearly.",
        )
    )

    assert result.valid is True
    assert result.role.skills == ("time",)


def test_validate_role_document_reloads_skills_once_for_unknown_skill(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    app_skills_dir = tmp_path / "skills"
    app_skills_dir.mkdir()
    builtin_skills_dir = tmp_path / "builtin_skills"
    _write_skill(
        builtin_skills_dir / "skill-installer",
        name="skill-installer",
        description="Install skills.",
        instructions="Install a skill.",
    )
    current_registry = SkillRegistry.from_skill_dirs(app_skills_dir=app_skills_dir)
    reload_calls: list[int] = []

    def reload_skill_registry() -> SkillRegistry:
        reload_calls.append(1)
        reloaded = SkillRegistry.from_skill_dirs(
            app_skills_dir=app_skills_dir,
            builtin_skills_dir=builtin_skills_dir,
        )
        nonlocal current_registry
        current_registry = reloaded
        return reloaded

    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=_create_builtin_roles_dir(tmp_path),
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: current_registry,
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
        reload_skill_registry=reload_skill_registry,
    )

    result = service.validate_role_document(
        RoleDocumentDraft(
            role_id="writer",
            name="Writer",
            description="Drafts user-facing content.",
            version="1.0.0",
            tools=("orch_dispatch_task",),
            mcp_servers=(),
            skills=("skill-installer",),
            model_profile="default",
            memory_profile=default_memory_profile(),
            system_prompt="Write clearly.",
        )
    )

    assert result.valid is True
    assert result.role.skills == ("skill-installer",)
    assert len(reload_calls) == 1


def test_save_role_document_reloads_skills_once_for_unknown_skill(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    app_skills_dir = tmp_path / "skills"
    app_skills_dir.mkdir()
    builtin_skills_dir = tmp_path / "builtin_skills"
    _write_skill(
        builtin_skills_dir / "skill-installer",
        name="skill-installer",
        description="Install skills.",
        instructions="Install a skill.",
    )
    current_registry = SkillRegistry.from_skill_dirs(app_skills_dir=app_skills_dir)
    reload_calls: list[int] = []

    def reload_skill_registry() -> SkillRegistry:
        reload_calls.append(1)
        reloaded = SkillRegistry.from_skill_dirs(
            app_skills_dir=app_skills_dir,
            builtin_skills_dir=builtin_skills_dir,
        )
        nonlocal current_registry
        current_registry = reloaded
        return reloaded

    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=_create_builtin_roles_dir(tmp_path),
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: current_registry,
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
        reload_skill_registry=reload_skill_registry,
    )

    saved = service.save_role_document(
        "writer",
        RoleDocumentDraft(
            role_id="writer",
            name="Writer",
            description="Drafts user-facing content.",
            version="1.0.0",
            tools=("orch_dispatch_task",),
            mcp_servers=(),
            skills=("skill-installer",),
            model_profile="default",
            memory_profile=default_memory_profile(),
            system_prompt="Write clearly.",
        ),
    )

    assert saved.skills == ("skill-installer",)
    assert len(reload_calls) == 1


def test_validate_role_document_reports_final_error_when_skill_reload_fails(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    app_skills_dir = tmp_path / "skills"
    app_skills_dir.mkdir()
    current_registry = SkillRegistry.from_skill_dirs(app_skills_dir=app_skills_dir)
    reload_calls: list[int] = []

    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=_create_builtin_roles_dir(tmp_path),
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: current_registry,
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
        reload_skill_registry=lambda: (
            reload_calls.append(1),
            current_registry,
        )[1],
    )

    with pytest.raises(
        ValueError,
        match="Unknown skills: \\['skill-installer'\\]",
    ):
        service.validate_role_document(
            RoleDocumentDraft(
                role_id="writer",
                name="Writer",
                description="Drafts user-facing content.",
                version="1.0.0",
                tools=("orch_dispatch_task",),
                mcp_servers=(),
                skills=("skill-installer",),
                model_profile="default",
                memory_profile=default_memory_profile(),
                system_prompt="Write clearly.",
            )
        )

    assert len(reload_calls) == 1


def test_validate_role_document_reloads_and_reports_unknown_skill_when_still_missing(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    app_skills_dir = tmp_path / "skills"
    app_skills_dir.mkdir()
    current_registry = SkillRegistry.from_skill_dirs(app_skills_dir=app_skills_dir)
    reload_calls: list[int] = []

    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=_create_builtin_roles_dir(tmp_path),
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: current_registry,
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
        reload_skill_registry=lambda: (
            reload_calls.append(1),
            current_registry,
        )[1],
    )

    with pytest.raises(ValueError, match="Unknown skills: \\['missing_skill'\\]"):
        service.validate_role_document(
            RoleDocumentDraft(
                role_id="writer",
                name="Writer",
                description="Drafts user-facing content.",
                version="1.0.0",
                tools=("orch_dispatch_task",),
                mcp_servers=(),
                skills=("missing_skill",),
                model_profile="default",
                memory_profile=default_memory_profile(),
                system_prompt="Write clearly.",
            )
        )

    assert reload_calls == [1]


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
            tools=("orch_dispatch_task",),
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


def test_save_role_document_preserves_skill_and_mcp_wildcards(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC.",
    )
    mcp_registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "npx"}}},
                server_config={"command": "npx"},
                source=McpConfigScope.APP,
            ),
        )
    )
    captured_registry: list[RoleRegistry] = []
    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=_create_builtin_roles_dir(tmp_path),
        get_tool_registry=build_default_registry,
        get_mcp_registry=lambda: mcp_registry,
        get_skill_registry=lambda: SkillRegistry.from_skill_dirs(
            app_skills_dir=skills_dir
        ),
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: captured_registry.append(registry),
    )

    saved = service.save_role_document(
        "wildcard_role",
        draft=RoleDocumentDraft(
            role_id="wildcard_role",
            name="Wildcard Role",
            description="Uses wildcard capabilities.",
            version="1.0.0",
            tools=("orch_dispatch_task",),
            mcp_servers=("*",),
            skills=("*",),
            model_profile="default",
            memory_profile=default_memory_profile(),
            system_prompt="Use all configured capabilities.",
        ),
    )

    assert saved.mcp_servers == ("*",)
    assert saved.skills == ("*",)
    assert captured_registry[-1].get("wildcard_role").mcp_servers == ("*",)
    assert captured_registry[-1].get("wildcard_role").skills == ("*",)
    assert "'*'" in saved.content


def test_save_role_document_collapses_mixed_wildcard_capability_selections(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC.",
    )
    mcp_registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "npx"}}},
                server_config={"command": "npx"},
                source=McpConfigScope.APP,
            ),
        )
    )
    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=_create_builtin_roles_dir(tmp_path),
        get_tool_registry=build_default_registry,
        get_mcp_registry=lambda: mcp_registry,
        get_skill_registry=lambda: SkillRegistry.from_skill_dirs(
            app_skills_dir=skills_dir
        ),
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
    )

    saved = service.save_role_document(
        "wildcard_role",
        draft=RoleDocumentDraft(
            role_id=" wildcard_role ",
            name=" Wildcard Role ",
            description=" Uses wildcard capabilities. ",
            version=" 1.0.0 ",
            tools=(" orch_dispatch_task ",),
            mcp_servers=(" filesystem ", " * "),
            skills=(" app:time ", "*"),
            model_profile=" default ",
            memory_profile=default_memory_profile(),
            system_prompt=" Use all configured capabilities. ",
        ),
    )

    assert saved.mcp_servers == ("*",)
    assert saved.skills == ("*",)
    assert "- '*'" in saved.content
    assert "filesystem" not in saved.content
    assert "app:time" not in saved.content


def test_validate_role_document_rejects_partial_mcp_wildcard(
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

    with pytest.raises(ValueError, match="Unknown MCP servers: \\['mcp-\\*'\\]"):
        service.validate_role_document(
            RoleDocumentDraft(
                role_id="wildcard_role",
                name="Wildcard Role",
                description="Uses wildcard capabilities.",
                version="1.0.0",
                tools=("orch_dispatch_task",),
                mcp_servers=("mcp-*",),
                skills=(),
                model_profile="default",
                memory_profile=default_memory_profile(),
                system_prompt="Use all configured capabilities.",
            )
        )


def test_validate_role_document_rejects_invalid_mcp_ref_mixed_with_wildcard(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    mcp_registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "npx"}}},
                server_config={"command": "npx"},
                source=McpConfigScope.APP,
            ),
        )
    )
    service = RoleSettingsService(
        roles_dir=roles_dir,
        builtin_roles_dir=_create_builtin_roles_dir(tmp_path),
        get_tool_registry=build_default_registry,
        get_mcp_registry=lambda: mcp_registry,
        get_skill_registry=lambda: SkillRegistry.from_skill_dirs(
            app_skills_dir=skills_dir
        ),
        get_external_agent_service=None,
        on_roles_reloaded=lambda registry: None,
    )

    with pytest.raises(ValueError, match="Unknown MCP servers: \\['mcp-\\*'\\]"):
        service.validate_role_document(
            RoleDocumentDraft(
                role_id="wildcard_role",
                name="Wildcard Role",
                description="Uses wildcard capabilities.",
                version="1.0.0",
                tools=("orch_dispatch_task",),
                mcp_servers=("filesystem", "mcp-*", "*"),
                skills=(),
                model_profile="default",
                memory_profile=default_memory_profile(),
                system_prompt="Use all configured capabilities.",
            )
        )


def test_validate_role_document_rejects_partial_skill_wildcard(
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

    with pytest.raises(ValueError, match="Unknown skills: \\['builtin:\\*'\\]"):
        service.validate_role_document(
            RoleDocumentDraft(
                role_id="wildcard_role",
                name="Wildcard Role",
                description="Uses wildcard capabilities.",
                version="1.0.0",
                tools=("orch_dispatch_task",),
                mcp_servers=(),
                skills=("builtin:*",),
                model_profile="default",
                memory_profile=default_memory_profile(),
                system_prompt="Use all configured capabilities.",
            )
        )


def test_validate_role_document_rejects_invalid_skill_ref_mixed_with_wildcard(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir / "time",
        name="time",
        description="timezone helper",
        instructions="Use UTC.",
    )
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

    with pytest.raises(ValueError, match="Unknown skills: \\['missing'\\]"):
        service.validate_role_document(
            RoleDocumentDraft(
                role_id="wildcard_role",
                name="Wildcard Role",
                description="Uses wildcard capabilities.",
                version="1.0.0",
                tools=("orch_dispatch_task",),
                mcp_servers=(),
                skills=("app:time", "missing", "*"),
                model_profile="default",
                memory_profile=default_memory_profile(),
                system_prompt="Use all configured capabilities.",
            )
        )


def test_save_role_document_strips_office_tool_from_coordinator_like_role(
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

    saved = service.save_role_document(
        "dispatch_lead",
        draft=RoleDocumentDraft(
            role_id="dispatch_lead",
            name="Dispatch Lead",
            description="Coordinates delegated work.",
            version="1.0.0",
            tools=(
                "orch_create_tasks",
                "orch_update_task",
                "orch_dispatch_task",
                "office_read_markdown",
            ),
            mcp_servers=(),
            skills=(),
            model_profile="default",
            memory_profile=default_memory_profile(),
            system_prompt="Coordinate delegated work.",
        ),
    )

    assert saved.tools == (
        "orch_create_tasks",
        "orch_update_task",
        "orch_dispatch_task",
    )


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
        tools=("orch_dispatch_task",),
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
            tools=("orch_dispatch_task",),
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
        tools=("orch_dispatch_task",),
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
    assert reloaded_dirty_role.tools == (
        "office_read_markdown",
        "todo_write",
        "todo_read",
    )
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
        tools=("orch_dispatch_task",),
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
        tools=("orch_dispatch_task",),
        system_prompt="Craft changes.",
    )
    _write_role(
        roles_dir / "Crafter.md",
        role_id="Crafter",
        name="Crafter",
        description="App override for builtin crafter.",
        version="1.1.0",
        tools=("orch_dispatch_task",),
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
    mode: RoleMode = RoleMode.PRIMARY,
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
        f"mode: {mode.value}\n",
        "tools:\n",
        *[f"  - {_format_yaml_list_item(tool)}\n" for tool in tools],
    ]
    if mcp_servers:
        lines.extend(
            [
                "mcp_servers:\n",
                *[f"  - {_format_yaml_list_item(server)}\n" for server in mcp_servers],
            ]
        )
    if skills:
        lines.extend(
            [
                "skills:\n",
                *[f"  - {_format_yaml_list_item(skill)}\n" for skill in skills],
            ]
        )
    lines.extend(["---\n\n", system_prompt, "\n"])
    path.write_text("".join(lines), encoding="utf-8")


def _format_yaml_list_item(value: str) -> str:
    if value == "*":
        return "'*'"
    return value


def _create_builtin_roles_dir(tmp_path: Path) -> Path:
    builtin_roles_dir = tmp_path / "builtin_roles"
    builtin_roles_dir.mkdir()
    return builtin_roles_dir


def _write_skill(
    directory: Path,
    *,
    name: str,
    description: str,
    instructions: str,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{instructions}\n",
        encoding="utf-8",
    )
