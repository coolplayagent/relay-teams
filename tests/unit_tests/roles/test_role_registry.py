# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.hooks.hook_models import HookEventName
from relay_teams.hooks.hook_models import HooksConfig
from relay_teams.builtin import get_builtin_roles_dir
from relay_teams.roles.role_models import RoleDefinition, RoleMode
from relay_teams.roles.role_registry import (
    RoleLoader,
    RoleRegistry,
    _parse_frontmatter_hooks,
)


def test_role_loader_loads_markdown_role() -> None:
    registry = RoleLoader().load_all(get_builtin_roles_dir())
    roles = registry.list_roles()
    assert len(roles) >= 1
    assert roles[0].role_id


def test_role_loader_rejects_depends_on_in_role_front_matter(tmp_path: Path) -> None:
    role_file = tmp_path / "bad_role.md"
    role_file.write_text(
        "---\n"
        "role_id: bad_role\n"
        "name: Bad Role\n"
        "description: Broken role\n"
        "version: 1.0.0\n"
        "tools: []\n"
        "depends_on: []\n"
        "---\n"
        "System prompt.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="depends_on is not allowed"):
        RoleLoader().load_one(role_file)


def test_role_loader_adds_office_markdown_tool_to_non_coordinator_roles(
    tmp_path: Path,
) -> None:
    role_file = tmp_path / "writer.md"
    role_file.write_text(
        "---\n"
        "role_id: writer\n"
        "name: Writer\n"
        "description: Drafts content\n"
        "version: 1.0.0\n"
        "tools:\n"
        "  - orch_dispatch_task\n"
        "---\n"
        "Write clearly.\n",
        encoding="utf-8",
    )

    role = RoleLoader().load_one(role_file)

    assert role.tools == (
        "orch_dispatch_task",
        "office_read_markdown",
        "todo_write",
        "todo_read",
    )


def test_role_loader_keeps_coordinator_tools_unchanged(tmp_path: Path) -> None:
    role_file = tmp_path / "coordinator.md"
    role_file.write_text(
        "---\n"
        "role_id: dispatch_lead\n"
        "name: Dispatch Lead\n"
        "description: Coordinates delegated work\n"
        "version: 1.0.0\n"
        "tools:\n"
        "  - orch_create_tasks\n"
        "  - orch_update_task\n"
        "  - orch_dispatch_task\n"
        "---\n"
        "Coordinate tasks.\n",
        encoding="utf-8",
    )

    role = RoleLoader().load_one(role_file)

    assert role.tools == ("orch_create_tasks", "orch_update_task", "orch_dispatch_task")


def test_role_loader_tolerates_invalid_frontmatter_hooks(tmp_path: Path) -> None:
    role_file = tmp_path / "writer.md"
    role_file.write_text(
        "---\n"
        "role_id: writer\n"
        "name: Writer\n"
        "description: Drafts content\n"
        "version: 1.0.0\n"
        "tools: []\n"
        "hooks:\n"
        "  Stop: bad\n"
        "---\n"
        "Write clearly.\n",
        encoding="utf-8",
    )

    role = RoleLoader().load_one(role_file)

    assert role.role_id == "writer"
    assert role.hooks.hooks == {}


def test_role_loader_preserves_valid_frontmatter_hooks_when_one_group_is_empty(
    tmp_path: Path,
) -> None:
    role_file = tmp_path / "writer.md"
    role_file.write_text(
        "---\n"
        "role_id: writer\n"
        "name: Writer\n"
        "description: Drafts content\n"
        "version: 1.0.0\n"
        "tools: []\n"
        "hooks:\n"
        "  PreToolUse:\n"
        "    - hooks: []\n"
        "    - matcher: Read\n"
        "      hooks:\n"
        "        - type: command\n"
        "          command: echo ok\n"
        "---\n"
        "Write clearly.\n",
        encoding="utf-8",
    )

    role = RoleLoader().load_one(role_file)

    groups = role.hooks.hooks[HookEventName.PRE_TOOL_USE]
    assert len(groups) == 2
    assert groups[0].hooks == ()
    assert groups[1].matcher == "Read"
    assert groups[1].hooks[0].command == "echo ok"


def test_role_loader_normalizes_legacy_frontmatter_hook_fields(
    tmp_path: Path,
) -> None:
    role_file = tmp_path / "writer.md"
    role_file.write_text(
        "---\n"
        "role_id: writer\n"
        "name: Writer\n"
        "description: Drafts content\n"
        "version: 1.0.0\n"
        "tools: []\n"
        "hooks:\n"
        "  PreToolUse:\n"
        "    - matcher: '*'\n"
        "      if_condition: Bash(git *)\n"
        "      tool_names:\n"
        "        - Read\n"
        "        - Write\n"
        "      hooks:\n"
        "        - type: command\n"
        "          command: echo ok\n"
        "---\n"
        "Write clearly.\n",
        encoding="utf-8",
    )

    role = RoleLoader().load_one(role_file)

    groups = role.hooks.hooks[HookEventName.PRE_TOOL_USE]
    assert [group.matcher for group in groups] == ["Read", "Write"]
    assert all(group.hooks[0].if_rule == "Bash(git *)" for group in groups)


def test_role_loader_ignores_unsupported_frontmatter_matcher_for_stop_hooks(
    tmp_path: Path,
) -> None:
    role_file = tmp_path / "writer.md"
    role_file.write_text(
        "---\n"
        "role_id: writer\n"
        "name: Writer\n"
        "description: Drafts content\n"
        "version: 1.0.0\n"
        "tools: []\n"
        "hooks:\n"
        "  Stop:\n"
        "    - matcher: manual\n"
        "      hooks:\n"
        "        - type: command\n"
        "          command: echo stop\n"
        "---\n"
        "Write clearly.\n",
        encoding="utf-8",
    )

    role = RoleLoader().load_one(role_file)

    assert role.hooks.hooks == {}


def test_role_registry_resolves_dynamic_coordinator_role() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates delegated work.",
            version="1.0.0",
            tools=(
                "orch_create_tasks",
                "orch_update_task",
                "orch_dispatch_task",
            ),
            system_prompt="Coordinate tasks.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="Crafter",
            name="Crafter",
            description="Implements requested changes.",
            version="1.0.0",
            tools=("read",),
            system_prompt="Implement tasks.",
        )
    )

    assert registry.get_coordinator_role_id() == "Coordinator"
    assert registry.is_coordinator_role("Coordinator") is True
    assert registry.is_coordinator_role("Crafter") is False


