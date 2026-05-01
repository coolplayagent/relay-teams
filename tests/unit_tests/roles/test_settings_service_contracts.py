# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.roles import RoleDocumentDraft, RoleRegistry, default_memory_profile
from relay_teams.roles.settings_service import RoleSettingsService
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.tools.registry.defaults import build_default_registry


def test_save_role_document_filters_unknown_contract_capabilities_from_other_roles(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    _write_dirty_contract_role(roles_dir / "dirty.md")
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
    assert reloaded_dirty_role.contract.invariants[0].tools == ("office_read_markdown",)
    assert reloaded_dirty_role.contract.invariants[1].mcp_servers == ("filesystem",)
    assert reloaded_dirty_role.contract.invariants[2].skills == ("time",)


def _write_dirty_contract_role(path: Path) -> None:
    path.write_text(
        """---
role_id: dirty
name: Dirty
description: Contains stale contract capability references.
model_profile: default
version: 1.0.0
tools:
  - office_read_markdown
  - missing_tool
mcp_servers:
  - filesystem
  - missing_mcp
skills:
  - time
  - missing_skill
contract:
  invariants:
    - invariant: must_have_tools
      tools:
        - office_read_markdown
        - missing_tool
    - invariant: must_have_mcp_servers
      mcp_servers:
        - filesystem
        - missing_mcp
    - invariant: must_have_skills
      skills:
        - time
        - missing_skill
---

Continue despite stale contract capabilities.
""",
        encoding="utf-8",
    )


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