def test_role_registry_does_not_treat_legacy_coordinator_id_as_system_role() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="coordinator_agent",
            name="Coordinator Agent",
            description="Legacy coordinator shape.",
            version="1.0.0",
            tools=("read",),
            system_prompt="Legacy prompt.",
        )
    )

    with pytest.raises(KeyError, match="Coordinator role could not be resolved"):
        _ = registry.get_coordinator()


def test_role_registry_lists_normal_mode_roles_with_main_agent_first() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates delegated work.",
            version="1.0.0",
            tools=("orch_create_tasks", "orch_update_task", "orch_dispatch_task"),
            system_prompt="Coordinate tasks.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="Handles direct runs.",
            version="1.0.0",
            tools=("read",),
            system_prompt="Handle tasks.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="Crafter",
            name="Crafter",
            description="Implements requested changes.",
            version="1.0.0",
            tools=("read",),
            mode=RoleMode.SUBAGENT,
            system_prompt="Implement tasks.",
        )
    )

    roles = registry.list_normal_mode_roles()

    assert [role.role_id for role in roles] == ["MainAgent"]


def test_role_registry_lists_subagent_roles_only_for_subagent_modes() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates delegated work.",
            version="1.0.0",
            tools=("orch_create_tasks", "orch_update_task", "orch_dispatch_task"),
            system_prompt="Coordinate tasks.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="Handles direct runs.",
            version="1.0.0",
            tools=("read",),
            system_prompt="Handle tasks.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="Crafter",
            name="Crafter",
            description="Implements requested changes.",
            version="1.0.0",
            tools=("read",),
            mode=RoleMode.SUBAGENT,
            system_prompt="Implement tasks.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="Writer",
            name="Writer",
            description="Can do both.",
            version="1.0.0",
            tools=("read",),
            mode=RoleMode.ALL,
            system_prompt="Write tasks.",
        )
    )

    roles = registry.list_subagent_roles()

    assert [role.role_id for role in roles] == ["Crafter", "Writer"]


def test_role_registry_rejects_coordinator_in_normal_mode() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates delegated work.",
            version="1.0.0",
            tools=("orch_create_tasks", "orch_update_task", "orch_dispatch_task"),
            system_prompt="Coordinate tasks.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="Handles direct runs.",
            version="1.0.0",
            tools=("read",),
            system_prompt="Handle tasks.",
        )
    )

    with pytest.raises(ValueError, match="Coordinator role cannot be used"):
        _ = registry.resolve_normal_mode_role_id("Coordinator")


def test_role_registry_rejects_subagent_only_role_in_normal_mode() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates delegated work.",
            version="1.0.0",
            tools=("orch_create_tasks", "orch_update_task", "orch_dispatch_task"),
            system_prompt="Coordinate tasks.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="Handles direct runs.",
            version="1.0.0",
            tools=("read",),
            system_prompt="Handle tasks.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="Crafter",
            name="Crafter",
            description="Implements requested changes.",
            version="1.0.0",
            tools=("read",),
            mode=RoleMode.SUBAGENT,
            system_prompt="Implement tasks.",
        )
    )

    with pytest.raises(ValueError, match="Role cannot be used in normal mode"):
        _ = registry.resolve_normal_mode_role_id("Crafter")


def test_role_registry_resolves_subagent_only_role_for_subagent_use() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates delegated work.",
            version="1.0.0",
            tools=("orch_create_tasks", "orch_update_task", "orch_dispatch_task"),
            system_prompt="Coordinate tasks.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="Handles direct runs.",
            version="1.0.0",
            tools=("read",),
            system_prompt="Handle tasks.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="Crafter",
            name="Crafter",
            description="Implements requested changes.",
            version="1.0.0",
            tools=("read",),
            mode=RoleMode.SUBAGENT,
            system_prompt="Implement tasks.",
        )
    )

    assert registry.resolve_subagent_role_id("Crafter") == "Crafter"


def test_parse_frontmatter_hooks_tolerates_parser_failure() -> None:
    original = _parse_frontmatter_hooks.__globals__["parse_tolerant_hooks_payload"]

    def _raise(_: object) -> None:
        raise RuntimeError("boom")

    _parse_frontmatter_hooks.__globals__["parse_tolerant_hooks_payload"] = _raise
    try:
        hooks = _parse_frontmatter_hooks(
            {"hooks": {"Stop": []}},
            source_name="writer.md",
        )
    finally:
        _parse_frontmatter_hooks.__globals__["parse_tolerant_hooks_payload"] = original

    assert hooks == HooksConfig()
